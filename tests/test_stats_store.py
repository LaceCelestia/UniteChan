from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from unitechan.core import split_service as split_module
from unitechan.core import stats_store as stats_module
from unitechan.core.config_store import ConfigStore, SplitConfig
from unitechan.core.lobby_store import LobbyStore
from unitechan.core.split_mode import SplitMode
from unitechan.core.split_service import Player, SplitService
from unitechan.core.stats_store import StatsStore


class StatsStoreHistoryTests(unittest.TestCase):
    def _store(self, tmp: str) -> StatsStore:
        return StatsStore(Path(tmp) / 'stats.json')

    def test_overwriting_last_match_keeps_previous_unrecorded_match(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.set_last_match(
                1,
                [[1, 2], [3, 4]],
                split_context={'mode_code': '00010', 'spectators': [9]},
            )
            store.set_last_match(
                1,
                [[5, 6], [7, 8]],
                split_context={'mode_code': '10000', 'spectators': []},
            )

            self.assertEqual(store.get_match_history(1), [[[1, 2], [3, 4]]])
            self.assertEqual(
                store.get_split_context(1, [[1, 2], [3, 4]]),
                {'mode_code': '00010', 'spectators': [9]},
            )
            self.assertEqual(
                store.get_split_context(1, [[5, 6], [7, 8]]),
                {'mode_code': '10000', 'spectators': []},
            )

    def test_recording_current_match_does_not_add_it_to_prev_history(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            teams = [[1, 2], [3, 4]]
            store.set_last_match(1, teams, split_context={'mode_code': '00010'})

            winners, losers = store.record_result(1, 0)

            self.assertEqual(winners, [1, 2])
            self.assertEqual(losers, [3, 4])
            self.assertIsNone(store.get_last_match(1))
            self.assertEqual(store.get_match_history(1), [])
            self.assertIsNone(store.get_split_context(1, teams))

    def test_recording_history_match_removes_it_from_prev_history(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            old_teams = [[1, 2], [3, 4]]
            store.set_last_match(1, old_teams, split_context={'mode_code': '00010'})
            store.set_last_match(1, [[5, 6], [7, 8]])

            winners, losers = store.record_result_for_teams(1, old_teams, 1)

            self.assertEqual(winners, [3, 4])
            self.assertEqual(losers, [1, 2])
            self.assertEqual(store.get_match_history(1), [])
            self.assertIsNone(store.get_split_context(1, old_teams))

    def test_reset_stats_returns_actual_record_count(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.set_last_match(1, [[1, 2], [3, 4]])

            self.assertEqual(store.reset_stats(1), 0)

            store.set_last_match(1, [[1, 2], [3, 4]])
            store.record_result(1, 0)
            self.assertEqual(store.reset_stats(1), 4)


class SplitServiceStoredContextTests(unittest.TestCase):
    def test_commit_uses_stored_spectators_and_role_assignments(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = stats_module._STORE
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 123
            SplitService.clear_history_cache(guild_id)
            try:
                teams = [[1, 2], [3, 4]]
                context = {
                    'version': 1,
                    'mode_code': '00010',
                    'avoid_count': 1,
                    'spectators': [9],
                    'role_assignments': {
                        '1': 'attacker',
                        '2': 'defender',
                        '3': 'speedster',
                        '4': 'supporter',
                    },
                }
                stats_module.get_stats_store().set_last_match(
                    guild_id,
                    teams,
                    split_context=context,
                )
                service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))

                self.assertTrue(service.commit_teams_with_stored_context(guild_id, teams))

                counts, last = stats_module.get_stats_store().get_spectator_history(guild_id)
                self.assertEqual(counts, {9: 1})
                self.assertEqual(last, [9])
                self.assertEqual(
                    stats_module.get_stats_store().get_role_history(guild_id),
                    {
                        1: ['attacker'],
                        2: ['defender'],
                        3: ['speedster'],
                        4: ['supporter'],
                    },
                )
            finally:
                SplitService.clear_history_cache(guild_id)
                stats_module._STORE = old_store

    def test_undo_restores_committed_context_without_recommitting_history(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = stats_module._STORE
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 456
            SplitService.clear_history_cache(guild_id)
            try:
                teams = [[1, 2], [3, 4]]
                context = {
                    'version': 1,
                    'mode_code': '00010',
                    'avoid_count': 1,
                    'spectators': [9],
                    'role_assignments': {
                        '1': 'attacker',
                        '2': 'defender',
                        '3': 'speedster',
                        '4': 'supporter',
                    },
                }
                store = stats_module.get_stats_store()
                store.set_last_match(guild_id, teams, split_context=context)
                service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))

                self.assertTrue(service.commit_teams_with_stored_context(guild_id, teams))
                store.record_result(guild_id, 0)
                self.assertTrue(store.undo_last_result(guild_id))

                restored_context = store.get_split_context(guild_id, teams)
                self.assertIsNotNone(restored_context)
                self.assertTrue(restored_context.get('history_committed'))
                self.assertEqual(restored_context.get('role_assignments'), context['role_assignments'])
                self.assertTrue(service.commit_teams_with_stored_context(guild_id, teams))

                self.assertEqual(store.get_pair_history(guild_id), {'1_2': 1, '3_4': 1})
                counts, last = store.get_spectator_history(guild_id)
                self.assertEqual(counts, {9: 1})
                self.assertEqual(last, [9])
                stored_context = store.get_split_context(guild_id, teams)
                self.assertIsNotNone(stored_context)
                self.assertTrue(stored_context.get('history_committed'))
                self.assertEqual(stored_context.get('role_assignments'), context['role_assignments'])
                self.assertEqual(
                    store.get_role_history(guild_id),
                    {
                        1: ['attacker'],
                        2: ['defender'],
                        3: ['speedster'],
                        4: ['supporter'],
                    },
                )
                stored_context = store.get_split_context(guild_id, teams)
                self.assertIsNotNone(stored_context)
                self.assertTrue(stored_context.get('history_committed'))
                self.assertEqual(stored_context.get('role_assignments'), context['role_assignments'])

                store.set_last_match(guild_id, teams, split_context=stored_context)
                store.record_result(guild_id, 0)
                self.assertTrue(store.undo_last_result(guild_id))
                self.assertTrue(service.commit_teams_with_stored_context(guild_id, teams))

                self.assertEqual(store.get_pair_history(guild_id), {'1_2': 1, '3_4': 1})
                counts, last = store.get_spectator_history(guild_id)
                self.assertEqual(counts, {9: 1})
                self.assertEqual(last, [9])
                self.assertEqual(
                    store.get_role_history(guild_id),
                    {
                        1: ['attacker'],
                        2: ['defender'],
                        3: ['speedster'],
                        4: ['supporter'],
                    },
                )
            finally:
                SplitService.clear_history_cache(guild_id)
                stats_module._STORE = old_store

    def test_commit_from_context_uses_original_split_settings(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = stats_module._STORE
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 457
            SplitService.clear_history_cache(guild_id)
            try:
                teams = [[1, 2], [3, 4]]
                context = {
                    'version': 1,
                    'mode_code': '00010',
                    'avoid_count': 1,
                    'spectators': [9],
                    'role_assignments': {
                        '1': 'attacker',
                        '2': 'defender',
                        '3': 'speedster',
                        '4': 'supporter',
                    },
                }
                service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))

                self.assertTrue(service.commit_teams_from_context(guild_id, teams, context))

                store = stats_module.get_stats_store()
                counts, last = store.get_spectator_history(guild_id)
                self.assertEqual(counts, {9: 1})
                self.assertEqual(last, [9])
                self.assertEqual(
                    store.get_role_history(guild_id),
                    {
                        1: ['attacker'],
                        2: ['defender'],
                        3: ['speedster'],
                        4: ['supporter'],
                    },
                )
            finally:
                SplitService.clear_history_cache(guild_id)
                stats_module._STORE = old_store

    def test_commit_from_context_filters_invalid_saved_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = stats_module._STORE
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 458
            SplitService.clear_history_cache(guild_id)
            try:
                context = {
                    'version': 1,
                    'mode_code': '00010',
                    'avoid_count': 1,
                    'spectators': [9, '009', 0, '0', True, 'x'],
                    'role_assignments': {
                        '1': 'attacker',
                        '0': 'defender',
                        0: 'speedster',
                        '2': 'bad_role',
                        '3': 'supporter',
                    },
                }
                service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))

                self.assertTrue(
                    service.commit_teams_from_context(
                        guild_id,
                        [[1, 0, 1], [2, 3, '3']],
                        context,
                    )
                )

                store = stats_module.get_stats_store()
                counts, last = store.get_spectator_history(guild_id)
                self.assertEqual(counts, {9: 1})
                self.assertEqual(last, [9])
                self.assertEqual(
                    store.get_role_history(guild_id),
                    {1: ['attacker'], 3: ['supporter']},
                )
                self.assertEqual(store.get_pair_history(guild_id), {'2_3': 1})
                stored_context = store.get_split_context(guild_id, [[1], [2, 3]])
                self.assertEqual(stored_context.get('spectators'), [9])
                self.assertEqual(
                    stored_context.get('role_assignments'),
                    {'1': 'attacker', '3': 'supporter'},
                )
                self.assertTrue(stored_context.get('history_committed'))
            finally:
                SplitService.clear_history_cache(guild_id)
                stats_module._STORE = old_store


class SplitServiceSeparateTests(unittest.TestCase):
    def test_split_rejects_invalid_team_count_boundaries(self) -> None:
        with TemporaryDirectory() as tmp:
            service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))
            players = [
                Player(1, 'p1', 'rank'),
                Player(2, 'p2', 'rank'),
            ]
            cfg = SplitConfig(role_balance_targets={}, avoid_count=0)

            with self.assertRaises(ValueError):
                service.preview_split(788, players, SplitMode('00000'), cfg, team_count=1)
            with self.assertRaises(ValueError):
                service.preview_split(788, players, SplitMode('00000'), cfg, team_count=3)

    def test_separate_pairs_are_satisfied_when_feasible(self) -> None:
        with TemporaryDirectory() as tmp:
            old_store = stats_module._STORE
            stats_module._STORE = StatsStore(Path(tmp) / 'stats.json')
            guild_id = 789
            SplitService.clear_history_cache(guild_id)
            try:
                service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))
                players = [
                    Player(1, 'p1', 'rank'),
                    Player(2, 'p2', 'rank'),
                    Player(3, 'p3', 'rank'),
                    Player(4, 'p4', 'rank'),
                ]
                cfg = SplitConfig(
                    role_balance_targets={},
                    avoid_count=0,
                    separate_pairs=frozenset({(1, 2), (1, 3)}),
                )

                for seed in range(20):
                    random.seed(seed)
                    result = service.preview_split(guild_id, players, SplitMode('00000'), cfg)
                    teams = [
                        {member.user_id for member in team.members}
                        for team in result.teams
                    ]
                    self.assertTrue(
                        all(not ({uid1, uid2} <= team) for uid1, uid2 in cfg.separate_pairs for team in teams),
                        f'seed={seed}, teams={teams}',
                    )
            finally:
                SplitService.clear_history_cache(guild_id)
                stats_module._STORE = old_store

    def test_exact_separate_limit_falls_back_to_valid_split(self) -> None:
        with TemporaryDirectory() as tmp:
            old_limit = split_module._EXACT_SEPARATE_NODE_LIMIT
            split_module._EXACT_SEPARATE_NODE_LIMIT = 1
            try:
                service = SplitService(LobbyStore(Path(tmp) / 'lobby.json'))
                players = [
                    Player(1, 'p1', 'rank'),
                    Player(2, 'p2', 'rank'),
                    Player(3, 'p3', 'rank'),
                    Player(4, 'p4', 'rank'),
                ]
                cfg = SplitConfig(
                    role_balance_targets={},
                    avoid_count=0,
                    separate_pairs=frozenset({(1, 2)}),
                )

                result = service.preview_split(790, players, SplitMode('00000'), cfg)

                assigned = sorted(
                    member.user_id
                    for team in result.teams
                    for member in team.members
                )
                self.assertEqual(assigned, [1, 2, 3, 4])
            finally:
                split_module._EXACT_SEPARATE_NODE_LIMIT = old_limit


class CorruptStateReadTests(unittest.TestCase):
    def test_config_store_ignores_invalid_saved_values(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(
                json.dumps({
                    '1': {
                        'split': {
                            'code': '99999',
                            'role_balance': {'attacker': 'x', 'defender': 2},
                            'avoid': 'bad',
                        },
                        'separate_pairs': [[1, 2, 3], ['4', '5'], [6, 6], ['x', 7], [0, 8]],
                        'start_announce_minutes': 'bad',
                        'vc_channels': {'0': '123', '1': '-1'},
                        'banned_pokemon': 'not-a-list',
                    },
                }),
                encoding='utf-8',
            )
            store = ConfigStore(path)

            cfg = store.get_split_config(1)

            self.assertEqual(cfg.role_balance_targets['attacker'], 0)
            self.assertEqual(cfg.role_balance_targets['defender'], 2)
            self.assertEqual(cfg.avoid_count, 0)
            self.assertEqual(cfg.separate_pairs, frozenset({(4, 5)}))
            self.assertEqual(store.get_separate_pairs(1), [[4, 5]])
            self.assertEqual(store.get_split_code(1), '00000')
            self.assertEqual(store.get_start_announce(1), 0)
            self.assertEqual(store.get_vc_channels(1), (123, None))
            self.assertEqual(store.get_banned_pokemon(1), frozenset())

    def test_config_store_replaces_invalid_guild_bucket(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'1': 'bad'}), encoding='utf-8')
            store = ConfigStore(path)

            cfg = store.get_split_config(1)

            self.assertEqual(cfg.role_balance_targets['attacker'], 0)
            self.assertEqual(cfg.separate_pairs, frozenset())

    def test_config_setters_replace_invalid_nested_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(
                json.dumps({
                    '1': {
                        'split': 'bad',
                        'vc_channels': 'bad',
                        'banned_pokemon': 'bad',
                    },
                }),
                encoding='utf-8',
            )
            store = ConfigStore(path)

            store.set_split_code(1, '12211')
            store.set_role_balance_targets(1, 1, 2, 3, 4, 5)
            store.set_avoid_count(1, 4)
            store.set_vc_channel(1, 0, 99)

            self.assertEqual(store.get_split_code(1), '12211')
            cfg = store.get_split_config(1)
            self.assertEqual(cfg.role_balance_targets['attacker'], 1)
            self.assertEqual(cfg.role_balance_targets['supporter'], 5)
            self.assertEqual(cfg.avoid_count, 4)
            self.assertEqual(store.get_vc_channels(1), (99, None))

            self.assertTrue(store.ban_pokemon(1, 'Pikachu'))
            self.assertFalse(store.ban_pokemon(1, 'Pikachu'))
            self.assertEqual(store.get_banned_pokemon(1), frozenset({'Pikachu'}))
            self.assertEqual(store.clear_banned_pokemon(1), 1)
            self.assertEqual(store.get_banned_pokemon(1), frozenset())
            self.assertFalse(store.add_separate_pair(1, 0, 8))
            self.assertFalse(store.add_separate_pair(1, 6, 6))
            self.assertTrue(store.add_separate_pair(1, 6, 8))
            self.assertFalse(store.add_separate_pair(1, 8, 6))
            self.assertEqual(store.get_separate_pairs(1), [[6, 8]])
            self.assertFalse(store.remove_separate_pair(1, 0, 8))
            self.assertTrue(store.remove_separate_pair(1, 8, 6))
            self.assertEqual(store.get_separate_pairs(1), [])

    def test_stats_store_ignores_invalid_saved_values(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stats.json'
            path.write_text(
                json.dumps({
                    '1': {
                        'records': {
                            'abc': {'wins': 9, 'losses': 9},
                            '2': {'wins': '3', 'losses': -1},
                            '3': 'bad',
                        },
                        'daily_records': {
                            '2026-05-08': {
                                'x': {'wins': 1, 'losses': 1},
                                '2': {'wins': 2, 'losses': 'bad'},
                            },
                        },
                        'role_history': {'2': ['attacker'], 'x': ['bad'], '3': 'bad'},
                        'spectator_counts': {'2': '4', 'x': 10, '3': -1},
                        'last_spectators': ['2', 'x', 3, 0, -1],
                        'pair_history': {
                            '1_2': '5',
                            '002_001': '6',
                            '0_1': '8',
                            '2_2': '7',
                            'bad': '9',
                            '3_4': -1,
                        },
                    },
                }),
                encoding='utf-8',
            )
            store = StatsStore(path)

            self.assertEqual(
                store.get_all_records(1),
                {
                    2: {'wins': 3, 'losses': 0},
                },
            )
            self.assertEqual(
                store.get_daily_records(1, '2026-05-08'),
                {2: {'wins': 2, 'losses': 0}},
            )
            self.assertEqual(store.get_role_history(1), {2: ['attacker']})
            self.assertEqual(store.get_spectator_history(1), ({2: 4, 3: 0}, [2, 3]))
            self.assertEqual(store.get_pair_history(1), {'1_2': 11})

    def test_stats_store_replaces_invalid_guild_bucket(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stats.json'
            path.write_text(json.dumps({'1': 'bad'}), encoding='utf-8')
            store = StatsStore(path)

            self.assertEqual(store.get_all_records(1), {})
            self.assertIsNone(store.get_last_match(1))

    def test_stats_store_ignores_invalid_match_shapes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stats.json'
            path.write_text(
                json.dumps({
                    '1': {
                        'last_match': [[], ['x']],
                        'match_history': [
                            [[], []],
                            [[1], []],
                            [[2], [3]],
                        ],
                        'prev_match': [[4], []],
                    },
                }),
                encoding='utf-8',
            )
            store = StatsStore(path)

            self.assertIsNone(store.get_last_match(1))
            self.assertEqual(store.get_match_history(1), [[[2], [3]]])
            self.assertIsNone(store.get_prev_match(1))
            self.assertEqual(store.record_result(1, 0), ([], []))
            self.assertEqual(store.record_result_for_teams(1, [[1], []], 0), ([], []))

    def test_stats_store_deduplicates_saved_match_members(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stats.json'
            path.write_text(
                json.dumps({'1': {'last_match': [[1, '1', 0], [1, 2, '2']]}}),
                encoding='utf-8',
            )
            store = StatsStore(path)

            self.assertEqual(store.get_last_match(1), [[1], [2]])

    def test_undo_persists_removal_of_corrupt_last_result(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stats.json'
            path.write_text(json.dumps({'1': {'last_result': 'bad'}}), encoding='utf-8')
            store = StatsStore(path)

            self.assertFalse(store.undo_last_result(1))
            reloaded = StatsStore(path)

            self.assertIsNone(reloaded.get_last_result(1))
            self.assertNotIn('last_result', json.loads(path.read_text(encoding='utf-8'))['1'])

    def test_lobby_store_ignores_invalid_saved_values(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lobby.json'
            path.write_text(
                json.dumps({
                    '1': 'bad',
                    '2': {'members': 'bad', 'ranks': [], 'aliases': []},
                    '3': {
                        'members': ['4', 'x', 0],
                        'ranks': {'4': 'CustomRank', 'x': 'bad', '0': 'bad'},
                        'aliases': {'4': 'Player 4', 'x': 'bad', '0': 'bad'},
                    },
                }),
                encoding='utf-8',
            )
            store = LobbyStore(path)

            self.assertEqual(store.snapshot(1), (set(), {}))
            self.assertEqual(store.snapshot(2), (set(), {}))
            self.assertEqual(store.snapshot(3), ({4}, {4: 'CustomRank'}))
            self.assertEqual(store.get_alias(3, 4), 'Player 4')

    def test_stats_import_survives_corrupt_existing_records(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stats.json'
            path.write_text(
                json.dumps({
                    '1': {
                        'records': {'2': 'bad'},
                        'daily_records': {'2026-05-08': {'2': 'bad'}},
                    },
                }),
                encoding='utf-8',
            )
            store = StatsStore(path)

            result = store.merge_stats(
                1,
                {
                    'version': 1,
                    'records': {'2': {'wins': 1, 'losses': 2}},
                    'daily_records': {'2026-05-08': {'2': {'wins': 3, 'losses': 4}}},
                },
            )

            self.assertEqual(result, {'added_wins': 1, 'added_losses': 2})
            self.assertEqual(store.get_all_records(1), {2: {'wins': 1, 'losses': 2}})
            self.assertEqual(
                store.get_daily_records(1, '2026-05-08'),
                {2: {'wins': 3, 'losses': 4}},
            )

    def test_stats_import_canonicalizes_duplicate_user_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            store = StatsStore(Path(tmp) / 'stats.json')

            result = store.merge_stats(
                1,
                {
                    'version': 1,
                    'records': {
                        '1': {'wins': 1, 'losses': 2},
                        '001': {'wins': 3, 'losses': 4},
                    },
                    'daily_records': {
                        '2026-05-08': {
                            '1': {'wins': 5, 'losses': 6},
                            '001': {'wins': 7, 'losses': 8},
                        },
                    },
                },
            )

            self.assertEqual(result, {'added_wins': 4, 'added_losses': 6})
            self.assertEqual(store.get_all_records(1), {1: {'wins': 4, 'losses': 6}})
            self.assertEqual(
                store.get_daily_records(1, '2026-05-08'),
                {1: {'wins': 12, 'losses': 14}},
            )

    def test_stats_import_rejects_non_positive_user_ids(self) -> None:
        payloads = [
            {
                'version': 1,
                'records': {'0': {'wins': 1, 'losses': 0}},
                'daily_records': {},
            },
            {
                'version': 1,
                'records': {},
                'daily_records': {
                    '2026-05-08': {'0': {'wins': 0, 'losses': 1}},
                },
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with TemporaryDirectory() as tmp:
                    store = StatsStore(Path(tmp) / 'stats.json')

                    with self.assertRaises(ValueError):
                        store.merge_stats(1, payload)
                    self.assertEqual(store.get_all_records(1), {})


if __name__ == '__main__':
    unittest.main()
