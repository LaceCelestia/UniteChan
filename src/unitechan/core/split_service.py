from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import ClassVar, Dict, FrozenSet, List, Optional
import random

import yaml

from .lobby_store import LobbyStore
from .split_mode import SplitMode
from .config_store import get_store
from .stats_store import get_stats_store
from .paths import data_path

# ランク順：弱 → 強
_RANK_ORDER = [
    "ビギナー",
    "スーパー",
    "ハイパー",
    "エリート",
    "エキスパート",
    "マスター",
    "レジェンド",
]


def _rank_weight(rank: str) -> int:
    """ランク名 → 数値（弱1〜強7）"""
    try:
        idx = _RANK_ORDER.index(rank)
    except ValueError:
        return 3
    return idx + 1


def _stats_weight(record: dict) -> float:
    """勝率 → 重み（1.0〜7.0、データなしは4.0）"""
    wins = record.get('wins', 0)
    total = wins + record.get('losses', 0)
    if total == 0:
        return 4.0
    return (wins / total) * 6.0 + 1.0


# ロールキー
def _positive_uid(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _ordered_positive_uids(values: object) -> List[int]:
    if not isinstance(values, list):
        return []
    result: List[int] = []
    seen: set[int] = set()
    for value in values:
        uid = _positive_uid(value)
        if uid is None or uid in seen:
            continue
        result.append(uid)
        seen.add(uid)
    return result


ROLE_KEYS = ["attacker", "all_rounder", "speedster", "defender", "supporter"]

# ロール3文字コード
ROLE_CODE = {
    "attacker": "ATK",
    "all_rounder": "ALL",
    "speedster": "SPD",
    "defender": "DEF",
    "supporter": "SUP",
}

_EXACT_SEPARATE_NODE_LIMIT = 100_000


@dataclass
class Player:
    user_id: int
    name: str
    rank_name: str


@dataclass
class TeamMemberAssignment:
    user_id: int
    name: str
    role: str
    pokemon: Optional[str]
    rank_name: str
    rank_value: int


@dataclass
class TeamResult:
    members: List[TeamMemberAssignment]
    # c=2 チーム割当用: [(role_key, pokemon_name), ...] ロール順
    team_pokemon: Optional[List[tuple]] = None

    @property
    def total_rank_value(self) -> int:
        return sum(m.rank_value for m in self.members)


@dataclass
class SplitResult:
    teams: List[TeamResult]
    spectators: List[Player] = field(default_factory=list)


class SplitService:
    """SplitMode の仕様を全部実装した完全版サービス"""

    _shared_role_history: ClassVar[Dict[int, Dict[int, List[str]]]] = {}
    _shared_prev_teams: ClassVar[Dict[int, Dict[int, int]]] = {}
    _shared_pair_history: ClassVar[Dict[int, Dict[tuple, int]]] = {}
    _shared_spectator_counts: ClassVar[Dict[int, Dict[int, int]]] = {}
    _shared_last_spectators: ClassVar[Dict[int, set]] = {}

    def __init__(self, lobby_store: LobbyStore) -> None:
        self._lobby_store = lobby_store
        self._pokemon_by_role = self._load_pokemon_list()
        self._all_pokemon_names = [
            name for names in self._pokemon_by_role.values() for name in names
        ]
        # guild_id -> user_id -> [roles...]
        self._role_history = self._shared_role_history
        # guild_id -> user_id -> 直前の試合でのチームindex（prev_match永続化用）
        self._prev_teams = self._shared_prev_teams
        # guild_id -> (min_uid, max_uid) -> 同チーム累積回数
        self._pair_history = self._shared_pair_history
        # guild_id -> user_id -> 観戦累積回数
        self._spectator_counts = self._shared_spectator_counts
        # guild_id -> 直前の試合で観戦だった user_id の集合
        self._last_spectators = self._shared_last_spectators

    @classmethod
    def clear_history_cache(cls, guild_id: int) -> None:
        cls._shared_role_history.pop(guild_id, None)
        cls._shared_prev_teams.pop(guild_id, None)
        cls._shared_pair_history.pop(guild_id, None)
        cls._shared_spectator_counts.pop(guild_id, None)
        cls._shared_last_spectators.pop(guild_id, None)

    def _ensure_history_cache(self, guild_id: int) -> None:
        store = get_stats_store()

        if guild_id not in self._prev_teams:
            prev = store.get_prev_match(guild_id)
            self._prev_teams[guild_id] = (
                {
                    int(uid): tidx
                    for tidx, uids in enumerate(prev)
                    if isinstance(uids, list)
                    for uid in uids
                    if isinstance(uid, int) or (isinstance(uid, str) and uid.isdigit())
                }
                if isinstance(prev, list) else {}
            )

        if guild_id not in self._pair_history:
            raw = store.get_pair_history(guild_id)
            pair_history: Dict[tuple, int] = {}
            for key, value in raw.items():
                if not isinstance(key, str):
                    continue
                parts = key.split('_')
                if len(parts) != 2 or not all(part.isdigit() for part in parts):
                    continue
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                pair_history[(int(parts[0]), int(parts[1]))] = max(0, count)
            self._pair_history[guild_id] = pair_history

        if guild_id not in self._spectator_counts:
            counts, last = store.get_spectator_history(guild_id)
            self._spectator_counts[guild_id] = counts
            self._last_spectators[guild_id] = set(last)
        elif guild_id not in self._last_spectators:
            self._last_spectators[guild_id] = set()

        if guild_id not in self._role_history:
            stored = store.get_role_history(guild_id)
            self._role_history[guild_id] = stored if stored else {}

    def _build_split_context(self, result: SplitResult, mode: SplitMode, cfg) -> dict:
        return {
            'version': 1,
            'mode_code': mode.mode_raw,
            'avoid_count': int(getattr(cfg, 'avoid_count', 0)),
            'spectators': [p.user_id for p in result.spectators],
            'role_assignments': {
                str(mem.user_id): mem.role
                for team in result.teams
                for mem in team.members
            },
        }

    # =======================================================
    # 外部用API（/split run）
    # =======================================================
    def split(self, guild_id: int, mode: SplitMode, team_count: int = 2) -> SplitResult:
        lobby, ranks = self._lobby_store.snapshot(guild_id)
        if len(lobby) < 2:
            raise ValueError("ロビーに2人以上いないとチーム分けできません。")

        players = [
            Player(uid, str(uid), ranks.get(uid, "ビギナー"))
            for uid in lobby
        ]

        store = get_stats_store()
        self._ensure_history_cache(guild_id)

        cfg = get_store().get_split_config(guild_id)
        result = self.preview_split(guild_id, players, mode, cfg, team_count)

        team_uids = [[mem.user_id for mem in team.members] for team in result.teams]
        store.set_last_match(
            guild_id,
            team_uids,
            split_context=self._build_split_context(result, mode, cfg),
        )
        return result

    def commit_split_result(
        self,
        guild_id: int,
        result: SplitResult,
        mode: SplitMode,
        cfg,
    ) -> None:
        teams = [[mem.user_id for mem in team.members] for team in result.teams]
        role_assignments = {
            mem.user_id: mem.role
            for team in result.teams
            for mem in team.members
        }
        self.commit_teams(
            guild_id,
            teams,
            spectators=[p.user_id for p in result.spectators],
            mode=mode,
            cfg=cfg,
            avoid_count=int(getattr(cfg, 'avoid_count', 0)),
            role_assignments=role_assignments,
        )

    def commit_teams_with_stored_context(
        self,
        guild_id: int,
        teams: List[List[int]],
    ) -> bool:
        context = get_stats_store().get_split_context(guild_id, teams)
        return self.commit_teams_from_context(guild_id, teams, context)

    def commit_teams_from_context(
        self,
        guild_id: int,
        teams: List[List[int]],
        context: Optional[dict],
    ) -> bool:
        if not context:
            return False
        if context.get('history_committed') is True:
            return True

        mode = None
        mode_code = context.get('mode_code')
        if isinstance(mode_code, str):
            try:
                mode = SplitMode(mode_code)
            except ValueError:
                mode = None

        try:
            avoid_count = int(context.get('avoid_count', 0))
        except (TypeError, ValueError):
            avoid_count = 0

        raw_spectators = context.get('spectators', [])
        spectators = _ordered_positive_uids(raw_spectators)

        raw_roles = context.get('role_assignments', {})
        role_assignments: Dict[int, str] = {}
        if isinstance(raw_roles, dict):
            for raw_uid, role in raw_roles.items():
                uid = _positive_uid(raw_uid)
                role_name = str(role)
                if uid is not None and role_name in ROLE_KEYS:
                    role_assignments[uid] = role_name

        if not self.commit_teams(
            guild_id,
            teams,
            spectators=spectators,
            mode=mode,
            avoid_count=avoid_count,
            role_assignments=role_assignments,
        ):
            return False
        committed_context = dict(context)
        committed_context['avoid_count'] = avoid_count
        committed_context['spectators'] = spectators
        committed_context['role_assignments'] = {
            str(uid): role for uid, role in role_assignments.items()
        }
        committed_context['history_committed'] = True
        get_stats_store().set_split_context(guild_id, teams, committed_context)
        return True

    def commit_teams(
        self,
        guild_id: int,
        teams: List[List[int]],
        spectators: Optional[List[int]] = None,
        mode: Optional[SplitMode] = None,
        cfg=None,
        avoid_count: Optional[int] = None,
        role_assignments: Optional[Dict[int, str]] = None,
    ) -> bool:
        self._ensure_history_cache(guild_id)
        store = get_stats_store()

        team_uids: List[List[int]] = []
        seen_members: set[int] = set()
        for team in teams:
            cleaned_team: List[int] = []
            for uid in _ordered_positive_uids(team):
                if uid in seen_members:
                    continue
                cleaned_team.append(uid)
                seen_members.add(uid)
            if cleaned_team:
                team_uids.append(cleaned_team)
        if len(team_uids) < 2:
            return False

        self._prev_teams[guild_id] = {
            uid: tidx for tidx, uids in enumerate(team_uids) for uid in uids
        }

        pair_hist = self._pair_history.setdefault(guild_id, {})
        for uids in team_uids:
            for i, uid1 in enumerate(uids):
                for uid2 in uids[i + 1:]:
                    key = (min(uid1, uid2), max(uid1, uid2))
                    pair_hist[key] = pair_hist.get(key, 0) + 1

        spectator_ids = _ordered_positive_uids(list(spectators or []))
        spec_counts = self._spectator_counts.setdefault(guild_id, {})
        for uid in spectator_ids:
            spec_counts[uid] = spec_counts.get(uid, 0) + 1
        self._last_spectators[guild_id] = set(spectator_ids)

        effective_avoid_count = avoid_count
        if effective_avoid_count is None and cfg is not None:
            effective_avoid_count = int(getattr(cfg, 'avoid_count', 0))
        if effective_avoid_count is None:
            effective_avoid_count = 0

        role_history_to_save: Optional[Dict[int, List[str]]] = None
        if (
            mode is not None
            and mode.use_avoid
            and effective_avoid_count > 0
            and role_assignments
        ):
            guild_hist = self._role_history.setdefault(guild_id, {})
            for uid, role in role_assignments.items():
                hist = guild_hist.setdefault(uid, [])
                hist.append(role)
                if len(hist) > effective_avoid_count:
                    del hist[0 : len(hist) - effective_avoid_count]
            role_history_to_save = guild_hist

        store.set_split_history(
            guild_id,
            team_uids,
            {f"{k[0]}_{k[1]}": v for k, v in pair_hist.items()},
            spec_counts,
            list(self._last_spectators[guild_id]),
            role_history=role_history_to_save,
        )
        store.mark_split_history_committed(guild_id, team_uids)
        return True

    def preview_split(
        self,
        guild_id: int,
        players: List[Player],
        mode: SplitMode,
        cfg,
        team_count: int = 2,
        preview_spectator_counts: Optional[Dict[int, int]] = None,
        preview_last_spectators: Optional[set[int]] = None,
    ) -> SplitResult:
        if team_count < 2:
            raise ValueError('team_count must be at least 2')
        if len(players) < team_count:
            raise ValueError('not enough players for requested team count')
        self._ensure_history_cache(guild_id)
        preview_role_history = {
            uid: list(hist)
            for uid, hist in self._role_history.get(guild_id, {}).items()
        }
        return self._split_players(
            guild_id,
            players,
            mode,
            cfg,
            team_count,
            dry_run=True,
            preview_role_history=preview_role_history,
            preview_spectator_counts=preview_spectator_counts,
            preview_last_spectators=preview_last_spectators,
        )

    def get_spectator_history(self, guild_id: int) -> tuple[Dict[int, int], set[int]]:
        self._ensure_history_cache(guild_id)
        return (
            dict(self._spectator_counts.get(guild_id, {})),
            set(self._last_spectators.get(guild_id, set())),
        )

    # =======================================================
    # /split test 用
    # =======================================================
    def split_custom(
        self,
        guild_id: int,
        players: List[Player],
        mode: SplitMode,
        cfg,
        team_count: int = 2,
        dry_run: bool = False,
    ) -> SplitResult:
        return self._split_players(guild_id, players, mode, cfg, team_count, dry_run=dry_run)

    def _player_balance_weight(
        self,
        player: Player,
        use_stats: bool,
        stats_records: dict,
    ) -> float:
        if use_stats:
            return _stats_weight(stats_records.get(player.user_id, {}))
        return float(_rank_weight(player.rank_name))

    def _candidate_pair_score(
        self,
        player: Player,
        team: List[Player],
        pair_hist: Dict[tuple, int],
    ) -> int:
        return sum(
            pair_hist.get((min(player.user_id, pm.user_id), max(player.user_id, pm.user_id)), 0)
            for pm in team
        )

    def _assignment_pair_score(
        self,
        teams: List[List[Player]],
        pair_hist: Dict[tuple, int],
    ) -> int:
        total = 0
        for team in teams:
            for idx, player in enumerate(team):
                for other in team[idx + 1:]:
                    key = (min(player.user_id, other.user_id), max(player.user_id, other.user_id))
                    total += pair_hist.get(key, 0)
        return total

    def _assign_players_greedy(
        self,
        players: List[Player],
        target_sizes: List[int],
        pair_hist: Dict[tuple, int],
        sep_pairs: FrozenSet[tuple],
        balance_active: bool,
        use_stats: bool,
        stats_records: dict,
    ) -> tuple[List[List[Player]], List[float]]:
        team_count = len(target_sizes)
        teams_simple: List[List[Player]] = [[] for _ in range(team_count)]
        weights: List[float] = [0.0] * team_count

        for p in players:
            candidates = [
                i for i in range(team_count)
                if len(teams_simple[i]) < target_sizes[i]
            ]

            scored = []
            for i in candidates:
                sep_violation = sum(
                    1 for pm in teams_simple[i]
                    if (min(p.user_id, pm.user_id), max(p.user_id, pm.user_id)) in sep_pairs
                )
                pair_score = self._candidate_pair_score(p, teams_simple[i], pair_hist)
                scored.append((sep_violation, weights[i], pair_score, len(teams_simple[i]), i))

            min_sep = min(s for s, *_ in scored)
            if min_sep > 0:
                idx = random.choice(candidates)
            else:
                after_sep = [(w, ps, sz, i) for s, w, ps, sz, i in scored if s == 0]
                if balance_active:
                    min_weight = min(w for w, *_ in after_sep)
                    after_weight = [(ps, sz, i) for w, ps, sz, i in after_sep if w == min_weight]
                    min_pair = min(ps for ps, *_ in after_weight)
                    after_pair = [(sz, i) for ps, sz, i in after_weight if ps == min_pair]
                else:
                    min_pair = min(ps for _, ps, *_ in after_sep)
                    after_pair_w = [(w, sz, i) for w, ps, sz, i in after_sep if ps == min_pair]
                    min_weight = min(w for w, *_ in after_pair_w)
                    after_pair = [(sz, i) for w, sz, i in after_pair_w if w == min_weight]
                min_size = min(sz for sz, _ in after_pair)
                best_indices = [i for sz, i in after_pair if sz == min_size]
                idx = random.choice(best_indices)

            teams_simple[idx].append(p)
            weights[idx] += self._player_balance_weight(p, use_stats, stats_records)

        return teams_simple, weights

    def _assign_players_exact_separate(
        self,
        players: List[Player],
        target_sizes: List[int],
        pair_hist: Dict[tuple, int],
        sep_pairs: FrozenSet[tuple],
        balance_active: bool,
        use_stats: bool,
        stats_records: dict,
    ) -> Optional[List[List[Player]]]:
        if not sep_pairs:
            return None

        sep_map: Dict[int, set[int]] = {}
        for uid1, uid2 in sep_pairs:
            sep_map.setdefault(uid1, set()).add(uid2)
            sep_map.setdefault(uid2, set()).add(uid1)

        ordered = list(players)
        random.shuffle(ordered)
        ordered.sort(
            key=lambda p: (
                len(sep_map.get(p.user_id, set())),
                self._player_balance_weight(p, use_stats, stats_records),
            ),
            reverse=True,
        )

        teams: List[List[Player]] = [[] for _ in target_sizes]
        weights = [0.0] * len(target_sizes)
        best: Optional[List[List[Player]]] = None
        best_score: Optional[tuple[float, int, int]] = None
        visited = 0
        aborted = False

        def impossible_to_fill(pos: int) -> bool:
            remaining = len(ordered) - pos
            return any(
                len(teams[idx]) > target_sizes[idx]
                or len(teams[idx]) + remaining < target_sizes[idx]
                for idx in range(len(target_sizes))
            )

        def final_score(candidate: List[List[Player]]) -> tuple[float, int, int]:
            final_weights = [
                sum(self._player_balance_weight(p, use_stats, stats_records) for p in team)
                for team in candidate
            ]
            spread = max(final_weights) - min(final_weights) if final_weights else 0.0
            pair_score = self._assignment_pair_score(candidate, pair_hist)
            size_spread = max(len(team) for team in candidate) - min(len(team) for team in candidate)
            if balance_active:
                return (spread, pair_score, size_spread)
            return (float(pair_score), int(spread), size_spread)

        def dfs(pos: int) -> None:
            nonlocal best, best_score, visited, aborted
            if aborted:
                return
            visited += 1
            if visited > _EXACT_SEPARATE_NODE_LIMIT:
                aborted = True
                best = None
                return
            if impossible_to_fill(pos):
                return
            if pos >= len(ordered):
                if any(len(teams[idx]) != target_sizes[idx] for idx in range(len(target_sizes))):
                    return
                candidate = [list(team) for team in teams]
                score = final_score(candidate)
                if best_score is None or score < best_score:
                    best_score = score
                    best = candidate
                return

            player = ordered[pos]
            blocked = sep_map.get(player.user_id, set())
            candidates = [
                idx
                for idx in range(len(target_sizes))
                if len(teams[idx]) < target_sizes[idx]
                and all(member.user_id not in blocked for member in teams[idx])
            ]
            random.shuffle(candidates)
            if balance_active:
                candidates.sort(
                    key=lambda idx: (
                        weights[idx],
                        self._candidate_pair_score(player, teams[idx], pair_hist),
                        len(teams[idx]),
                    )
                )
            else:
                candidates.sort(
                    key=lambda idx: (
                        self._candidate_pair_score(player, teams[idx], pair_hist),
                        weights[idx],
                        len(teams[idx]),
                    )
                )

            player_weight = self._player_balance_weight(player, use_stats, stats_records)
            for idx in candidates:
                teams[idx].append(player)
                weights[idx] += player_weight
                dfs(pos + 1)
                weights[idx] -= player_weight
                teams[idx].pop()

        dfs(0)
        return None if aborted else best

    # =======================================================
    # 内部メイン
    # =======================================================
    def _split_players(
        self,
        guild_id: int,
        players: List[Player],
        mode: SplitMode,
        cfg,
        team_count: int,
        dry_run: bool = False,
        preview_role_history: Optional[Dict[int, List[str]]] = None,
        preview_spectator_counts: Optional[Dict[int, int]] = None,
        preview_last_spectators: Optional[set[int]] = None,
    ) -> SplitResult:

        # 0) 11人以上の場合、超過分を観戦者に（連続回避・観戦回数の少ない順で公平に選出）
        max_players = team_count * 5
        spectators: List[Player] = []
        if len(players) > max_players:
            n_spec = len(players) - max_players
            if dry_run and preview_spectator_counts is not None:
                spec_counts = preview_spectator_counts
            else:
                spec_counts = self._spectator_counts.get(guild_id, {})
            if dry_run and preview_last_spectators is not None:
                last_specs = preview_last_spectators
            else:
                last_specs = self._last_spectators.get(guild_id, set())

            # 直前に観戦した人は今回はプレイ優先（連続観戦NG）
            can_spec = [p for p in players if p.user_id not in last_specs]
            must_play = [p for p in players if p.user_id in last_specs]

            if len(can_spec) < n_spec:
                # 連続回避を満たせない場合はやむを得ずmust_playからも選ぶ
                random.shuffle(must_play)
                can_spec += must_play[:n_spec - len(can_spec)]
                must_play = must_play[n_spec - len(can_spec) + len(must_play):]

            # 観戦回数が少ない順に選ぶ（同数はランダム）
            random.shuffle(can_spec)
            can_spec.sort(key=lambda p: spec_counts.get(p.user_id, 0))
            spectators = can_spec[:n_spec]
            spec_set = {p.user_id for p in spectators}
            players = [p for p in players if p.user_id not in spec_set]

        # 1) バランス方式に応じてソート
        stats_records: dict = {}
        if mode.use_stats_balance:
            stats_records = get_stats_store().get_all_records(guild_id)
        elif mode.use_daily_stats_balance:
            stats_records = get_stats_store().get_daily_records(guild_id)

        use_stats = mode.use_stats_balance or mode.use_daily_stats_balance
        if mode.use_rank_balance or use_stats:
            # 一度シャッフルしてからソートすると、同重みの順番にゆらぎが出る
            base = list(players)
            random.shuffle(base)
            if use_stats:
                base.sort(
                    key=lambda p: _stats_weight(stats_records.get(p.user_id, {})),
                    reverse=True,
                )
            else:
                base.sort(key=lambda p: _rank_weight(p.rank_name), reverse=True)
        else:
            base = list(players)
            random.shuffle(base)

        # チーム分配
        # まず「各チームの目標人数」を決める（できるだけ均等）
        total = len(base)
        base_size = total // team_count          # だいたいの人数
        remainder = total % team_count           # 余りを前のチームから +1 していく

        target_sizes = [
            base_size + (1 if i < remainder else 0)
            for i in range(team_count)
        ]

        # ペア同チーム累積回数（なければ空）
        pair_hist = self._pair_history.get(guild_id, {})

        # 分離ペアを (min_uid, max_uid) の集合として保持
        sep_pairs: FrozenSet[tuple] = cfg.separate_pairs

        # 分離ペアに含まれる人を先に処理する（チームが偏って詰まる前に配置するため）
        if sep_pairs:
            sep_uids = {uid for pair in sep_pairs for uid in pair}
            base = [p for p in base if p.user_id in sep_uids] + \
                   [p for p in base if p.user_id not in sep_uids]

        # バランスモード有効時は重み差を優先、無効時はペア累積を優先
        balance_active = mode.use_rank_balance or use_stats

        teams_simple = (
            self._assign_players_exact_separate(
                base,
                target_sizes,
                pair_hist,
                sep_pairs,
                balance_active,
                use_stats,
                stats_records,
            )
            if sep_pairs else None
        )
        if teams_simple is None:
            teams_simple, _ = self._assign_players_greedy(
                base,
                target_sizes,
                pair_hist,
                sep_pairs,
                balance_active,
                use_stats,
                stats_records,
            )
        # 2) ロール割当
        final_roles: Dict[int, str] = {}
        if preview_role_history is not None:
            guild_hist = {
                uid: list(hist)
                for uid, hist in preview_role_history.items()
            }
        elif dry_run:
            guild_hist = {}
        else:
            self._ensure_history_cache(guild_id)
            guild_hist = self._role_history.setdefault(guild_id, {})

        for tidx, members in enumerate(teams_simple):
            roles = self._assign_roles_for_team(len(members), mode, cfg)
            use_avoid = mode.use_avoid and cfg.avoid_count > 0

            if mode.role_balance_mode in (1, 2) and use_avoid:
                # 固定ロール構成 (b=1 or b=2) + 連続回避: DFS で最適割当
                assignment = self._assign_roles_with_avoid(
                    members, roles, guild_hist, cfg.avoid_count
                )
                for p in members:
                    final_roles[p.user_id] = assignment[p.user_id]
            else:
                random.shuffle(roles)
                for p, role in zip(members, roles):
                    if use_avoid:
                        hist = guild_hist.setdefault(p.user_id, [])
                        banned_roles = set(hist[-cfg.avoid_count:])
                        if role in banned_roles:
                            # mode=0 はロール自由なので banned を除いた全ロールから選ぶ
                            alternatives = [r for r in ROLE_KEYS if r not in banned_roles]
                            if alternatives:
                                role = random.choice(alternatives)
                        hist.append(role)
                        if len(hist) > cfg.avoid_count:
                            del hist[0]
                    final_roles[p.user_id] = role

        # 3) ポケモン割当
        assigned_poke: Dict[int, Optional[str]] = {p.user_id: None for p in players}
        team_pokemon_sets: List[Optional[List[tuple]]] = [None] * team_count

        allow_cross = mode.allow_cross_dup
        used_global: set[str] = set()
        used_team: List[set[str]] = [set() for _ in range(team_count)]

        if mode.pokemon_assign_mode == 1:
            # 個人割当: ロールに合ったポケモンを1人1匹ずつ
            for tidx, members in enumerate(teams_simple):
                for p in members:
                    role = final_roles[p.user_id]
                    poke = self._assign_pokemon(
                        tidx, role, allow_cross, used_global, used_team, cfg.banned_pokemon
                    )
                    assigned_poke[p.user_id] = poke or None

        elif mode.pokemon_assign_mode == 2:
            # チーム割当: チームごとに全5ロール各1匹のセットを生成
            for tidx in range(team_count):
                team_set: List[tuple] = []
                for role in ROLE_KEYS:
                    poke = self._assign_pokemon(
                        tidx, role, allow_cross, used_global, used_team, cfg.banned_pokemon
                    )
                    if poke:
                        team_set.append((role, poke))
                team_pokemon_sets[tidx] = team_set if team_set else None

        # 4) TeamResult へ構築
        team_results: List[TeamResult] = []
        for tidx, members in enumerate(teams_simple):
            arr: List[TeamMemberAssignment] = []
            for p in members:
                rv = _rank_weight(p.rank_name)
                arr.append(
                    TeamMemberAssignment(
                        user_id=p.user_id,
                        name=p.name,
                        role=final_roles[p.user_id],
                        pokemon=assigned_poke[p.user_id],
                        rank_name=p.rank_name,
                        rank_value=rv,
                    )
                )
            team_results.append(TeamResult(arr, team_pokemon=team_pokemon_sets[tidx]))

        return SplitResult(team_results, spectators=spectators)

    # =======================================================
    # ロール割当
    # =======================================================
    def _assign_roles_for_team(self, size: int, mode: SplitMode, cfg) -> List[str]:
        if size <= 0:
            return []

        # 0 = 無視（全部ランダム）
        if mode.role_balance_mode == 0:
            return [random.choice(ROLE_KEYS) for _ in range(size)]

        # 1 = 自動（1チーム5人を仮定する ATK/ALL/SPD/DEF/SUP を均等）
        if mode.role_balance_mode == 1:
            roles: List[str] = []
            while len(roles) < size:
                roles.extend(ROLE_KEYS)
            return roles[:size]

        # 2 = /config で指定
        targets = cfg.role_balance_targets
        roles: List[str] = []
        for key in ROLE_KEYS:
            roles.extend([key] * max(0, int(targets.get(key, 0))))

        # 足りない分を補う
        if not roles:
            # 設定全部0 → 自動扱い
            return self._assign_roles_for_team(size, SplitMode("x1xxx"), cfg)

        if len(roles) < size:
            while len(roles) < size:
                for key in ROLE_KEYS:
                    if len(roles) >= size:
                        break
                    if targets.get(key, 0) > 0:
                        roles.append(key)
        if len(roles) > size:
            roles = roles[:size]

        return roles

    def _assign_roles_with_avoid(
        self,
        members: List[Player],
        roles: List[str],
        guild_hist: Dict[int, List[str]],
        avoid_count: int,
    ) -> Dict[int, str]:
        """各ロール1個ずつ固定のまま、avoid を考慮してメンバーに割り当てる。

        - roles: 例えば ["attacker","all_rounder","speedster","defender","supporter"]
        - members: チームメンバー
        - guild_hist: guild_id ごとのロール履歴 (user_id -> [roles...])
        - avoid_count: 直近何回分のロールを避けたいか
        """
        n = len(members)
        idx_list = list(range(len(roles)))

        # DFS で割当パターンを探す（人数 max 5 なので全探索で十分）
        def dfs(
            i: int,
            used: set[int],
            mapping: Dict[int, str],
        ) -> Optional[Dict[int, str]]:
            if i >= n:
                return mapping

            p = members[i]
            hist = guild_hist.get(p.user_id, [])
            banned = set(hist[-avoid_count:]) if avoid_count > 0 else set()

            candidates = idx_list[:]
            random.shuffle(candidates)  # 毎回違う割り当てになるようにランダム順

            for ri in candidates:
                if ri in used:
                    continue
                role = roles[ri]
                if role in banned:
                    continue

                used.add(ri)
                mapping[p.user_id] = role
                res = dfs(i + 1, used, mapping)
                if res is not None:
                    return res
                used.remove(ri)
                del mapping[p.user_id]

            return None  # この並びでは無理

        result = dfs(0, set(), {})
        if result is None:
            # 完全には避けきれない場合 → avoid無視でランダム割当（各ロール1個ずつは守る）
            shuffled_roles = roles[:]
            random.shuffle(shuffled_roles)
            result = {p.user_id: r for p, r in zip(members, shuffled_roles)}

        # 履歴更新（直近 avoid_count 件だけ残す）
        for p in members:
            role = result[p.user_id]
            hist = guild_hist.setdefault(p.user_id, [])
            hist.append(role)
            if avoid_count > 0 and len(hist) > avoid_count:
                # 末尾 avoid_count 件だけ残す
                del hist[0 : len(hist) - avoid_count]

        return result

    # =======================================================
    # ポケモン割当
    # =======================================================
    def _load_pokemon_list(self) -> Dict[str, List[str]]:
        path = data_path('pokemon_list.yaml')
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            try:
                text = resources.files('unitechan').joinpath('data/pokemon_list.yaml').read_text(
                    encoding="utf-8"
                )
            except FileNotFoundError:
                return {}
        data = yaml.safe_load(text) or {}
        return {key: [str(v) for v in data.get(key, [])] for key in ROLE_KEYS}

    def get_all_pokemon_names(self) -> List[str]:
        """全ポケモン名のリストを返す（autocomplete用）"""
        return list(self._all_pokemon_names)

    def _assign_pokemon(
        self,
        team_idx: int,
        role_key: str,
        allow_cross_dup: bool,
        used_global: set,
        used_team: List[set],
        banned: frozenset,
    ) -> str:
        pool = [p for p in self._pokemon_by_role.get(role_key, []) if p not in banned]
        if not pool:
            return ""

        # 候補構築
        candidates: List[str] = []
        for name in pool:
            if name in used_team[team_idx]:
                continue
            if not allow_cross_dup and name in used_global:
                continue
            candidates.append(name)

        # team 内だけ緩和
        if not candidates:
            for name in pool:
                if name not in used_team[team_idx]:
                    candidates.append(name)

        # 完全に自由
        if not candidates:
            candidates = pool

        chosen = random.choice(candidates)
        used_team[team_idx].add(chosen)
        if not allow_cross_dup:
            used_global.add(chosen)
        return chosen
