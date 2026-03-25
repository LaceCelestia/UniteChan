import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Optional


@dataclass
class SplitConfig:
    role_balance_targets: Dict[str, int]
    avoid_count: int
    banned_pokemon: FrozenSet[str] = field(default_factory=frozenset)


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
        role_balance_targets = {
            'attacker': int(role_raw.get('attacker', 0) or 0),
            'all_rounder': int(role_raw.get('all_rounder', 0) or 0),
            'speedster': int(role_raw.get('speedster', 0) or 0),
            'defender': int(role_raw.get('defender', 0) or 0),
            'supporter': int(role_raw.get('supporter', 0) or 0),
        }
        avoid_count = int(split.get('avoid', 0) or 0)
        banned_pokemon = frozenset(str(v) for v in g.get('banned_pokemon', []))
        return SplitConfig(
            role_balance_targets=role_balance_targets,
            avoid_count=avoid_count,
            banned_pokemon=banned_pokemon,
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
        c = max(0, min(5, int(count)))
        split['avoid'] = c
        g['split'] = split
        self._save()

    # ---- VC チャンネル ----

    def set_vc_channel(self, guild_id: int, team_idx: int, channel_id: int) -> None:
        """team_idx=0: Team A, 1: Team B"""
        g = self._ensure_guild(guild_id)
        vc = g.setdefault('vc_channels', {})
        vc[str(team_idx)] = channel_id
        self._save()

    def get_vc_channels(self, guild_id: int) -> tuple[int | None, int | None]:
        """(team_a_channel_id, team_b_channel_id) を返す。未設定は None。"""
        g = self._ensure_guild(guild_id)
        vc = g.get('vc_channels', {})
        return vc.get('0'), vc.get('1')

    # ---- バンポケモン ----

    def get_banned_pokemon(self, guild_id: int) -> FrozenSet[str]:
        g = self._ensure_guild(guild_id)
        return frozenset(str(v) for v in g.get('banned_pokemon', []))

    def ban_pokemon(self, guild_id: int, name: str) -> bool:
        """True: 新規バン / False: すでにバン済み"""
        g = self._ensure_guild(guild_id)
        banned = set(g.get('banned_pokemon', []))
        if name in banned:
            return False
        banned.add(name)
        g['banned_pokemon'] = sorted(banned)
        self._save()
        return True

    def unban_pokemon(self, guild_id: int, name: str) -> bool:
        """True: 解除成功 / False: バンされていない"""
        g = self._ensure_guild(guild_id)
        banned = set(g.get('banned_pokemon', []))
        if name not in banned:
            return False
        banned.discard(name)
        g['banned_pokemon'] = sorted(banned)
        self._save()
        return True

    def clear_banned_pokemon(self, guild_id: int) -> int:
        """バンをすべて解除し、解除した件数を返す"""
        g = self._ensure_guild(guild_id)
        count = len(g.get('banned_pokemon', []))
        if count:
            g['banned_pokemon'] = []
            self._save()
        return count

    # ---- その他 ----

    def reset_guild(self, guild_id: int) -> None:
        gid = str(guild_id)
        if gid in self._data:
            del self._data[gid]
            self._save()

    def describe_split_config(self, guild_id: int) -> str:
        cfg = self.get_split_config(guild_id)
        rb = cfg.role_balance_targets
        lines = [
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
