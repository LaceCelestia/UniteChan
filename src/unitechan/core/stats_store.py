from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .paths import data_path

_JST = timezone(timedelta(hours=9))
_DAY_RESET_HOUR = 5  # 05:00 JST で日付切替


def _today_jst() -> str:
    """JST 05:00 を境に日付を返す（05:00未満は前日扱い）。"""
    now = datetime.now(_JST)
    if now.hour < _DAY_RESET_HOUR:
        now = now - timedelta(days=1)
    return now.strftime('%Y-%m-%d')


def _safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_uid(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _clean_record(value: object) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {'wins': 0, 'losses': 0}
    return {
        'wins': max(0, _safe_int(value.get('wins'), 0)),
        'losses': max(0, _safe_int(value.get('losses'), 0)),
    }


def _clean_record_map(value: object) -> Dict[int, Dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    result: Dict[int, Dict[str, int]] = {}
    for uid, record in value.items():
        parsed_uid = _safe_uid(uid)
        if parsed_uid is None or not isinstance(record, dict):
            continue
        result[parsed_uid] = _clean_record(record)
    return result


class StatsStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path: Path = path or data_path('stats_state.json')
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise RuntimeError(f'failed to load stats state: {self._path}') from exc
        if isinstance(raw, dict):
            self._data = raw
        else:
            raise RuntimeError(f'invalid stats state format: {self._path}')

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f'{self._path.name}.tmp')
        tmp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        tmp_path.replace(self._path)

    def _ensure_guild(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if not isinstance(self._data.get(gid), dict):
            self._data[gid] = {}
        return self._data[gid]

    # ---- 直前の試合 ----

    _MATCH_HISTORY_MAX = 5

    def _normalize_teams(self, teams: List[List[int]]) -> List[List[int]]:
        if not isinstance(teams, list):
            return []
        normalized: List[List[int]] = []
        seen: set[int] = set()
        for team in teams:
            if not isinstance(team, list):
                continue
            members: List[int] = []
            for uid in team:
                parsed_uid = _safe_uid(uid)
                if parsed_uid is not None and parsed_uid not in seen:
                    members.append(parsed_uid)
                    seen.add(parsed_uid)
            if members:
                normalized.append(members)
        return normalized

    def _is_valid_match(self, teams: List[List[int]]) -> bool:
        return len(teams) >= 2

    def _match_key(self, teams: List[List[int]]) -> str:
        return json.dumps(self._normalize_teams(teams), separators=(',', ':'))

    def _copy_split_context(self, context: object) -> Optional[dict]:
        return dict(context) if isinstance(context, dict) else None

    def _remember_match(self, g: dict, teams: List[List[int]]) -> None:
        normalized = self._normalize_teams(teams)
        if not self._is_valid_match(normalized):
            return
        raw_history = g.get('match_history', [])
        if not isinstance(raw_history, list):
            raw_history = []
        hist: List[List[List[int]]] = []
        for item in raw_history:
            existing = self._normalize_teams(item)
            if self._is_valid_match(existing) and existing != normalized:
                hist.append(existing)
        hist.insert(0, normalized)
        if len(hist) > self._MATCH_HISTORY_MAX:
            del hist[self._MATCH_HISTORY_MAX:]
        g['match_history'] = hist

    def _forget_match(self, g: dict, teams: List[List[int]]) -> None:
        normalized = self._normalize_teams(teams)
        raw_history = g.get('match_history', [])
        if not isinstance(raw_history, list):
            raw_history = []
        history: List[List[List[int]]] = []
        for item in raw_history:
            existing = self._normalize_teams(item)
            if self._is_valid_match(existing) and existing != normalized:
                history.append(existing)
        g['match_history'] = history
        contexts = g.get('match_contexts')
        if isinstance(contexts, dict):
            contexts.pop(self._match_key(normalized), None)

    def set_last_match(
        self,
        guild_id: int,
        teams: List[List[int]],
        split_context: Optional[dict] = None,
    ) -> None:
        """チーム分け直後に呼び出す。teams = [[team_a_uids], [team_b_uids]]"""
        g = self._ensure_guild(guild_id)
        previous = g.get('last_match')
        normalized_new = self._normalize_teams(teams)
        if not self._is_valid_match(normalized_new):
            return
        if isinstance(previous, list):
            normalized_previous = self._normalize_teams(previous)
            if self._is_valid_match(normalized_previous) and normalized_previous != normalized_new:
                self._remember_match(g, normalized_previous)
                previous_context = g.get('last_split_context')
                if isinstance(previous_context, dict):
                    contexts = g.get('match_contexts')
                    if not isinstance(contexts, dict):
                        contexts = {}
                        g['match_contexts'] = contexts
                    contexts[self._match_key(normalized_previous)] = previous_context
            self._forget_match(g, normalized_new)
            g['last_match'] = normalized_new
        else:
            self._forget_match(g, normalized_new)
            g['last_match'] = normalized_new

        if split_context is None:
            g.pop('last_split_context', None)
        else:
            g['last_split_context'] = dict(split_context)
        self._save()

    def get_last_match(self, guild_id: int) -> Optional[List[List[int]]]:
        last = self._ensure_guild(guild_id).get('last_match')
        normalized = self._normalize_teams(last)
        return normalized if self._is_valid_match(normalized) else None

    def get_match_history(self, guild_id: int) -> List[List[List[int]]]:
        """過去の試合履歴を新しい順で返す（最大 _MATCH_HISTORY_MAX 件）。"""
        history = self._ensure_guild(guild_id).get('match_history', [])
        if not isinstance(history, list):
            return []
        return [
            normalized
            for item in history
            if self._is_valid_match(normalized := self._normalize_teams(item))
        ]

    def clear_last_match(self, guild_id: int) -> None:
        g = self._ensure_guild(guild_id)
        g.pop('last_match', None)
        g.pop('last_split_context', None)
        self._save()

    def get_split_context(self, guild_id: int, teams: List[List[int]]) -> Optional[dict]:
        g = self._ensure_guild(guild_id)
        normalized = self._normalize_teams(teams)
        if not self._is_valid_match(normalized):
            return None
        if self._normalize_teams(g.get('last_match')) == normalized:
            return self._copy_split_context(g.get('last_split_context'))
        contexts = g.get('match_contexts')
        if not isinstance(contexts, dict):
            return None
        context = contexts.get(self._match_key(normalized))
        return self._copy_split_context(context)

    def set_split_context(self, guild_id: int, teams: List[List[int]], context: dict) -> None:
        g = self._ensure_guild(guild_id)
        normalized = self._normalize_teams(teams)
        if not self._is_valid_match(normalized):
            return
        saved_context = dict(context)
        if self._normalize_teams(g.get('last_match')) == normalized:
            g['last_split_context'] = saved_context
        else:
            contexts = g.get('match_contexts')
            if not isinstance(contexts, dict):
                contexts = {}
                g['match_contexts'] = contexts
            contexts[self._match_key(normalized)] = saved_context
        self._save()

    def mark_split_history_committed(self, guild_id: int, teams: List[List[int]]) -> None:
        g = self._ensure_guild(guild_id)
        normalized = self._normalize_teams(teams)
        if not self._is_valid_match(normalized):
            return
        key = self._match_key(normalized)
        if self._normalize_teams(g.get('last_match')) == normalized:
            context = self._copy_split_context(g.get('last_split_context')) or {'version': 1}
            context['history_committed'] = True
            g['last_split_context'] = context
            self._save()
            return

        contexts = g.get('match_contexts')
        if not isinstance(contexts, dict):
            contexts = {}
            g['match_contexts'] = contexts
        context = self._copy_split_context(contexts.get(key)) or {'version': 1}
        context['history_committed'] = True
        contexts[key] = context
        self._save()

    # ---- 結果記録 ----

    def record_result(
        self, guild_id: int, winning_team_idx: int
    ) -> Tuple[List[int], List[int]]:
        """勝利チームのインデックスを受け取り戦績を更新。
        (winners, losers) の user_id リストを返す。
        last_match は記録後に消去する。"""
        g = self._ensure_guild(guild_id)
        last = self._normalize_teams(g.get('last_match'))
        if not self._is_valid_match(last) or winning_team_idx < 0 or winning_team_idx >= len(last):
            return [], []
        split_context = self._copy_split_context(g.get('last_split_context'))
        winners, losers = self._apply_result(
            guild_id,
            last,
            winning_team_idx,
            split_context=split_context,
        )
        g = self._ensure_guild(guild_id)
        self._forget_match(g, last)
        del g['last_match']
        g.pop('last_split_context', None)
        self._save()
        return winners, losers

    def record_result_for_teams(
        self, guild_id: int, teams: List[List[int]], winning_team_idx: int
    ) -> Tuple[List[int], List[int]]:
        """チームを直接指定して戦績を更新（last_match を使わない）。"""
        teams = self._normalize_teams(teams)
        if not self._is_valid_match(teams) or winning_team_idx < 0 or winning_team_idx >= len(teams):
            return [], []
        split_context = self.get_split_context(guild_id, teams)
        winners, losers = self._apply_result(
            guild_id,
            teams,
            winning_team_idx,
            split_context=split_context,
        )
        if winners:
            self._forget_match(self._ensure_guild(guild_id), teams)
            self._save()
        return winners, losers

    def _apply_result(
        self,
        guild_id: int,
        teams: List[List[int]],
        winning_team_idx: int,
        *,
        split_context: Optional[dict] = None,
    ) -> Tuple[List[int], List[int]]:
        g = self._ensure_guild(guild_id)
        winners = teams[winning_team_idx]
        losers = [uid for i, team in enumerate(teams) for uid in team if i != winning_team_idx]

        records = g.setdefault('records', {})
        if not isinstance(records, dict):
            records = {}
            g['records'] = records
        for uid in winners:
            r = _clean_record(records.get(str(uid)))
            r['wins'] += 1
            records[str(uid)] = r
        for uid in losers:
            r = _clean_record(records.get(str(uid)))
            r['losses'] += 1
            records[str(uid)] = r

        today = _today_jst()
        daily_records = g.setdefault('daily_records', {})
        if not isinstance(daily_records, dict):
            daily_records = {}
            g['daily_records'] = daily_records
        daily = daily_records.setdefault(today, {})
        if not isinstance(daily, dict):
            daily = {}
            daily_records[today] = daily
        for uid in winners:
            r = _clean_record(daily.get(str(uid)))
            r['wins'] += 1
            daily[str(uid)] = r
        for uid in losers:
            r = _clean_record(daily.get(str(uid)))
            r['losses'] += 1
            daily[str(uid)] = r

        last_result = {
            'winners': list(winners),
            'losers': list(losers),
            'teams': [list(team) for team in teams],
            'date': today,
        }
        if split_context is not None:
            last_result['split_context'] = dict(split_context)
        g['last_result'] = last_result
        return winners, losers

    def undo_last_result(self, guild_id: int) -> bool:
        """直前の record_result を取り消す。成功したら True を返す。"""
        g = self._ensure_guild(guild_id)
        last = g.pop('last_result', None)
        if not isinstance(last, dict):
            if last is not None:
                self._save()
            return False

        records = g.get('records')
        if not isinstance(records, dict):
            records = {}
            g['records'] = records
        raw_winners = last.get('winners', [])
        raw_losers = last.get('losers', [])
        if not isinstance(raw_winners, list):
            raw_winners = []
        if not isinstance(raw_losers, list):
            raw_losers = []
        winners = [uid for uid in (_safe_uid(v) for v in raw_winners) if uid is not None]
        losers = [uid for uid in (_safe_uid(v) for v in raw_losers) if uid is not None]
        for uid in winners:
            r = records.get(str(uid))
            if r:
                cleaned = _clean_record(r)
                cleaned['wins'] = max(0, cleaned['wins'] - 1)
                records[str(uid)] = cleaned
        for uid in losers:
            r = records.get(str(uid))
            if r:
                cleaned = _clean_record(r)
                cleaned['losses'] = max(0, cleaned['losses'] - 1)
                records[str(uid)] = cleaned

        date = last.get('date', _today_jst())
        daily_records = g.get('daily_records', {})
        daily = daily_records.get(date, {}) if isinstance(daily_records, dict) else {}
        for uid in winners:
            r = daily.get(str(uid))
            if r:
                cleaned = _clean_record(r)
                cleaned['wins'] = max(0, cleaned['wins'] - 1)
                daily[str(uid)] = cleaned
        for uid in losers:
            r = daily.get(str(uid))
            if r:
                cleaned = _clean_record(r)
                cleaned['losses'] = max(0, cleaned['losses'] - 1)
                daily[str(uid)] = cleaned

        teams = last.get('teams')
        if isinstance(teams, list):
            restored = self._normalize_teams(teams)
            if self._is_valid_match(restored):
                split_context = self._copy_split_context(last.get('split_context'))
                self._forget_match(g, restored)
                if 'last_match' not in g:
                    g['last_match'] = restored
                    if split_context is not None:
                        g['last_split_context'] = split_context
                    else:
                        g.pop('last_split_context', None)
                elif self._normalize_teams(g.get('last_match')) != restored:
                    self._remember_match(g, restored)
                    if split_context is not None:
                        contexts = g.get('match_contexts')
                        if not isinstance(contexts, dict):
                            contexts = {}
                            g['match_contexts'] = contexts
                        contexts[self._match_key(restored)] = split_context
                elif split_context is not None:
                    g['last_split_context'] = split_context

        self._save()
        return True

    def get_last_result(self, guild_id: int) -> Optional[dict]:
        last = self._ensure_guild(guild_id).get('last_result')
        if not isinstance(last, dict):
            return None
        raw_winners = last.get('winners', [])
        raw_losers = last.get('losers', [])
        if not isinstance(raw_winners, list):
            raw_winners = []
        if not isinstance(raw_losers, list):
            raw_losers = []
        teams = self._normalize_teams(last.get('teams', []))
        return {
            'winners': [
                uid for uid in (_safe_uid(value) for value in raw_winners)
                if uid is not None
            ],
            'losers': [
                uid for uid in (_safe_uid(value) for value in raw_losers)
                if uid is not None
            ],
            'teams': teams if self._is_valid_match(teams) else [],
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
        normalized = self._normalize_teams(teams)
        if self._is_valid_match(normalized):
            g['prev_match'] = normalized
        else:
            g.pop('prev_match', None)
        self._save()

    def get_prev_match(self, guild_id: int) -> Optional[List[List[int]]]:
        prev = self._ensure_guild(guild_id).get('prev_match')
        normalized = self._normalize_teams(prev)
        return normalized if self._is_valid_match(normalized) else None

    def set_role_history(self, guild_id: int, hist: Dict[int, List[str]]) -> None:
        g = self._ensure_guild(guild_id)
        g['role_history'] = {str(uid): list(roles) for uid, roles in hist.items()}
        self._save()

    def get_role_history(self, guild_id: int) -> Dict[int, List[str]]:
        g = self._ensure_guild(guild_id)
        raw_history = g.get('role_history', {})
        if not isinstance(raw_history, dict):
            return {}
        history: Dict[int, List[str]] = {}
        for uid, roles in raw_history.items():
            parsed_uid = _safe_uid(uid)
            if parsed_uid is None or not isinstance(roles, list):
                continue
            history[parsed_uid] = [str(role) for role in roles]
        return history

    def set_pair_history(self, guild_id: int, hist: Dict[str, int]) -> None:
        """ペアごとの同チーム累積回数を保存。key = "uid1_uid2" (uid1 < uid2)"""
        g = self._ensure_guild(guild_id)
        g['pair_history'] = dict(hist)
        self._save()

    def get_pair_history(self, guild_id: int) -> Dict[str, int]:
        g = self._ensure_guild(guild_id)
        raw_history = g.get('pair_history', {})
        if not isinstance(raw_history, dict):
            return {}
        result: Dict[str, int] = {}
        for key, value in raw_history.items():
            if not isinstance(key, str):
                continue
            parts = key.split('_')
            if len(parts) != 2:
                continue
            uid1 = _safe_uid(parts[0])
            uid2 = _safe_uid(parts[1])
            if uid1 is None or uid2 is None or uid1 == uid2:
                continue
            count = _safe_int(value, 0)
            if count > 0:
                pair_key = f'{min(uid1, uid2)}_{max(uid1, uid2)}'
                result[pair_key] = result.get(pair_key, 0) + count
        return result

    def set_spectator_history(
        self, guild_id: int, counts: Dict[int, int], last: List[int]
    ) -> None:
        """観戦回数と直前の観戦者リストを保存。"""
        g = self._ensure_guild(guild_id)
        g['spectator_counts'] = {str(uid): c for uid, c in counts.items()}
        g['last_spectators'] = list(last)
        self._save()

    def set_split_history(
        self,
        guild_id: int,
        teams: List[List[int]],
        pair_history: Dict[str, int],
        spectator_counts: Dict[int, int],
        last_spectators: List[int],
        role_history: Optional[Dict[int, List[str]]] = None,
    ) -> None:
        """チーム分け品質向上用の履歴をまとめて保存する。"""
        g = self._ensure_guild(guild_id)
        normalized = self._normalize_teams(teams)
        if self._is_valid_match(normalized):
            g['prev_match'] = normalized
        else:
            g.pop('prev_match', None)
        g['pair_history'] = dict(pair_history)
        g['spectator_counts'] = {str(uid): int(count) for uid, count in spectator_counts.items()}
        g['last_spectators'] = list(last_spectators)
        if role_history is not None:
            g['role_history'] = {str(uid): list(roles) for uid, roles in role_history.items()}
        self._save()

    def get_spectator_history(self, guild_id: int) -> tuple:
        """(counts: Dict[int,int], last_spectators: List[int]) を返す。"""
        g = self._ensure_guild(guild_id)
        raw_counts = g.get('spectator_counts', {})
        counts: Dict[int, int] = {}
        if isinstance(raw_counts, dict):
            for uid, count in raw_counts.items():
                parsed_uid = _safe_uid(uid)
                if parsed_uid is None:
                    continue
                counts[parsed_uid] = max(0, _safe_int(count, 0))
        raw_last = g.get('last_spectators', [])
        last = [
            uid for uid in (_safe_uid(value) for value in raw_last)
            if uid is not None
        ] if isinstance(raw_last, list) else []
        return counts, last

    # ---- 戦績参照 ----

    def get_record(self, guild_id: int, user_id: int) -> Dict[str, int]:
        g = self._ensure_guild(guild_id)
        records = g.get('records', {})
        if not isinstance(records, dict):
            return {'wins': 0, 'losses': 0}
        return _clean_record(records.get(str(user_id)))

    def get_daily_records(self, guild_id: int, date_str: Optional[str] = None) -> Dict[int, Dict[str, int]]:
        """指定日（省略時は今日JST）の戦績を返す。"""
        if date_str is None:
            date_str = _today_jst()
        g = self._ensure_guild(guild_id)
        daily_records = g.get('daily_records', {})
        if not isinstance(daily_records, dict):
            return {}
        return _clean_record_map(daily_records.get(date_str, {}))

    def get_all_records(self, guild_id: int) -> Dict[int, Dict[str, int]]:
        g = self._ensure_guild(guild_id)
        return _clean_record_map(g.get('records', {}))

    def export_stats(self, guild_id: int) -> dict:
        """戦績データをエクスポート用dictで返す。"""
        g = self._ensure_guild(guild_id)
        daily_records = g.get('daily_records', {})
        if not isinstance(daily_records, dict):
            daily_records = {}
        return {
            'version': 1,
            'records': {
                str(uid): record
                for uid, record in self.get_all_records(guild_id).items()
            },
            'daily_records': {
                date: {str(uid): record for uid, record in _clean_record_map(day).items()}
                for date, day in daily_records.items()
                if isinstance(date, str)
            },
        }

    def merge_stats(self, guild_id: int, data: dict) -> Dict[str, int]:
        """エクスポートデータをマージする。追加した勝数・負数の合計を返す。"""
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('対応していないフォーマットです。')

        g = self._ensure_guild(guild_id)

        def clean_record(value: object) -> dict:
            if not isinstance(value, dict):
                raise ValueError('invalid stats record')
            wins = value.get('wins', 0)
            losses = value.get('losses', 0)
            if (
                not isinstance(wins, int)
                or isinstance(wins, bool)
                or not isinstance(losses, int)
                or isinstance(losses, bool)
                or wins < 0
                or losses < 0
            ):
                raise ValueError('invalid stats value')
            return {'wins': wins, 'losses': losses}

        records_in = data.get('records', {})
        daily_in = data.get('daily_records', {})
        if not isinstance(records_in, dict) or not isinstance(daily_in, dict):
            raise ValueError('invalid stats format')

        cleaned_records = {}
        for uid, record in records_in.items():
            if not isinstance(uid, str) or not uid.isdigit():
                raise ValueError('invalid user id')
            parsed_uid = int(uid)
            if parsed_uid <= 0:
                raise ValueError('invalid user id')
            canonical_uid = str(parsed_uid)
            cleaned = clean_record(record)
            existing = cleaned_records.setdefault(canonical_uid, {'wins': 0, 'losses': 0})
            existing['wins'] += cleaned['wins']
            existing['losses'] += cleaned['losses']

        cleaned_daily = {}
        for date, day in daily_in.items():
            if not isinstance(date, str):
                raise ValueError('invalid date')
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError as exc:
                raise ValueError('invalid date') from exc
            if not isinstance(day, dict):
                raise ValueError('invalid daily stats format')
            cleaned_daily[date] = {}
            for uid, record in day.items():
                if not isinstance(uid, str) or not uid.isdigit():
                    raise ValueError('invalid user id')
                parsed_uid = int(uid)
                if parsed_uid <= 0:
                    raise ValueError('invalid user id')
                canonical_uid = str(parsed_uid)
                cleaned = clean_record(record)
                existing = cleaned_daily[date].setdefault(canonical_uid, {'wins': 0, 'losses': 0})
                existing['wins'] += cleaned['wins']
                existing['losses'] += cleaned['losses']

        added_wins = 0
        added_losses = 0

        # 通算戦績マージ
        records = g.get('records')
        if not isinstance(records, dict):
            records = {}
            g['records'] = records
        for uid, r in cleaned_records.items():
            existing = _clean_record(records.get(uid))
            existing['wins'] += r['wins']
            existing['losses'] += r['losses']
            records[uid] = existing
            added_wins += r['wins']
            added_losses += r['losses']

        # 日次戦績マージ
        daily_records = g.get('daily_records')
        if not isinstance(daily_records, dict):
            daily_records = {}
            g['daily_records'] = daily_records
        for date, day in cleaned_daily.items():
            daily_day = daily_records.get(date)
            if not isinstance(daily_day, dict):
                daily_day = {}
                daily_records[date] = daily_day
            for uid, r in day.items():
                existing = _clean_record(daily_day.get(uid))
                existing['wins'] += r['wins']
                existing['losses'] += r['losses']
                daily_day[uid] = existing

        self._save()
        return {'added_wins': added_wins, 'added_losses': added_losses}

    def reset_stats(self, guild_id: int) -> int:
        """戦績をリセット。削除したプレイヤー数を返す。"""
        g = self._ensure_guild(guild_id)
        count = len(self.get_all_records(guild_id))
        g.pop('records', None)
        g.pop('last_result', None)
        g.pop('daily_records', None)
        g.pop('last_match', None)
        g.pop('last_split_context', None)
        g.pop('match_history', None)
        g.pop('match_contexts', None)
        g.pop('prev_match', None)
        g.pop('role_history', None)
        g.pop('pair_history', None)
        g.pop('spectator_counts', None)
        g.pop('last_spectators', None)
        self._save()
        return count


_STORE: Optional[StatsStore] = None


def get_stats_store() -> StatsStore:
    global _STORE
    if _STORE is None:
        _STORE = StatsStore()
    return _STORE
