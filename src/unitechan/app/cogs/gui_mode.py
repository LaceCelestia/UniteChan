from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.app.cogs._utils import is_admin
from unitechan.core.config_store import get_store
from unitechan.core.lobby_store import LobbyStore
from unitechan.core.split_mode import SplitMode
from unitechan.core.split_service import ROLE_CODE, Player, SplitResult, SplitService
from unitechan.core.stats_store import get_stats_store

MAX_TEAM_SIZE = 5
_JST = timezone(timedelta(hours=9))
_TRANSIENT_NOTICE_SECONDS = 15
NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
TEAM_LABELS = ["🟦 Team A", "🟥 Team B"]
ROLE_BADGES = {
    "ATK": "🟥ATK",
    "ALL": "🟪ALL",
    "SPD": "🟦SPD",
    "DEF": "🟩DEF",
    "SUP": "🟨SUP",
}


def _ordered_unique(user_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for uid in user_ids:
        if uid in seen:
            continue
        seen.add(uid)
        ordered.append(uid)
    return ordered


def _role_label(role_key: str) -> str:
    code = ROLE_CODE.get(role_key, role_key[:3].upper())
    return ROLE_BADGES.get(code, code)


@dataclass
class GuiPanelState:
    guild_id: int
    mode_code: str
    use_config_code: bool = False
    pool: list[int] = field(default_factory=list)
    team_a: list[int] = field(default_factory=list)
    team_b: list[int] = field(default_factory=list)
    spectators: list[int] = field(default_factory=list)
    recorded_winner: int | None = None
    awaiting_result: bool = False
    auto_result: SplitResult | None = None
    auto_result_mode_code: str | None = None

    def _remove_from_assignments(self, user_id: int) -> None:
        self.team_a = [uid for uid in self.team_a if uid != user_id]
        self.team_b = [uid for uid in self.team_b if uid != user_id]
        self.spectators = [uid for uid in self.spectators if uid != user_id]

    def _clear_result(self) -> None:
        self.recorded_winner = None
        self.awaiting_result = False

    def _clear_auto_result(self) -> None:
        self.auto_result = None
        self.auto_result_mode_code = None

    def unassigned(self) -> list[int]:
        assigned = set(self.team_a) | set(self.team_b) | set(self.spectators)
        return [uid for uid in self.pool if uid not in assigned]

    def is_ready(self) -> bool:
        return bool(self.team_a) and bool(self.team_b) and not self.unassigned()

    def current_teams(self) -> list[list[int]]:
        return [list(self.team_a), list(self.team_b)]

    def playable_pool(self) -> list[int]:
        spectator_ids = set(self.spectators)
        return [uid for uid in self.pool if uid not in spectator_ids]

    def assign_team(self, user_id: int, team_idx: int) -> bool:
        current_target = self.team_a if team_idx == 0 else self.team_b
        if user_id in current_target:
            return False
        if len(current_target) >= MAX_TEAM_SIZE:
            raise ValueError('そのチームは満員です。')
        if user_id not in self.pool:
            self.pool.append(user_id)
        self._remove_from_assignments(user_id)
        if team_idx == 0:
            self.team_a.append(user_id)
        else:
            self.team_b.append(user_id)
        self._clear_auto_result()
        self._clear_result()
        return True

    def assign_spectator(self, user_id: int) -> bool:
        if user_id in self.spectators:
            return False
        if user_id not in self.pool:
            self.pool.append(user_id)
        self._remove_from_assignments(user_id)
        self.spectators.append(user_id)
        self._clear_auto_result()
        self._clear_result()
        return True

    def remove_user(self, user_id: int) -> bool:
        if user_id not in self.pool:
            return False
        self.pool = [uid for uid in self.pool if uid != user_id]
        self._remove_from_assignments(user_id)
        self._clear_auto_result()
        self._clear_result()
        return True

    def replace_pool(self, user_ids: list[int]) -> None:
        ordered = _ordered_unique(user_ids)
        allowed = set(ordered)
        self.pool = ordered
        self.team_a = [uid for uid in self.team_a if uid in allowed]
        self.team_b = [uid for uid in self.team_b if uid in allowed]
        self.spectators = [uid for uid in self.spectators if uid in allowed]
        self._clear_auto_result()
        self._clear_result()

    def reset_assignments(self) -> bool:
        if not self.team_a and not self.team_b and not self.spectators and self.recorded_winner is None:
            return False
        self.team_a = []
        self.team_b = []
        self.spectators = []
        self._clear_auto_result()
        self._clear_result()
        return True

    def apply_split(
        self,
        team_a: list[int],
        team_b: list[int],
        spectators: list[int],
        *,
        split_result: SplitResult | None = None,
        mode_code: str | None = None,
    ) -> None:
        self.team_a = _ordered_unique(team_a)
        self.team_b = _ordered_unique(team_b)
        self.spectators = _ordered_unique(spectators)
        self.pool = _ordered_unique(self.pool + self.team_a + self.team_b + self.spectators)
        self.auto_result = split_result
        self.auto_result_mode_code = mode_code if split_result is not None else None
        self._clear_result()

    def start_match(self) -> None:
        self.recorded_winner = None
        self.awaiting_result = True

    def finish_match(self, winning_idx: int) -> None:
        self.recorded_winner = winning_idx
        self.awaiting_result = False

    def recorded_sides(self) -> tuple[list[int], list[int]] | None:
        if self.recorded_winner is None:
            return None
        teams = self.current_teams()
        winners = teams[self.recorded_winner]
        losers = teams[1 - self.recorded_winner]
        return winners, losers

    def display_auto_result(self) -> SplitResult | None:
        if self.auto_result is None:
            return None
        if self.auto_result_mode_code != self.mode_code:
            return None
        return self.auto_result


class GuiMode(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.lobby_store = LobbyStore()
        self.service = SplitService(self.lobby_store)
        self._name_cache: dict[tuple[int, int], str] = {}
        self._active_views: dict[int, set[GuiModeView]] = {}

    def _resolve_name_guild_cached(self, guild: discord.Guild, uid: int) -> str:
        alias = self.lobby_store.get_alias(guild.id, uid)
        if alias:
            return alias
        member = guild.get_member(uid)
        if member is not None:
            return member.display_name
        return f'ID:{uid}'

    async def _resolve_member(self, guild: discord.Guild, uid: int) -> discord.Member | None:
        member = guild.get_member(uid)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(uid)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _resolve_name_guild(self, guild: discord.Guild, uid: int) -> str:
        alias = self.lobby_store.get_alias(guild.id, uid)
        if alias:
            return alias

        cache_key = (guild.id, uid)
        cached = self._name_cache.get(cache_key)
        if cached:
            return cached

        member = await self._resolve_member(guild, uid)
        if member is not None:
            self._name_cache[cache_key] = member.display_name
            return member.display_name

        user = self.bot.get_user(uid)
        if user is not None:
            self._name_cache[cache_key] = user.name
            return user.name
        try:
            user = await self.bot.fetch_user(uid)
            self._name_cache[cache_key] = user.name
            return user.name
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return f'ID:{uid}'

    def _sort_user_ids(self, guild: discord.Guild, user_ids: list[int]) -> list[int]:
        return sorted(
            _ordered_unique(user_ids),
            key=lambda uid: self._resolve_name_guild_cached(guild, uid).casefold(),
        )

    async def _format_member_lines(self, guild: discord.Guild, user_ids: list[int]) -> str:
        if not user_ids:
            return '(なし)'
        names = await asyncio.gather(
            *(self._resolve_name_guild(guild, uid) for uid in user_ids)
        )
        return '\n'.join(f'・{name}' for name in names)

    async def _resolve_name_map(self, guild: discord.Guild, user_ids: list[int]) -> dict[int, str]:
        ordered = _ordered_unique(user_ids)
        names = await asyncio.gather(
            *(self._resolve_name_guild(guild, uid) for uid in ordered)
        )
        return dict(zip(ordered, names))

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
            f"{mode.mode_raw}  バランス:{rank} / ロール:{role} / "
            f"ポケモン:{pokemon} / 連続回避:{avoid} / 重複:{dup}"
        )

    async def _build_embed(self, guild: discord.Guild, state: GuiPanelState) -> discord.Embed:
        if state.use_config_code:
            state.mode_code = get_store().get_split_code(guild.id)
        cfg = get_store().get_split_config(guild.id)
        mode = SplitMode(state.mode_code)
        description = [
            '🟦 / 🟥 / 👀 / ↩️ は誰でも押せます。',
            '📥 / 🎲 / 🔄 / 🎙️ / 🏆 は管理者のみ使えます。',
        ]
        if state.awaiting_result:
            description.append('試合中: 勝敗入力が終わるまで A勝ち/B勝ち 以外は操作できません')
        if state.recorded_winner is not None:
            winner = 'Team A' if state.recorded_winner == 0 else 'Team B'
            description.append(f'直近結果: {winner} の勝利を記録済み')
            description.append('↩️ 勝敗取消 で直前の入力を戻せます')

        embed = discord.Embed(
            title='GUIモード',
            description='\n'.join(description),
            color=0x5865F2,
        )
        auto_result = state.display_auto_result()
        if auto_result is not None:
            name_map = await self._resolve_name_map(guild, state.pool)
            for idx, team in enumerate(auto_result.teams):
                lines: list[str] = []
                for i, mem in enumerate(team.members):
                    num = NUMS[i] if i < len(NUMS) else f"{i + 1}."
                    name = name_map.get(mem.user_id, f"ID:{mem.user_id}")
                    if mem.pokemon:
                        lines.append(f"{num} {name}  **{_role_label(mem.role)}** {mem.pokemon}")
                    elif mode.role_balance_mode != 0:
                        lines.append(f"{num} {name}  **{_role_label(mem.role)}**")
                    else:
                        lines.append(f"{num} {name}")

                if team.team_pokemon:
                    lines.append("")
                    poke_parts = [
                        f"**{_role_label(role)}** {pokemon}"
                        for role, pokemon in team.team_pokemon
                    ]
                    lines.append("🎮 " + "  ".join(poke_parts))

                embed.add_field(
                    name=f"{TEAM_LABELS[idx]} ({len(team.members)})",
                    value="\n".join(lines) if lines else "(なし)",
                    inline=True,
                )
        else:
            embed.add_field(
                name=f'🟦 Team A ({len(state.team_a)})',
                value=await self._format_member_lines(guild, state.team_a),
                inline=True,
            )
            embed.add_field(
                name=f'🟥 Team B ({len(state.team_b)})',
                value=await self._format_member_lines(guild, state.team_b),
                inline=True,
            )
        embed.add_field(
            name=f'👀 観戦 ({len(state.spectators)})',
            value=await self._format_member_lines(guild, state.spectators),
            inline=False,
        )
        embed.add_field(
            name=f'📋 未割当 ({len(state.unassigned())})',
            value=await self._format_member_lines(guild, state.unassigned()),
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

    async def _send_start_announce(
        self, channel: discord.TextChannel | discord.Thread, guild_id: int
    ) -> None:
        minutes = get_store().get_start_announce(guild_id)
        if minutes <= 0:
            return
        start_time = datetime.now(_JST) + timedelta(minutes=minutes)
        delete_after = (minutes + 5) * 60
        await channel.send(
            f'⏰ **{start_time.strftime("%H:%M")}** スタートです！',
            delete_after=delete_after,
        )

    async def _send_transient_notice(
        self,
        channel: discord.TextChannel | discord.Thread,
        content: str,
    ) -> None:
        await channel.send(content, delete_after=_TRANSIENT_NOTICE_SECONDS)

    async def _move_members(self, guild: discord.Guild, state: GuiPanelState) -> int:
        cfg_a_id, cfg_b_id = get_store().get_vc_channels(guild.id)
        channel_a = guild.get_channel(cfg_a_id) if cfg_a_id else None
        channel_b = guild.get_channel(cfg_b_id) if cfg_b_id else None

        if not isinstance(channel_a, discord.VoiceChannel) or not isinstance(channel_b, discord.VoiceChannel):
            raise ValueError('VCが設定されていません。`/config vc` で Team A / Team B のVCを設定してください。')

        moved = 0
        for target, users in ((channel_a, state.team_a), (channel_b, state.team_b)):
            for uid in users:
                member = await self._resolve_member(guild, uid)
                if member is None or member.voice is None:
                    continue
                try:
                    await member.move_to(target)
                    moved += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue
        return moved

    def _sync_from_lobby(self, guild: discord.Guild, state: GuiPanelState) -> None:
        lobby, _ = self.lobby_store.snapshot(guild.id)
        state.replace_pool(self._sort_user_ids(guild, list(lobby)))

    def _auto_split(self, guild: discord.Guild, state: GuiPanelState) -> None:
        if state.use_config_code:
            state.mode_code = get_store().get_split_code(guild.id)
        player_ids = state.playable_pool()
        if len(player_ids) < 2:
            raise ValueError('自動分けするには、プレイヤーが2人以上必要です。')

        mode = SplitMode.parse(state.mode_code)
        cfg = get_store().get_split_config(guild.id)
        _, ranks = self.lobby_store.snapshot(guild.id)
        players = [
            Player(
                uid,
                self._resolve_name_guild_cached(guild, uid),
                ranks.get(uid, 'ビギナー'),
            )
            for uid in player_ids
        ]
        result = self.service.preview_split(guild.id, players, mode, cfg)

        manual_spectators = list(state.spectators)
        auto_spectators = [player.user_id for player in result.spectators]
        state.apply_split(
            [member.user_id for member in result.teams[0].members],
            [member.user_id for member in result.teams[1].members],
            manual_spectators + auto_spectators,
            split_result=result,
            mode_code=state.mode_code,
        )

    async def _record_result(self, guild_id: int, teams: list[list[int]], winning_idx: int) -> None:
        store = get_stats_store()
        if store.get_last_match(guild_id) != teams:
            store.set_last_match(guild_id, teams)
        store.record_result(guild_id, winning_idx)

    def _register_view(self, guild_id: int, view: GuiModeView) -> None:
        self._active_views.setdefault(guild_id, set()).add(view)

    def _unregister_view(self, guild_id: int, view: GuiModeView) -> None:
        views = self._active_views.get(guild_id)
        if not views:
            return
        views.discard(view)
        if not views:
            self._active_views.pop(guild_id, None)

    async def refresh_guild_panels(self, guild: discord.Guild) -> int:
        refreshed = 0
        for view in list(self._active_views.get(guild.id, set())):
            try:
                if await view.refresh_from_config(guild):
                    refreshed += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                self._unregister_view(guild.id, view)
        return refreshed

    @app_commands.command(name='guimode', description='ボタン操作でチーム分けを進めるGUIパネルを作成します')
    @app_commands.describe(code='5桁コード。省略時は /config split の設定値を使用')
    async def guimode(self, interaction: discord.Interaction, code: str | None = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        try:
            mode = SplitMode.parse(code) if code else SplitMode(get_store().get_split_code(interaction.guild.id))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        lobby, _ = self.lobby_store.snapshot(interaction.guild.id)
        state = GuiPanelState(
            guild_id=interaction.guild.id,
            mode_code=mode.mode_raw,
            use_config_code=(code is None),
            pool=self._sort_user_ids(interaction.guild, list(lobby)),
        )
        view = GuiModeView(self, state)
        await interaction.response.defer()
        message = await interaction.followup.send(
            embed=await self._build_embed(interaction.guild, state),
            view=view,
            wait=True,
        )
        view.bind_message(message)
        self._register_view(interaction.guild.id, view)


class GuiModeView(discord.ui.View):
    def __init__(self, cog: GuiMode, state: GuiPanelState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state
        self.message: discord.Message | None = None
        self._refresh_disabled()

    def bind_message(self, message: discord.Message) -> None:
        self.message = message

    async def refresh_from_config(self, guild: discord.Guild) -> bool:
        if not self.state.use_config_code or self.message is None:
            return False
        new_code = get_store().get_split_code(guild.id)
        if new_code == self.state.mode_code:
            return False
        self.state.mode_code = new_code
        self._refresh_disabled()
        await self.message.edit(
            embed=await self.cog._build_embed(guild, self.state),
            view=self,
        )
        return True

    def _button(self, custom_id: str) -> discord.ui.Button:
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == custom_id:
                return child
        raise RuntimeError(f'button not found: {custom_id}')

    def _refresh_disabled(self) -> None:
        ready = self.state.is_ready()
        result_done = self.state.recorded_winner is not None
        locked = self.state.awaiting_result

        self._button('guimode_team_a').disabled = locked
        self._button('guimode_team_b').disabled = locked
        self._button('guimode_spectate').disabled = locked
        self._button('guimode_leave').disabled = locked
        self._button('guimode_sync').disabled = locked
        self._button('guimode_auto').disabled = locked
        self._button('guimode_reset').disabled = locked
        self._button('guimode_move').disabled = locked or not ready
        self._button('guimode_win_a').disabled = (not locked) or result_done
        self._button('guimode_win_b').disabled = (not locked) or result_done
        self._button('guimode_undo_result').disabled = locked or (not result_done)

    async def _ensure_not_locked(self, interaction: discord.Interaction) -> bool:
        if self.state.awaiting_result:
            await interaction.response.send_message(
                'VC移動後は勝敗入力が終わるまで操作できません。先に A勝ち / B勝ち を押してください。',
                ephemeral=True,
            )
            return False
        return True

    async def _require_admin(self, interaction: discord.Interaction) -> bool:
        if not is_admin(interaction):
            await interaction.response.send_message('この操作は管理者のみ使用できます。', ephemeral=True)
            return False
        return True

    async def _update_message(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        await interaction.response.defer()
        await self._edit_panel_message(interaction)

    async def _edit_panel_message(self, interaction: discord.Interaction) -> None:
        self._refresh_disabled()
        if interaction.guild is None or interaction.message is None:
            return
        await interaction.message.edit(
            embed=await self.cog._build_embed(interaction.guild, self.state),
            view=self,
        )

    @discord.ui.button(label='Team A', emoji='🟦', style=discord.ButtonStyle.primary, custom_id='guimode_team_a', row=0)
    async def join_team_a(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._ensure_not_locked(interaction):
            return
        try:
            changed = self.state.assign_team(interaction.user.id, 0)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not changed:
            await interaction.response.send_message('すでに Team A に入っています。', ephemeral=True)
            return
        await self._update_message(interaction)

    @discord.ui.button(label='Team B', emoji='🟥', style=discord.ButtonStyle.danger, custom_id='guimode_team_b', row=0)
    async def join_team_b(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._ensure_not_locked(interaction):
            return
        try:
            changed = self.state.assign_team(interaction.user.id, 1)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not changed:
            await interaction.response.send_message('すでに Team B に入っています。', ephemeral=True)
            return
        await self._update_message(interaction)

    @discord.ui.button(label='観戦', emoji='👀', style=discord.ButtonStyle.secondary, custom_id='guimode_spectate', row=0)
    async def spectate(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._ensure_not_locked(interaction):
            return
        changed = self.state.assign_spectator(interaction.user.id)
        if not changed:
            await interaction.response.send_message('すでに観戦に入っています。', ephemeral=True)
            return
        await self._update_message(interaction)

    @discord.ui.button(label='離脱', emoji='↩️', style=discord.ButtonStyle.secondary, custom_id='guimode_leave', row=0)
    async def leave_panel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._ensure_not_locked(interaction):
            return
        changed = self.state.remove_user(interaction.user.id)
        if not changed:
            await interaction.response.send_message('まだこのパネルに参加していません。', ephemeral=True)
            return
        await self._update_message(interaction)

    @discord.ui.button(label='ロビー同期', emoji='📥', style=discord.ButtonStyle.secondary, custom_id='guimode_sync', row=1)
    async def sync_lobby(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_admin(interaction):
            return
        if not await self._ensure_not_locked(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        self.cog._sync_from_lobby(interaction.guild, self.state)
        await self._update_message(interaction)

    @discord.ui.button(label='自動分け', emoji='🎲', style=discord.ButtonStyle.success, custom_id='guimode_auto', row=1)
    async def auto_split(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_admin(interaction):
            return
        if not await self._ensure_not_locked(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        try:
            self.cog._auto_split(interaction.guild, self.state)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self._update_message(interaction)

    @discord.ui.button(label='リセット', emoji='🔄', style=discord.ButtonStyle.secondary, custom_id='guimode_reset', row=1)
    async def reset_panel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_admin(interaction):
            return
        if not await self._ensure_not_locked(interaction):
            return
        changed = self.state.reset_assignments()
        if not changed:
            await interaction.response.send_message('リセットする内容がありません。', ephemeral=True)
            return
        await self._update_message(interaction)

    @discord.ui.button(label='VC移動', emoji='🎙️', style=discord.ButtonStyle.success, custom_id='guimode_move', row=1)
    async def move_voice(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_admin(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not self.state.is_ready():
            await interaction.response.send_message('未割当メンバーがいるため、まだVC移動できません。', ephemeral=True)
            return

        try:
            await interaction.response.defer()
            moved = await self.cog._move_members(interaction.guild, self.state)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        self.state.start_match()
        await self._edit_panel_message(interaction)
        if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await self.cog._send_transient_notice(
                interaction.channel,
                f'🎙️ VC移動完了（{moved}人）',
            )
            await self.cog._send_start_announce(interaction.channel, interaction.guild.id)

    @discord.ui.button(label='A勝ち', emoji='🏆', style=discord.ButtonStyle.primary, custom_id='guimode_win_a', row=2)
    async def win_team_a(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._record_winner(interaction, 0)

    @discord.ui.button(label='B勝ち', emoji='🏆', style=discord.ButtonStyle.danger, custom_id='guimode_win_b', row=2)
    async def win_team_b(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._record_winner(interaction, 1)

    @discord.ui.button(label='勝敗取消', emoji='↩️', style=discord.ButtonStyle.secondary, custom_id='guimode_undo_result', row=2)
    async def undo_result(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._require_admin(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if self.state.awaiting_result:
            await interaction.response.send_message('まだ勝敗が記録されていません。', ephemeral=True)
            return
        recorded = self.state.recorded_sides()
        if recorded is None:
            await interaction.response.send_message('取り消せる勝敗記録がありません。', ephemeral=True)
            return

        winners, losers = recorded
        await interaction.response.defer()
        ok = get_stats_store().undo_last_result_if_matches(interaction.guild.id, winners, losers)
        if not ok:
            await interaction.followup.send(
                '直前の戦績が別操作で更新されているため、このパネルからは取り消せません。',
                ephemeral=True,
            )
            return

        self.state.start_match()
        await self._edit_panel_message(interaction)
        if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await self.cog._send_transient_notice(
                interaction.channel,
                '↩️ 勝敗記録を取り消しました。A勝ち / B勝ち を押し直してください。',
            )

    async def _record_winner(self, interaction: discord.Interaction, winning_idx: int) -> None:
        if not await self._require_admin(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if self.state.recorded_winner is not None:
            await interaction.response.send_message('この試合結果はすでに記録済みです。', ephemeral=True)
            return
        if not self.state.awaiting_result:
            await interaction.response.send_message('先に VC移動 を押して試合開始状態にしてください。', ephemeral=True)
            return
        if not self.state.is_ready():
            await interaction.response.send_message('未割当メンバーがいるため、まだ勝敗を記録できません。', ephemeral=True)
            return

        await interaction.response.defer()
        await self.cog._record_result(interaction.guild.id, self.state.current_teams(), winning_idx)
        self.state.finish_match(winning_idx)
        await self._edit_panel_message(interaction)

        team_label = 'Team A' if winning_idx == 0 else 'Team B'
        if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await self.cog._send_transient_notice(
                interaction.channel,
                f'🏆 **{team_label}** の勝利を記録しました！',
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuiMode(bot))
