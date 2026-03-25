from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
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
        # guild_id -> user_id -> 直前の試合でのチームindex
        self._prev_teams: Dict[int, Dict[int, int]] = {}

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

        cfg = get_store().get_split_config(guild_id)
        result = self.split_custom(guild_id, players, mode, cfg, team_count)

        team_uids = [[mem.user_id for mem in team.members] for team in result.teams]
        self._prev_teams[guild_id] = {
            uid: tidx for tidx, uids in enumerate(team_uids) for uid in uids
        }
        get_stats_store().set_last_match(guild_id, team_uids)

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
    ) -> SplitResult:
        return self._split_players(guild_id, players, mode, cfg, team_count)

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
    ) -> SplitResult:

        # 1) ランクバランス or ランダム
        if mode.use_rank_balance:
            # 一度シャッフルしてからランク順ソートすると、
            # 同ランク帯の順番にゆらぎが出る
            base = list(players)
            random.shuffle(base)
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

        # 前回チーム履歴（なければ空）
        prev_teams = self._prev_teams.get(guild_id, {})

        for p in base:
            candidates = [
                i for i in range(team_count)
                if len(teams_simple[i]) < target_sizes[i]
            ]

            # スコア: (前回同チーム人数, 総ランク値, 人数)
            # → 前回同じチームだった人が少ないほど優先、次にランクバランス
            p_prev = prev_teams.get(p.user_id)
            scored = []
            for i in candidates:
                same_prev = (
                    sum(1 for pm in teams_simple[i] if prev_teams.get(pm.user_id) == p_prev)
                    if p_prev is not None else 0
                )
                scored.append((same_prev, weights[i], len(teams_simple[i]), i))

            min_same = min(s for s, *_ in scored)
            after_same = [(w, sz, i) for s, w, sz, i in scored if s == min_same]
            min_weight = min(w for w, _, _ in after_same)
            after_weight = [(sz, i) for w, sz, i in after_same if w == min_weight]
            min_size = min(sz for sz, _ in after_weight)
            best_indices = [i for sz, i in after_weight if sz == min_size]
            idx = random.choice(best_indices)

            teams_simple[idx].append(p)
            weights[idx] += _rank_weight(p.rank_name)

        # 2) ロール割当
        final_roles: Dict[int, str] = {}
        guild_hist = self._role_history.setdefault(guild_id, {})

        for tidx, members in enumerate(teams_simple):
            roles = self._assign_roles_for_team(len(members), mode, cfg)
            use_avoid = mode.use_avoid and cfg.avoid_count > 0

            # b=1（ロール自動）のときは、
            #   ロール構成は固定（ATK/ALL/SPD/DEF/SUP 1個ずつ）
            #   「誰にどのロールを割り当てるか」だけを avoid を考慮して決める
            if mode.role_balance_mode == 1 and use_avoid:
                assignment = self._assign_roles_with_avoid(
                    members, roles, guild_hist, cfg.avoid_count
                )
                for p in members:
                    final_roles[p.user_id] = assignment[p.user_id]
            else:
                # それ以外は従来通り（ロールをシャッフルして割当）
                random.shuffle(roles)
                for p, role in zip(members, roles):
                    if use_avoid:
                        hist = guild_hist.setdefault(p.user_id, [])
                        if hist and hist[-1] == role:
                            role = self._next_role(role)
                        hist.append(role)
                        if len(hist) > cfg.avoid_count:
                            del hist[0]
                    final_roles[p.user_id] = role

        # 3) ポケモン割当
        assigned_poke: Dict[int, Optional[str]] = {p.user_id: None for p in players}
        if mode.pokemon_assign_mode in (1, 2):
            allow_cross = mode.allow_cross_dup
            used_global: set[str] = set()
            used_team: List[set[str]] = [set() for _ in range(team_count)]

            for tidx, members in enumerate(teams_simple):
                for p in members:
                    role = final_roles[p.user_id]
                    assigned_poke[p.user_id] = self._assign_pokemon(
                        tidx, role, allow_cross, used_global, used_team, cfg.banned_pokemon
                    )

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
            team_results.append(TeamResult(arr))

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

    def _next_role(self, role: str) -> str:
        try:
            idx = ROLE_KEYS.index(role)
        except ValueError:
            return role
        return ROLE_KEYS[(idx + 1) % len(ROLE_KEYS)]

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
