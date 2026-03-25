from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class StatsStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path: Path = path or Path('data/stats_state.json')
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except Exception:
            return
        if isinstance(raw, dict):
            self._data = raw

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _ensure_guild(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self._data:
            self._data[gid] = {}
        return self._data[gid]

    # ---- 直前の試合 ----

    def set_last_match(self, guild_id: int, teams: List[List[int]]) -> None:
        """チーム分け直後に呼び出す。teams = [[team_a_uids], [team_b_uids]]"""
        g = self._ensure_guild(guild_id)
        g['last_match'] = [list(team) for team in teams]
        self._save()

    def get_last_match(self, guild_id: int) -> Optional[List[List[int]]]:
        return self._ensure_guild(guild_id).get('last_match')

    def clear_last_match(self, guild_id: int) -> None:
        self._ensure_guild(guild_id).pop('last_match', None)
        self._save()

    # ---- 結果記録 ----

    def record_result(
        self, guild_id: int, winning_team_idx: int
    ) -> Tuple[List[int], List[int]]:
        """勝利チームのインデックスを受け取り戦績を更新。
        (winners, losers) の user_id リストを返す。
        last_match は記録後に消去する。"""
        g = self._ensure_guild(guild_id)
        last = g.get('last_match')
        if not last or winning_team_idx >= len(last):
            return [], []

        winners = last[winning_team_idx]
        losers = [uid for i, team in enumerate(last) for uid in team if i != winning_team_idx]

        records = g.setdefault('records', {})
        for uid in winners:
            r = records.setdefault(str(uid), {'wins': 0, 'losses': 0})
            r['wins'] += 1
        for uid in losers:
            r = records.setdefault(str(uid), {'wins': 0, 'losses': 0})
            r['losses'] += 1

        del g['last_match']
        self._save()
        return winners, losers

    # ---- 戦績参照 ----

    def get_record(self, guild_id: int, user_id: int) -> Dict[str, int]:
        g = self._ensure_guild(guild_id)
        return dict(g.get('records', {}).get(str(user_id), {'wins': 0, 'losses': 0}))

    def get_all_records(self, guild_id: int) -> Dict[int, Dict[str, int]]:
        g = self._ensure_guild(guild_id)
        return {int(uid): dict(r) for uid, r in g.get('records', {}).items()}

    def reset_stats(self, guild_id: int) -> int:
        """戦績をリセット。削除したプレイヤー数を返す。"""
        g = self._ensure_guild(guild_id)
        count = len(g.get('records', {}))
        g.pop('records', None)
        self._save()
        return count


_STORE: Optional[StatsStore] = None


def get_stats_store() -> StatsStore:
    global _STORE
    if _STORE is None:
        _STORE = StatsStore()
    return _STORE
