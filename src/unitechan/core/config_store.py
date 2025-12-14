import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class SplitConfig:
    role_balance_targets: Dict[str, int]
    avoid_count: int


class ConfigStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path: Path = path or Path('data/config_state.json')
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except Exception:
            self._data = {}
            return
        if not isinstance(raw, dict):
            self._data = {}
            return
        self._data = raw

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        self._path.write_text(text, encoding='utf-8')

    def _ensure_guild(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self._data:
            self._data[gid] = {}
        return self._data[gid]

    def get_split_config(self, guild_id: int) -> SplitConfig:
        g = self._ensure_guild(guild_id)
        split = g.get('split', {})
        role_raw = split.get('role_balance', {})
        if not isinstance(role_raw, dict):
            role_raw = {}
        # デフォルトは全部0
        role_balance_targets = {
            'attacker': int(role_raw.get('attacker', 0) or 0),
            'all_rounder': int(role_raw.get('all_rounder', 0) or 0),
            'speedster': int(role_raw.get('speedster', 0) or 0),
            'defender': int(role_raw.get('defender', 0) or 0),
            'supporter': int(role_raw.get('supporter', 0) or 0),
        }
        avoid_count = int(split.get('avoid', 0) or 0)
        # avoid は 0〜5 に丸めておく
        if avoid_count < 0:
            avoid_count = 0
        if avoid_count > 5:
            avoid_count = 5
        return SplitConfig(role_balance_targets=role_balance_targets, avoid_count=avoid_count)

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
        split = g.get('split') or {}
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
        split = g.get('split') or {}
        c = int(count)
        if c < 0:
            c = 0
        if c > 5:
            c = 5
        split['avoid'] = c
        g['split'] = split
        self._save()

    def reset_guild(self, guild_id: int) -> None:
        gid = str(guild_id)
        if gid in self._data:
            del self._data[gid]
            self._save()

    def describe_split_config(self, guild_id: int) -> str:
        cfg = self.get_split_config(guild_id)
        rb = cfg.role_balance_targets
        parts = []
        parts.append(f"ロールバランス(1チーム想定): ATK={rb['attacker']} ALL={rb['all_rounder']} SPD={rb['speedster']} DEF={rb['defender']} SUP={rb['supporter']}")
        parts.append(f'連続ロール回避 avoid={cfg.avoid_count} (0で無効, 最大5)')
        return '\n'.join(parts)


_STORE: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    global _STORE
    if _STORE is None:
        _STORE = ConfigStore()
    return _STORE
