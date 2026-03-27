from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional
from pathlib import Path
import random

import yaml

from .lobby_store import LobbyStore
from .split_mode import SplitMode
from .config_store import get_store
from .stats_store import get_stats_store

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
ROLE_KEYS = ["attacker", "all_rounder", "speedster", "defender", "supporter"]

# ロール3文字コード
ROLE_CODE = {
    "attacker": "ATK",
    "all_rounder": "ALL",
    "speedster": "SPD",
    "defender": "DEF",
    "supporter": "SUP",
}


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


class SplitService:
    """SplitMode の仕様を全部実装した完全版サービス"""

    def __init__(self, lobby_store: LobbyStore) -> None:
        self._lobby_store = lobby_store
        self._pokemon_by_role = self._load_pokemon_list()
        # guild_id -> user_id -> [roles...]
        self._role_history: Dict[int, Dict[int, List[str]]] = {}
        # guild_id -> user_id -> 直前の試合でのチームindex（prev_match永続化用）
        self._prev_teams: Dict[int, Dict[int, int]] = {}
        # guild_id -> (min_uid, max_uid) -> 同チーム累積回数
        self._pair_history: Dict[int, Dict[tuple, int]] = {}

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

        # 再起動後に備えてディスクから履歴を遅延ロード
        if guild_id not in self._prev_teams:
            prev = store.get_prev_match(guild_id)
            if prev:
                self._prev_teams[guild_id] = {
                    uid: tidx for tidx, uids in enumerate(prev) for uid in uids
                }

        if guild_id not in self._pair_history:
            raw = store.get_pair_history(guild_id)
            self._pair_history[guild_id] = {
                (int(k.split('_')[0]), int(k.split('_')[1])): v
                for k, v in raw.items()
            }

        cfg = get_store().get_split_config(guild_id)
        result = self.split_custom(guild_id, players, mode, cfg, team_count)

        team_uids = [[mem.user_id for mem in team.members] for team in result.teams]
        self._prev_teams[guild_id] = {
            uid: tidx for tidx, uids in enumerate(team_uids) for uid in uids
        }

        # ペア履歴を更新
        pair_hist = self._pair_history.setdefault(guild_id, {})
        for uids in team_uids:
            for i, uid1 in enumerate(uids):
                for uid2 in uids[i + 1:]:
                    key = (min(uid1, uid2), max(uid1, uid2))
                    pair_hist[key] = pair_hist.get(key, 0) + 1

        store.set_last_match(guild_id, team_uids)
        store.set_prev_match(guild_id, team_uids)
        store.set_pair_history(guild_id, {f"{k[0]}_{k[1]}": v for k, v in pair_hist.items()})

        # ロール履歴もディスクに永続化
        if guild_id in self._role_history:
            store.set_role_history(guild_id, self._role_history[guild_id])

        return result

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
    ) -> SplitResult:

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
        teams_simple: List[List[Player]] = [[] for _ in range(team_count)]
        weights = [0] * team_count

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

        for p in base:
            candidates = [
                i for i in range(team_count)
                if len(teams_simple[i]) < target_sizes[i]
            ]

            # スコア: (分離ペア違反数, ペア累積同チーム数, 総ランク重み, 人数)
            # → 分離ペア違反が少ない → ペア累積が少ない → ランクバランス の順で優先
            scored = []
            for i in candidates:
                sep_violation = sum(
                    1 for pm in teams_simple[i]
                    if (min(p.user_id, pm.user_id), max(p.user_id, pm.user_id)) in sep_pairs
                )
                pair_score = sum(
                    pair_hist.get((min(p.user_id, pm.user_id), max(p.user_id, pm.user_id)), 0)
                    for pm in teams_simple[i]
                )
                scored.append((sep_violation, pair_score, weights[i], len(teams_simple[i]), i))

            min_sep = min(s for s, *_ in scored)
            if min_sep > 0:
                # 全チームで分離違反が避けられない → 毎回違うペアになるようランダム割当
                idx = random.choice(candidates)
            else:
                after_sep = [(ps, w, sz, i) for s, ps, w, sz, i in scored if s == 0]
                min_pair = min(ps for ps, *_ in after_sep)
                after_pair = [(w, sz, i) for ps, w, sz, i in after_sep if ps == min_pair]
                min_weight = min(w for w, _, _ in after_pair)
                after_weight = [(sz, i) for w, sz, i in after_pair if w == min_weight]
                min_size = min(sz for sz, _ in after_weight)
                best_indices = [i for sz, i in after_weight if sz == min_size]
                idx = random.choice(best_indices)

            teams_simple[idx].append(p)
            if use_stats:
                weights[idx] += _stats_weight(stats_records.get(p.user_id, {}))
            else:
                weights[idx] += _rank_weight(p.rank_name)

        # 2) ロール割当
        final_roles: Dict[int, str] = {}
        # dry_run（テスト実行）の場合は使い捨てのdictを使い、本番の履歴を汚染しない
        if not dry_run and guild_id not in self._role_history:
            stored = get_stats_store().get_role_history(guild_id)
            if stored:
                self._role_history[guild_id] = stored
        guild_hist = {} if dry_run else self._role_history.setdefault(guild_id, {})

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

        return SplitResult(team_results)

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
        path = Path("data/pokemon_list.yaml")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}
        return {key: [str(v) for v in data.get(key, [])] for key in ROLE_KEYS}

    def get_all_pokemon_names(self) -> List[str]:
        """全ポケモン名のリストを返す（autocomplete用）"""
        return [name for names in self._pokemon_by_role.values() for name in names]

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
