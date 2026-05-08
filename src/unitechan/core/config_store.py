import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Optional

from .paths import data_path


def _is_valid_split_code(code: str) -> bool:
    return (
        len(code) == 5
        and code.isdigit()
        and code[0] in '0123'
        and code[1] in '012'
        and code[2] in '012'
        and code[3] in '01'
        and code[4] in '01'
    )


def _safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_int_or_none(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_pair(value: object) -> Optional[tuple[int, int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    uid1 = _safe_int_or_none(value[0])
    uid2 = _safe_int_or_none(value[1])
    if uid1 is None or uid2 is None or uid1 == uid2:
        return None
    return (min(uid1, uid2), max(uid1, uid2))


@dataclass
class SplitConfig:
    role_balance_targets: Dict[str, int]
    avoid_count: int
    banned_pokemon: FrozenSet[str] = field(default_factory=frozenset)
    # 必ず別チームにするペア。各要素は (min_uid, max_uid) のタプル
    separate_pairs: FrozenSet[tuple] = field(default_factory=frozenset)


class ConfigStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path: Path = path or data_path('config_state.json')
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise RuntimeError(f'failed to load config state: {self._path}') from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f'invalid config state format: {self._path}')
        self._data = raw

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp_path = self._path.with_name(f'{self._path.name}.tmp')
        tmp_path.write_text(text, encoding='utf-8')
        tmp_path.replace(self._path)

    def _ensure_guild(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if not isinstance(self._data.get(gid), dict):
            self._data[gid] = {}
        return self._data[gid]

    def _ensure_split_section(self, g: dict) -> dict:
        split = g.get('split')
        if not isinstance(split, dict):
            split = {}
            g['split'] = split
        return split

    def _ensure_vc_section(self, g: dict) -> dict:
        vc = g.get('vc_channels')
        if not isinstance(vc, dict):
            vc = {}
            g['vc_channels'] = vc
        return vc

    def _valid_banned_pokemon(self, g: dict) -> set[str]:
        raw_banned = g.get('banned_pokemon', [])
        if not isinstance(raw_banned, list):
            return set()
        return {str(value) for value in raw_banned if str(value)}

    def get_split_config(self, guild_id: int) -> SplitConfig:
        g = self._ensure_guild(guild_id)
        split = g.get('split', {})
        if not isinstance(split, dict):
            split = {}
        role_raw = split.get('role_balance', {})
        if not isinstance(role_raw, dict):
            role_raw = {}
        role_balance_targets = {
            'attacker': _safe_int(role_raw.get('attacker'), 0),
            'all_rounder': _safe_int(role_raw.get('all_rounder'), 0),
            'speedster': _safe_int(role_raw.get('speedster'), 0),
            'defender': _safe_int(role_raw.get('defender'), 0),
            'supporter': _safe_int(role_raw.get('supporter'), 0),
        }
        avoid_count = max(0, min(5, _safe_int(split.get('avoid'), 0)))
        banned_pokemon = frozenset(self._valid_banned_pokemon(g))
        separate_pairs = frozenset(self._valid_separate_pairs(g))
        return SplitConfig(
            role_balance_targets=role_balance_targets,
            avoid_count=avoid_count,
            banned_pokemon=banned_pokemon,
            separate_pairs=separate_pairs,
        )

    def set_role_balance_targets(
        self,
        guild_id: int,
        attacker: int,
        all_rounder: int,
        speedster: int,
        defender: int,
        supporter: int,
    ) -> None:
        g = self._ensure_guild(guild_id)
        split = self._ensure_split_section(g)
        split['role_balance'] = {
            'attacker': int(attacker),
            'all_rounder': int(all_rounder),
            'speedster': int(speedster),
            'defender': int(defender),
            'supporter': int(supporter),
        }
        g['split'] = split
        self._save()

    def set_avoid_count(self, guild_id: int, count: int) -> None:
        g = self._ensure_guild(guild_id)
        split = self._ensure_split_section(g)
        c = max(0, min(5, int(count)))
        split['avoid'] = c
        self._save()

    # ---- デフォルト チーム分けコード ----

    def set_split_code(self, guild_id: int, code: str) -> None:
        if not _is_valid_split_code(code):
            raise ValueError('invalid split code')
        g = self._ensure_guild(guild_id)
        self._ensure_split_section(g)['code'] = code
        self._save()

    def get_split_code(self, guild_id: int) -> str:
        """未設定の場合は '00000' を返す"""
        split = self._ensure_guild(guild_id).get('split', {})
        if not isinstance(split, dict):
            split = {}
        code = str(split.get('code', '00000'))
        return code if _is_valid_split_code(code) else '00000'

    # ---- VC チャンネル ----

    def set_vc_channel(self, guild_id: int, team_idx: int, channel_id: int) -> None:
        """team_idx=0: Team A, 1: Team B"""
        g = self._ensure_guild(guild_id)
        vc = self._ensure_vc_section(g)
        vc[str(team_idx)] = channel_id
        self._save()

    def get_vc_channels(self, guild_id: int) -> tuple[int | None, int | None]:
        """(team_a_channel_id, team_b_channel_id) を返す。未設定は None。"""
        g = self._ensure_guild(guild_id)
        vc = g.get('vc_channels', {})
        if not isinstance(vc, dict):
            return None, None
        return _safe_int_or_none(vc.get('0')), _safe_int_or_none(vc.get('1'))

    # ---- バンポケモン ----

    def get_banned_pokemon(self, guild_id: int) -> FrozenSet[str]:
        g = self._ensure_guild(guild_id)
        raw_banned = g.get('banned_pokemon', [])
        if not isinstance(raw_banned, list):
            raw_banned = []
        return frozenset(str(v) for v in raw_banned if str(v))

    def ban_pokemon(self, guild_id: int, name: str) -> bool:
        """True: 新規バン / False: すでにバン済み"""
        g = self._ensure_guild(guild_id)
        banned = self._valid_banned_pokemon(g)
        if name in banned:
            return False
        banned.add(name)
        g['banned_pokemon'] = sorted(banned)
        self._save()
        return True

    def unban_pokemon(self, guild_id: int, name: str) -> bool:
        """True: 解除成功 / False: バンされていない"""
        g = self._ensure_guild(guild_id)
        banned = self._valid_banned_pokemon(g)
        if name not in banned:
            return False
        banned.discard(name)
        g['banned_pokemon'] = sorted(banned)
        self._save()
        return True

    def clear_banned_pokemon(self, guild_id: int) -> int:
        """バンをすべて解除し、解除した件数を返す"""
        g = self._ensure_guild(guild_id)
        count = len(self._valid_banned_pokemon(g))
        if count:
            g['banned_pokemon'] = []
            self._save()
        return count

    # ---- 分離ペア ----

    def _pair_key(self, uid1: int, uid2: int) -> tuple:
        return (min(uid1, uid2), max(uid1, uid2))

    def _valid_separate_pairs(self, g: dict) -> list[tuple[int, int]]:
        raw_pairs = g.get('separate_pairs', [])
        if not isinstance(raw_pairs, list):
            return []
        pairs: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for raw_pair in raw_pairs:
            pair = _normalize_pair(raw_pair)
            if pair is None or pair in seen:
                continue
            seen.add(pair)
            pairs.append(pair)
        return pairs

    def get_separate_pairs(self, guild_id: int) -> list:
        return [list(pair) for pair in self._valid_separate_pairs(self._ensure_guild(guild_id))]

    def add_separate_pair(self, guild_id: int, uid1: int, uid2: int) -> bool:
        """True: 新規追加 / False: すでに登録済み"""
        g = self._ensure_guild(guild_id)
        pairs = self._valid_separate_pairs(g)
        key = _normalize_pair([uid1, uid2])
        if key is None:
            return False
        if key in pairs:
            return False
        pairs.append(key)
        g['separate_pairs'] = [list(pair) for pair in pairs]
        self._save()
        return True

    def remove_separate_pair(self, guild_id: int, uid1: int, uid2: int) -> bool:
        """True: 削除成功 / False: 登録されていない"""
        g = self._ensure_guild(guild_id)
        pairs = self._valid_separate_pairs(g)
        key = _normalize_pair([uid1, uid2])
        if key is None:
            return False
        if key not in pairs:
            return False
        pairs.remove(key)
        g['separate_pairs'] = [list(p) for p in pairs]
        self._save()
        return True

    def clear_separate_pairs(self, guild_id: int) -> int:
        g = self._ensure_guild(guild_id)
        count = len(self._valid_separate_pairs(g))
        if count:
            g['separate_pairs'] = []
            self._save()
        return count

    # ---- スタートアナウンス ----

    def get_start_announce(self, guild_id: int) -> int:
        """VC移動後に「XX:XX スタートです」を告知するまでの分数。0 = OFF。"""
        return max(0, _safe_int(self._ensure_guild(guild_id).get('start_announce_minutes'), 0))

    def set_start_announce(self, guild_id: int, minutes: int) -> None:
        self._ensure_guild(guild_id)['start_announce_minutes'] = max(0, int(minutes))
        self._save()

    # ---- その他 ----

    def reset_split_settings(self, guild_id: int) -> bool:
        gid = str(guild_id)
        g = self._data.get(gid)
        if not isinstance(g, dict) or 'split' not in g:
            return False
        del g['split']
        if not g:
            del self._data[gid]
        self._save()
        return True

    def reset_guild(self, guild_id: int) -> None:
        gid = str(guild_id)
        if gid in self._data:
            del self._data[gid]
            self._save()

    def describe_split_config(self, guild_id: int) -> str:
        cfg = self.get_split_config(guild_id)
        rb = cfg.role_balance_targets
        code = self.get_split_code(guild_id)
        lines = [
            f'チーム分けコード: {code}',
            f"ロールバランス(1チーム想定): ATK={rb['attacker']} ALL={rb['all_rounder']} SPD={rb['speedster']} DEF={rb['defender']} SUP={rb['supporter']}",
            f'連続ロール回避 avoid={cfg.avoid_count} (0で無効, 最大5)',
        ]
        if cfg.banned_pokemon:
            lines.append(f'バン中ポケモン ({len(cfg.banned_pokemon)}件): {", ".join(sorted(cfg.banned_pokemon))}')
        else:
            lines.append('バン中ポケモン: なし')
        return '\n'.join(lines)


_STORE: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    global _STORE
    if _STORE is None:
        _STORE = ConfigStore()
    return _STORE
