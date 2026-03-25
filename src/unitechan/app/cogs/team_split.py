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


class TeamSplit(commands.Cog):
    """ユナイトのチーム分け"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.lobby_store = LobbyStore()
        self.service = SplitService(self.lobby_store)

    # /split グループ
    split = app_commands.Group(name="split", description="ユナイトチーム分けコマンド")

    # -------------------------------------------------- 名前解決（本番用） --

    async def _resolve_name(self, interaction: discord.Interaction, uid: int) -> str:
        guild = interaction.guild
        if guild is not None:
            m = guild.get_member(uid)
            if m:
                return m.display_name

        try:
            u = await interaction.client.fetch_user(uid)
            return u.name
        except Exception:
            return f"ID:{uid}"

    # -------------------------------------------------- モード表示 --

    def _mode_summary(self, mode: SplitMode, cfg) -> str:
        rank = "ON" if mode.use_rank_balance else "OFF"
        pokemon = {0: "割当なし", 1: "個人割当", 2: "チーム割当"}.get(mode.pokemon_assign_mode, "?")
        role = {0: "なし", 1: "自動", 2: "/config"}.get(mode.role_balance_mode, "?")
        dup = "許可" if mode.allow_cross_dup else "禁止"

        # ★ここ修正：フラグが ON かつ avoid_count > 0 のときだけ「n回」
        if mode.use_avoid and cfg.avoid_count > 0:
            avoid = f"{cfg.avoid_count}回"
        else:
            avoid = "OFF"

        return (
            f"{mode.mode_raw} / ランク:{rank}・ポケモン:{pokemon}・"
            f"ロール:{role}・重複:{dup}・連続回避:{avoid}"
        )

    # -------------------------------------------------- 共通表示処理 --

    async def _display(
        self,
        interaction: discord.Interaction,
        result: SplitResult,
        mode: SplitMode,
        cfg,
        *,
        resolve_names: bool,
    ) -> None:
        """チーム分け結果をEmbedで表示する。"""

        embed = discord.Embed(
            title="🏅 チーム分け結果",
            description=self._mode_summary(mode, cfg),
        )

        team_labels = ["🅰 Team A", "🅱 Team B"]

        for idx, team in enumerate(result.teams):
            lines: List[str] = []
            for mem in team.members:
                if resolve_names:
                    name = await self._resolve_name(interaction, mem.user_id)
                else:
                    name = mem.name

                if mem.pokemon:
                    code = ROLE_CODE.get(mem.role, mem.role[:3].upper())
                    lines.append(f"・{name} [{code}] {mem.pokemon}")
                else:
                    lines.append(f"・{name}")

            if not lines:
                lines.append("(なし)")

            embed.add_field(
                name=f"{team_labels[idx]} ({len(team.members)}人)",
                value="\n" + "\n".join(lines),
                inline=True,
            )

        if cfg.banned_pokemon:
            embed.add_field(
                name='🚫 バン中のポケモン',
                value=' / '.join(sorted(cfg.banned_pokemon)),
                inline=False,
            )

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /split run --

    @split.command(name="run", description="5桁コードでチーム分けを実行")
    @app_commands.describe(mode="例: 00000 / 11110 / 12111")
    async def split_run(self, interaction: discord.Interaction, mode: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で使ってね。", ephemeral=True)
            return

        try:
            m = SplitMode.parse(mode)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        guild_id = interaction.guild.id
        cfg = get_store().get_split_config(guild_id)

        result = self.service.split(guild_id, m)
        await self._display(interaction, result, m, cfg, resolve_names=True)

    # -------------------------------------------------- /split test --

    @split.command(name="test", description="デモチーム分け（管理者専用）")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(mode="例: 00000 / 11110 / 12111")
    async def split_test(self, interaction: discord.Interaction, mode: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で使ってね。", ephemeral=True)
            return

        try:
            m = SplitMode.parse(mode)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        guild_id = interaction.guild.id
        cfg = get_store().get_split_config(guild_id)

        players: List[Player] = [
            Player(i + 1, name, rank)
            for i, (name, rank) in enumerate(_DEMO_PLAYERS)
        ]

        result = self.service.split_custom(guild_id, players, m, cfg)
        await self._display(interaction, result, m, cfg, resolve_names=False)


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
