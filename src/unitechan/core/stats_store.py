from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_JST = timezone(timedelta(hours=9))
_DAY_RESET_HOUR = 5  # 05:00 JST で日付切替


def _today_jst() -> str:
    """JST 05:00 を境に日付を返す（05:00未満は前日扱い）。"""
    now = datetime.now(_JST)
    if now.hour < _DAY_RESET_HOUR:
        now = now - timedelta(days=1)
    return now.strftime('%Y-%m-%d')


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

        today = _today_jst()
        daily = g.setdefault('daily_records', {}).setdefault(today, {})
        for uid in winners:
            r = daily.setdefault(str(uid), {'wins': 0, 'losses': 0})
            r['wins'] += 1
        for uid in losers:
            r = daily.setdefault(str(uid), {'wins': 0, 'losses': 0})
            r['losses'] += 1

        del g['last_match']
        g['last_result'] = {'winners': winners, 'losers': losers, 'date': today}
        self._save()
        return winners, losers

    def undo_last_result(self, guild_id: int) -> bool:
        """直前の record_result を取り消す。成功したら True を返す。"""
        g = self._ensure_guild(guild_id)
        last = g.pop('last_result', None)
        if not last:
            return False

        records = g.get('records', {})
        for uid in last['winners']:
            r = records.get(str(uid))
            if r:
                r['wins'] = max(0, r['wins'] - 1)
        for uid in last['losers']:
            r = records.get(str(uid))
            if r:
                r['losses'] = max(0, r['losses'] - 1)

        date = last.get('date', _today_jst())
        daily = g.get('daily_records', {}).get(date, {})
        for uid in last['winners']:
            r = daily.get(str(uid))
            if r:
                r['wins'] = max(0, r['wins'] - 1)
        for uid in last['losers']:
            r = daily.get(str(uid))
            if r:
                r['losses'] = max(0, r['losses'] - 1)

        self._save()
        return True

    # ---- チーム履歴・ロール履歴（チーム分け品質向上用） ----

    def set_prev_match(self, guild_id: int, teams: List[List[int]]) -> None:
        """直前のチーム構成を保存。last_match と違い record_result では消さない。"""
        g = self._ensure_guild(guild_id)
        g['prev_match'] = [list(team) for team in teams]
        self._save()

    def get_prev_match(self, guild_id: int) -> Optional[List[List[int]]]:
        return self._ensure_guild(guild_id).get('prev_match')

    def set_role_history(self, guild_id: int, hist: Dict[int, List[str]]) -> None:
        g = self._ensure_guild(guild_id)
        g['role_history'] = {str(uid): list(roles) for uid, roles in hist.items()}
        self._save()

    def get_role_history(self, guild_id: int) -> Dict[int, List[str]]:
        g = self._ensure_guild(guild_id)
        return {int(uid): list(roles) for uid, roles in g.get('role_history', {}).items()}

    # ---- 戦績参照 ----

    def get_record(self, guild_id: int, user_id: int) -> Dict[str, int]:
        g = self._ensure_guild(guild_id)
        return dict(g.get('records', {}).get(str(user_id), {'wins': 0, 'losses': 0}))

    def get_daily_records(self, guild_id: int, date_str: Optional[str] = None) -> Dict[int, Dict[str, int]]:
        """指定日（省略時は今日JST）の戦績を返す。"""
        if date_str is None:
            date_str = _today_jst()
        g = self._ensure_guild(guild_id)
        return {
            int(uid): dict(r)
            for uid, r in g.get('daily_records', {}).get(date_str, {}).items()
        }

    def get_all_records(self, guild_id: int) -> Dict[int, Dict[str, int]]:
        g = self._ensure_guild(guild_id)
        return {int(uid): dict(r) for uid, r in g.get('records', {}).items()}

    def reset_stats(self, guild_id: int) -> int:
        """戦績をリセット。削除したプレイヤー数を返す。"""
        g = self._ensure_guild(guild_id)
        count = len(g.get('records', {}))
        g.pop('records', None)
        g.pop('last_result', None)
        g.pop('daily_records', None)
        self._save()
        return count


_STORE: Optional[StatsStore] = None


def get_stats_store() -> StatsStore:
    global _STORE
    if _STORE is None:
        _STORE = StatsStore()
    return _STORE
