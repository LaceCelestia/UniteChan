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

    _MATCH_HISTORY_MAX = 5

    def set_last_match(self, guild_id: int, teams: List[List[int]]) -> None:
        """チーム分け直後に呼び出す。teams = [[team_a_uids], [team_b_uids]]"""
        g = self._ensure_guild(guild_id)
        # 上書き前の last_match を履歴に積む
        if 'last_match' in g:
            hist = g.setdefault('match_history', [])
            hist.insert(0, g['last_match'])
            if len(hist) > self._MATCH_HISTORY_MAX:
                del hist[self._MATCH_HISTORY_MAX:]
        g['last_match'] = [list(team) for team in teams]
        self._save()

    def get_last_match(self, guild_id: int) -> Optional[List[List[int]]]:
        return self._ensure_guild(guild_id).get('last_match')

    def get_match_history(self, guild_id: int) -> List[List[List[int]]]:
        """過去の試合履歴を新しい順で返す（最大 _MATCH_HISTORY_MAX 件）。"""
        return list(self._ensure_guild(guild_id).get('match_history', []))

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
        winners, losers = self._apply_result(guild_id, last, winning_team_idx)
        del self._ensure_guild(guild_id)['last_match']
        self._save()
        return winners, losers

    def record_result_for_teams(
        self, guild_id: int, teams: List[List[int]], winning_team_idx: int
    ) -> Tuple[List[int], List[int]]:
        """チームを直接指定して戦績を更新（last_match を使わない）。"""
        if not teams or winning_team_idx >= len(teams):
            return [], []
        return self._apply_result(guild_id, teams, winning_team_idx)

    def _apply_result(
        self, guild_id: int, teams: List[List[int]], winning_team_idx: int
    ) -> Tuple[List[int], List[int]]:
        g = self._ensure_guild(guild_id)
        winners = teams[winning_team_idx]
        losers = [uid for i, team in enumerate(teams) for uid in team if i != winning_team_idx]

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

        g['last_result'] = {
            'winners': list(winners),
            'losers': list(losers),
            'teams': [list(team) for team in teams],
            'date': today,
        }
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

        teams = last.get('teams')
        if 'last_match' not in g and isinstance(teams, list):
            restored = [list(team) for team in teams if isinstance(team, list)]
            if restored:
                g['last_match'] = restored

        self._save()
        return True

    def get_last_result(self, guild_id: int) -> Optional[dict]:
        last = self._ensure_guild(guild_id).get('last_result')
        if not isinstance(last, dict):
            return None
        return {
            'winners': list(last.get('winners', [])),
            'losers': list(last.get('losers', [])),
            'teams': [list(team) for team in last.get('teams', []) if isinstance(team, list)],
            'date': last.get('date'),
        }

    def undo_last_result_if_matches(
        self,
        guild_id: int,
        winners: List[int],
        losers: List[int],
    ) -> bool:
        last = self.get_last_result(guild_id)
        if not last:
            return False
        if sorted(last.get('winners', [])) != sorted(winners):
            return False
        if sorted(last.get('losers', [])) != sorted(losers):
            return False
        return self.undo_last_result(guild_id)

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

    def set_pair_history(self, guild_id: int, hist: Dict[str, int]) -> None:
        """ペアごとの同チーム累積回数を保存。key = "uid1_uid2" (uid1 < uid2)"""
        g = self._ensure_guild(guild_id)
        g['pair_history'] = dict(hist)
        self._save()

    def get_pair_history(self, guild_id: int) -> Dict[str, int]:
        g = self._ensure_guild(guild_id)
        return dict(g.get('pair_history', {}))

    def set_spectator_history(
        self, guild_id: int, counts: Dict[int, int], last: List[int]
    ) -> None:
        """観戦回数と直前の観戦者リストを保存。"""
        g = self._ensure_guild(guild_id)
        g['spectator_counts'] = {str(uid): c for uid, c in counts.items()}
        g['last_spectators'] = list(last)
        self._save()

    def get_spectator_history(self, guild_id: int) -> tuple:
        """(counts: Dict[int,int], last_spectators: List[int]) を返す。"""
        g = self._ensure_guild(guild_id)
        counts = {int(uid): c for uid, c in g.get('spectator_counts', {}).items()}
        last = [int(uid) for uid in g.get('last_spectators', [])]
        return counts, last

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

    def export_stats(self, guild_id: int) -> dict:
        """戦績データをエクスポート用dictで返す。"""
        g = self._ensure_guild(guild_id)
        return {
            'version': 1,
            'records': {uid: dict(r) for uid, r in g.get('records', {}).items()},
            'daily_records': {
                date: {uid: dict(r) for uid, r in day.items()}
                for date, day in g.get('daily_records', {}).items()
            },
        }

    def merge_stats(self, guild_id: int, data: dict) -> Dict[str, int]:
        """エクスポートデータをマージする。追加した勝数・負数の合計を返す。"""
        if data.get('version') != 1:
            raise ValueError('対応していないフォーマットです。')

        g = self._ensure_guild(guild_id)
        added_wins = 0
        added_losses = 0

        # 通算戦績マージ
        records = g.setdefault('records', {})
        for uid, r in data.get('records', {}).items():
            existing = records.setdefault(uid, {'wins': 0, 'losses': 0})
            existing['wins'] += r.get('wins', 0)
            existing['losses'] += r.get('losses', 0)
            added_wins += r.get('wins', 0)
            added_losses += r.get('losses', 0)

        # 日次戦績マージ
        daily_records = g.setdefault('daily_records', {})
        for date, day in data.get('daily_records', {}).items():
            daily_day = daily_records.setdefault(date, {})
            for uid, r in day.items():
                existing = daily_day.setdefault(uid, {'wins': 0, 'losses': 0})
                existing['wins'] += r.get('wins', 0)
                existing['losses'] += r.get('losses', 0)

        self._save()
        return {'added_wins': added_wins, 'added_losses': added_losses}

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
