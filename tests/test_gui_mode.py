from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from unitechan.app.cogs.gui_mode import GuiMode, GuiModeView, GuiPanelState
from unitechan.core import config_store as config_module
from unitechan.core import stats_store as stats_module
from unitechan.core.config_store import ConfigStore
from unitechan.core.lobby_store import LobbyStore
from unitechan.core.split_service import SplitResult, SplitService
from unitechan.core.stats_store import StatsStore


class _FakeGuild:
    id = 123

    def get_member(self, _uid: int):
        return None


class _FakeMessage:
    def __init__(self) -> None:
        self.edits = 0

    async def edit(self, **_kwargs) -> None:
        self.edits += 1


class _FakeLobbyStore:
    def snapshot(self, _guild_id: int):
        return {1, 2, 3}, {}


class _FakeCog:
    def __init__(self) -> None:
        self.lobby_store = _FakeLobbyStore()

    def _sort_user_ids(self, _guild, user_ids: list[int]) -> list[int]:
        return sorted(user_ids)

    async def _build_embed(self, _guild, _state):
        return None


class GuiModeStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_lobby_refresh_does_not_mutate_active_gui_match(self) -> None:
        state = GuiPanelState(guild_id=123, mode_code='00000', pool=[1, 2])
        state.apply_split([1], [2], [])
        state.start_match()
        view = GuiModeView(_FakeCog(), state)  # type: ignore[arg-type]
        message = _FakeMessage()
        view.bind_message(message)  # type: ignore[arg-type]

        changed = await view.refresh_panel(_FakeGuild(), sync_pool=True)  # type: ignore[arg-type]

        self.assertFalse(changed)
        self.assertEqual(state.pool, [1, 2])
        self.assertEqual(state.current_teams(), [[1], [2]])
        self.assertTrue(state.awaiting_result)
        self.assertEqual(message.edits, 0)

    async def test_config_refresh_does_not_invalidate_auto_split_result(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = config_module._STORE
            config_module._STORE = ConfigStore(Path(tmp) / 'config.json')
            try:
                config_module.get_store().set_split_code(123, '10000')
                state = GuiPanelState(
                    guild_id=123,
                    mode_code='00000',
                    use_config_code=True,
                    pool=[1, 2],
                    auto_result=SplitResult([]),
                    auto_result_mode_code='00000',
                )
                view = GuiModeView(_FakeCog(), state)  # type: ignore[arg-type]
                message = _FakeMessage()
                view.bind_message(message)  # type: ignore[arg-type]

                changed = await view.refresh_panel(_FakeGuild())  # type: ignore[arg-type]

                self.assertFalse(changed)
                self.assertEqual(state.mode_code, '00000')
                self.assertIsNotNone(state.display_auto_result())
                self.assertEqual(message.edits, 0)
            finally:
                config_module._STORE = old_store

    async def test_embed_syncs_config_code_when_panel_is_idle(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = config_module._STORE
            config_module._STORE = ConfigStore(Path(tmp) / 'config.json')
            try:
                config_module.get_store().set_split_code(123, '10000')
                cog = GuiMode.__new__(GuiMode)
                cog.bot = object()
                cog.lobby_store = _FakeLobbyStore()
                cog.service = object()
                cog._name_cache = {}
                cog._active_views = {}
                state = GuiPanelState(
                    guild_id=123,
                    mode_code='00000',
                    use_config_code=True,
                    pool=[],
                )

                await cog._build_embed(_FakeGuild(), state)  # type: ignore[arg-type]

                self.assertEqual(state.mode_code, '10000')
            finally:
                config_module._STORE = old_store

    def test_auto_split_context_includes_manual_spectators(self) -> None:
        with TemporaryDirectory() as tmp:
            old_config = config_module._STORE
            old_stats = stats_module._STORE
            config_module._STORE = ConfigStore(Path(tmp) / 'config.json')
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 123
            SplitService.clear_history_cache(guild_id)
            try:
                lobby = LobbyStore(Path(tmp) / 'lobby.json')
                cog = GuiMode.__new__(GuiMode)
                cog.bot = object()
                cog.lobby_store = lobby
                cog.service = SplitService(lobby)
                cog._name_cache = {}
                cog._active_views = {}
                state = GuiPanelState(guild_id=guild_id, mode_code='00000', pool=[1, 2, 3])
                state.assign_spectator(3)

                cog._auto_split(_FakeGuild(), state)  # type: ignore[arg-type]

                self.assertEqual(
                    sorted(uid for team in state.current_teams() for uid in team),
                    [1, 2],
                )
                self.assertEqual(state.spectators, [3])
                self.assertEqual(state.auto_result_context['spectators'], [3])  # type: ignore[index]
            finally:
                SplitService.clear_history_cache(guild_id)
                config_module._STORE = old_config
                stats_module._STORE = old_stats

    async def test_record_result_does_not_replace_newer_last_match(self) -> None:
        with TemporaryDirectory() as tmp:
            old_stats = stats_module._STORE
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 123
            try:
                store = stats_module.get_stats_store()
                gui_teams = [[1], [2]]
                newer_teams = [[9], [10]]
                store.set_last_match(guild_id, gui_teams, split_context={'mode_code': '00000'})
                store.set_last_match(guild_id, newer_teams, split_context={'mode_code': '10000'})
                cog = GuiMode.__new__(GuiMode)

                recorded = await cog._record_result(guild_id, gui_teams, 0)

                self.assertTrue(recorded)
                self.assertEqual(store.get_last_match(guild_id), newer_teams)
                self.assertEqual(store.get_record(guild_id, 1), {'wins': 1, 'losses': 0})
                self.assertEqual(store.get_record(guild_id, 2), {'wins': 0, 'losses': 1})
                self.assertEqual(
                    store.get_split_context(guild_id, newer_teams),
                    {'mode_code': '10000'},
                )
            finally:
                stats_module._STORE = old_stats


if __name__ == '__main__':
    unittest.main()
