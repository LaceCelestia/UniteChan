from __future__ import annotations

from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.lobby_store import LobbyStore
from unitechan.core.split_mode import SplitMode
from unitechan.core.split_service import (
    SplitService,
    Player,
    ROLE_CODE,
    SplitResult,
)
from unitechan.core.config_store import get_store
from unitechan.core.stats_store import get_stats_store


# /split test 用のデモプレイヤー（名前・ランク）
_DEMO_PLAYERS = [
    ("プレイヤー1", "レジェンド"),
    ("プレイヤー2", "マスター"),
    ("プレイヤー3", "エキスパート"),
    ("プレイヤー4", "エリート"),
    ("プレイヤー5", "ハイパー"),
    ("プレイヤー6", "スーパー"),
    ("プレイヤー7", "ビギナー"),
    ("プレイヤー8", "マスター"),
    ("プレイヤー9", "エキスパート"),
    ("プレイヤー10", "ハイパー"),
]

_REACTION_EMOJIS = ['🇦', '🇧', '🎙️', '🔄']

NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
TEAM_LABELS = ["🟦 Team A", "🟥 Team B"]


class TeamSplit(commands.Cog):
    """ユナイトのチーム分け"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.lobby_store = LobbyStore()
        self.service = SplitService(self.lobby_store)
        # メッセージID -> guild_id。リアクション操作を待っているチーム分けメッセージを管理
        self._pending_votes: dict[int, int] = {}

    # /split グループ
    split = app_commands.Group(name="split", description="ユナイトチーム分けコマンド")

    # -------------------------------------------------- 名前解決 --

    async def _resolve_name(self, interaction: discord.Interaction, uid: int) -> str:
        guild = interaction.guild
        if guild is not None:
            alias = self.lobby_store.get_alias(guild.id, uid)
            if alias:
                return alias
            m = guild.get_member(uid)
            if m:
                return m.display_name
            # キャッシュにいない場合はAPIから取得
            try:
                m = await guild.fetch_member(uid)
                return m.display_name
            except Exception:
                pass

        try:
            u = await interaction.client.fetch_user(uid)
            return u.name
        except Exception:
            return f"ID:{uid}"

    def _resolve_name_guild(self, guild: discord.Guild, uid: int) -> str:
        alias = self.lobby_store.get_alias(guild.id, uid)
        if alias:
            return alias
        m = guild.get_member(uid)
        return m.display_name if m else f"ID:{uid}"

    # -------------------------------------------------- モード表示 --

    def _mode_footer(self, mode: SplitMode, cfg) -> str:
        if mode.use_rank_balance:
            rank = "ランク"
        elif mode.use_stats_balance:
            rank = "通算戦績"
        elif mode.use_daily_stats_balance:
            rank = "当日戦績"
        else:
            rank = "OFF"
        pokemon = {0: "なし", 1: "個人", 2: "チーム"}.get(mode.pokemon_assign_mode, "?")
        role = {0: "なし", 1: "自動", 2: "設定値"}.get(mode.role_balance_mode, "?")
        dup = "許可" if mode.allow_cross_dup else "禁止"
        avoid = f"{cfg.avoid_count}回" if mode.use_avoid and cfg.avoid_count > 0 else "OFF"
        return (
            f"{mode.mode_raw}  バランス:{rank} / ポケモン:{pokemon} / "
            f"ロール:{role} / 重複:{dup} / 連続回避:{avoid}"
        )

    # -------------------------------------------------- Embed生成 --

    def _build_embed_from_result(
        self,
        result: SplitResult,
        mode: SplitMode,
        cfg,
        names: dict[int, str],
    ) -> discord.Embed:
        """SplitResult と名前辞書から Embed を生成する。"""
        embed = discord.Embed(title="🏅 チーム分け結果", color=0xF1C40F)

        for idx, team in enumerate(result.teams):
            label = TEAM_LABELS[idx]
            lines: List[str] = []
            for i, mem in enumerate(team.members):
                num = NUMS[i] if i < len(NUMS) else f"{i + 1}."
                name = names.get(mem.user_id, f"ID:{mem.user_id}")
                if mem.pokemon:
                    code = ROLE_CODE.get(mem.role, mem.role[:3].upper())
                    lines.append(f"{num} {name}  `{code}` {mem.pokemon}")
                else:
                    lines.append(f"{num} {name}")

            if team.team_pokemon:
                lines.append("")
                poke_parts = [
                    f"`{ROLE_CODE.get(r, r[:3].upper())}` {p}"
                    for r, p in team.team_pokemon
                ]
                lines.append("🎮 " + "  ".join(poke_parts))

            embed.add_field(
                name=f"{label} — {len(team.members)}人",
                value="\n".join(lines) if lines else "(なし)",
                inline=True,
            )

        if result.spectators:
            spec_names = [
                names.get(p.user_id, f"ID:{p.user_id}") for p in result.spectators
            ]
            embed.add_field(
                name=f"👀 観戦 — {len(result.spectators)}人",
                value="  ".join(spec_names),
                inline=False,
            )

        if cfg.banned_pokemon:
            embed.add_field(
                name="\u200b",
                value=f"🚫 **バン中:** {' / '.join(sorted(cfg.banned_pokemon))}",
                inline=False,
            )

        embed.set_footer(text=self._mode_footer(mode, cfg))
        return embed

    async def _build_embed_interaction(
        self,
        interaction: discord.Interaction,
        result: SplitResult,
        mode: SplitMode,
        cfg,
    ) -> discord.Embed:
        """interaction を使って名前解決し Embed を生成する（/split run 用）。"""
        names = {
            mem.user_id: await self._resolve_name(interaction, mem.user_id)
            for team in result.teams
            for mem in team.members
        }
        for p in result.spectators:
            names[p.user_id] = await self._resolve_name(interaction, p.user_id)
        return self._build_embed_from_result(result, mode, cfg, names)

    def _build_embed_guild(
        self,
        guild: discord.Guild,
        result: SplitResult,
        mode: SplitMode,
        cfg,
    ) -> discord.Embed:
        """guild を使って名前解決し Embed を生成する（リアクションハンドラ用）。"""
        names = {
            mem.user_id: self._resolve_name_guild(guild, mem.user_id)
            for team in result.teams
            for mem in team.members
        }
        for p in result.spectators:
            names[p.user_id] = self._resolve_name_guild(guild, p.user_id)
        return self._build_embed_from_result(result, mode, cfg, names)

    # -------------------------------------------------- 共通表示処理 --

    async def _display(
        self,
        interaction: discord.Interaction,
        result: SplitResult,
        mode: SplitMode,
        cfg,
        *,
        resolve_names: bool,
    ) -> discord.Message:
        """チーム分け結果をEmbedで表示し、送信したメッセージを返す。"""
        if resolve_names:
            embed = await self._build_embed_interaction(interaction, result, mode, cfg)
        else:
            names = {mem.user_id: mem.name for team in result.teams for mem in team.members}
            embed = self._build_embed_from_result(result, mode, cfg, names)

        if interaction.response.is_done():
            return await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)
            return await interaction.original_response()

    # -------------------------------------------------- /split run --

    @split.command(name="run", description="チーム分けを実行（コードは /config split で設定）")
    async def split_run(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で使ってね。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        cfg = get_store().get_split_config(guild_id)
        m = SplitMode(get_store().get_split_code(guild_id))

        try:
            result = self.service.split(guild_id, m)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        msg = await self._display(interaction, result, m, cfg, resolve_names=True)
        for emoji in _REACTION_EMOJIS:
            await msg.add_reaction(emoji)
        self._pending_votes[msg.id] = guild_id

    # -------------------------------------------------- /split test --

    @split.command(name="test", description="デモチーム分け（管理者専用）")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(mode="省略時は /config split の設定値を使用")
    async def split_test(
        self, interaction: discord.Interaction, mode: Optional[str] = None
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で使ってね。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        code = mode or get_store().get_split_code(guild_id)
        try:
            m = SplitMode.parse(code)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        cfg = get_store().get_split_config(guild_id)
        players: List[Player] = [
            Player(i + 1, name, rank)
            for i, (name, rank) in enumerate(_DEMO_PLAYERS)
        ]

        result = self.service.split_custom(guild_id, players, m, cfg, dry_run=True)
        await self._display(interaction, result, m, cfg, resolve_names=False)

    # -------------------------------------------------- リアクションハンドラ --

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """🇦/🇧/🎤/🔄 リアクションを処理する。"""
        if payload.message_id not in self._pending_votes:
            return
        if payload.user_id == self.bot.user.id:  # type: ignore[union-attr]
            return

        emoji = str(payload.emoji)
        guild_id = self._pending_votes[payload.message_id]
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        if emoji == '🇦' or emoji == '🇧':
            winning_idx = 0 if emoji == '🇦' else 1
            self._pending_votes.pop(payload.message_id)
            winners, losers = get_stats_store().record_result(guild_id, winning_idx)
            if not winners:
                return
            team_label = 'Team A' if winning_idx == 0 else 'Team B'
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(f'🏆 **{team_label}** の勝利を記録しました！')

        elif emoji == '🎙️':
            member = payload.member or guild.get_member(payload.user_id)
            if not self._is_admin_member(member):
                return
            await self._reaction_move(payload, guild, guild_id)

        elif emoji == '🔄':
            member = payload.member or guild.get_member(payload.user_id)
            if not self._is_admin_member(member):
                return
            await self._reaction_reroll(payload, guild, guild_id)

    def _is_admin_member(self, member: Optional[discord.Member]) -> bool:
        if member is None:
            return False
        p = member.guild_permissions
        return bool(p.administrator or p.manage_guild or p.manage_roles)

    async def _reaction_move(
        self,
        payload: discord.RawReactionActionEvent,
        guild: discord.Guild,
        guild_id: int,
    ) -> None:
        """🎤 リアクション: チーム分け結果に従ってVCを移動する。"""
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        cfg_a_id, cfg_b_id = get_store().get_vc_channels(guild_id)
        channel_a = guild.get_channel(cfg_a_id) if cfg_a_id else None
        channel_b = guild.get_channel(cfg_b_id) if cfg_b_id else None

        if not isinstance(channel_a, discord.VoiceChannel) or not isinstance(channel_b, discord.VoiceChannel):
            await channel.send(
                'VCが設定されていません。`/config vc` で Team A / Team B のVCを設定してください。'
            )
            return

        last = get_stats_store().get_last_match(guild_id)
        if not last:
            await channel.send('チーム分け結果がありません。先に `/split run` を実行してください。')
            return

        vc_channels = [channel_a, channel_b]
        team_labels = ["🅰 Team A", "🅱 Team B"]
        moved: List[List[str]] = [[], []]
        skipped: List[str] = []

        for tidx, uids in enumerate(last):
            for uid in uids:
                m = guild.get_member(uid)
                if m is None or m.voice is None:
                    skipped.append(m.display_name if m else f"<@{uid}>")
                    continue
                try:
                    await m.move_to(vc_channels[tidx])
                    moved[tidx].append(m.display_name)
                except (discord.Forbidden, discord.HTTPException):
                    skipped.append(m.display_name)

        embed = discord.Embed(title="🎙️ VC移動完了")
        for tidx, names in enumerate(moved):
            embed.add_field(
                name=f"{team_labels[tidx]} → {vc_channels[tidx].name}（{len(names)}人）",
                value="\n".join(f"・{n}" for n in names) if names else "(移動なし)",
                inline=True,
            )
        if skipped:
            embed.set_footer(text=f"VCに未参加のためスキップ: {', '.join(skipped)}")

        await channel.send(embed=embed)

        # VC移動済みなのでリロール不可にする
        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.clear_reaction('🔄')
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    async def _reaction_reroll(
        self,
        payload: discord.RawReactionActionEvent,
        guild: discord.Guild,
        guild_id: int,
    ) -> None:
        """🔄 リアクション: 同じ設定でリロールしてメッセージを編集する。"""
        cfg = get_store().get_split_config(guild_id)
        mode = SplitMode(get_store().get_split_code(guild_id))

        try:
            result = self.service.split(guild_id, mode)
        except ValueError:
            return

        embed = self._build_embed_guild(guild, result, mode, cfg)

        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.edit(embed=embed)
            await msg.clear_reactions()
            for emoji in _REACTION_EMOJIS:
                await msg.add_reaction(emoji)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        # _pending_votes のエントリはそのまま（同じメッセージIDで継続）

    # -------------------------------------------------- /split move --

    @split.command(name="move", description="直前のチーム分け結果に従ってVCを移動します（管理者専用）")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel_a="Team A を移動させるVC（省略時は /config vc の設定値を使用）",
        channel_b="Team B を移動させるVC（省略時は /config vc の設定値を使用）",
    )
    async def split_move(
        self,
        interaction: discord.Interaction,
        channel_a: Optional[discord.VoiceChannel] = None,
        channel_b: Optional[discord.VoiceChannel] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で使ってね。", ephemeral=True)
            return

        # 省略時はコンフィグのデフォルトVCを使用
        if channel_a is None or channel_b is None:
            cfg_a_id, cfg_b_id = get_store().get_vc_channels(interaction.guild.id)
            if channel_a is None:
                ch = interaction.guild.get_channel(cfg_a_id) if cfg_a_id else None
                if not isinstance(ch, discord.VoiceChannel):
                    await interaction.response.send_message(
                        "Team A のVCが設定されていません。`/config vc` で設定するか、引数で直接指定してください。",
                        ephemeral=True,
                    )
                    return
                channel_a = ch
            if channel_b is None:
                ch = interaction.guild.get_channel(cfg_b_id) if cfg_b_id else None
                if not isinstance(ch, discord.VoiceChannel):
                    await interaction.response.send_message(
                        "Team B のVCが設定されていません。`/config vc` で設定するか、引数で直接指定してください。",
                        ephemeral=True,
                    )
                    return
                channel_b = ch

        last = get_stats_store().get_last_match(interaction.guild.id)
        if not last:
            await interaction.response.send_message(
                "直前のチーム分け結果がありません。先に `/split run` でチーム分けしてください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        channels = [channel_a, channel_b]
        team_labels = ["🅰 Team A", "🅱 Team B"]
        moved: List[List[str]] = [[], []]
        skipped: List[str] = []

        for tidx, uids in enumerate(last):
            for uid in uids:
                member = interaction.guild.get_member(uid)
                if member is None or member.voice is None:
                    name = member.display_name if member else f"<@{uid}>"
                    skipped.append(name)
                    continue
                try:
                    await member.move_to(channels[tidx])
                    moved[tidx].append(member.display_name)
                except (discord.Forbidden, discord.HTTPException):
                    skipped.append(member.display_name)

        embed = discord.Embed(title="🎙️ VC移動完了")
        for tidx, names in enumerate(moved):
            embed.add_field(
                name=f"{team_labels[tidx]} → {channels[tidx].name}（{len(names)}人）",
                value="\n".join(f"・{n}" for n in names) if names else "(移動なし)",
                inline=True,
            )
        if skipped:
            embed.set_footer(text=f"VCに未参加のためスキップ: {', '.join(skipped)}")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(TeamSplit(bot))
