import json
from pathlib import Path
from typing import Dict, Set, Optional, Tuple


# 旧実装の英語ランク → ポケモンユナイトの日本語ランク
_EN_TO_JP_RANK = {
    'Beginner': 'ビギナー',
    'Great': 'スーパー',
    'Veteran': 'ハイパー',
    'Expert': 'エリート',
    'Ultra': 'エキスパート',
    'Master': 'マスター',
}


class LobbyStore:
    """ロビーとランク情報の永続化を担当するクラス"""

    # ★★★★★ ここをクラス変数にする（重要）★★★★★
    _data_path: Path = Path("data/lobby_state.json")
    _lobbies: Dict[int, Set[int]] = {}
    _ranks: Dict[int, Dict[int, str]] = {}
    _aliases: Dict[int, Dict[int, str]] = {}  # guild_id -> {user_id -> alias}
    _loaded: bool = False

    def __init__(self, data_path: Optional[Path] = None) -> None:
        if data_path:
            LobbyStore._data_path = data_path

        # ★インスタンスごとではなく、一度だけ読み込む★
        if not LobbyStore._loaded:
            self._load_state()
            LobbyStore._loaded = True

    # ---- 内部ユーティリティ ----

    def _ensure_guild(self, guild_id: int) -> None:
        LobbyStore._lobbies.setdefault(guild_id, set())
        LobbyStore._ranks.setdefault(guild_id, {})
        LobbyStore._aliases.setdefault(guild_id, {})

    def _normalize_rank(self, value: str) -> str:
        return _EN_TO_JP_RANK.get(value, value)

    def _load_state(self) -> None:
        path = LobbyStore._data_path
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return

        for gid_str, info in raw.items():
            try:
                gid = int(gid_str)
            except ValueError:
                continue

            members = {int(uid) for uid in info.get('members', []) if str(uid).isdigit()}
            ranks_raw = info.get('ranks', {})

            ranks: Dict[int, str] = {}
            for uid_str, rank in ranks_raw.items():
                if uid_str.isdigit():
                    uid = int(uid_str)
                    ranks[uid] = self._normalize_rank(str(rank))

            aliases_raw = info.get('aliases', {})
            aliases: Dict[int, str] = {
                int(uid_str): str(name)
                for uid_str, name in aliases_raw.items()
                if uid_str.isdigit()
            }

            LobbyStore._lobbies[gid] = members
            LobbyStore._ranks[gid] = ranks
            LobbyStore._aliases[gid] = aliases

        self._save_state()

    def _save_state(self) -> None:
        path = LobbyStore._data_path
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        for gid, members in LobbyStore._lobbies.items():
            ranks = LobbyStore._ranks.get(gid, {})
            aliases = LobbyStore._aliases.get(gid, {})
            data[str(gid)] = {
                'members': list(members),
                'ranks': {str(uid): rank for uid, rank in ranks.items()},
                'aliases': {str(uid): name for uid, name in aliases.items()},
            }

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    # ---- 公開API ----

    def get_lobby(self, guild_id: int) -> Set[int]:
        self._ensure_guild(guild_id)
        return LobbyStore._lobbies[guild_id]

    def get_ranks(self, guild_id: int) -> Dict[int, str]:
        self._ensure_guild(guild_id)
        return LobbyStore._ranks[guild_id]

    def join(self, guild_id: int, user_id: int) -> int:
        lobby = self.get_lobby(guild_id)
        lobby.add(user_id)
        self._save_state()
        return len(lobby)

    def leave(self, guild_id: int, user_id: int) -> int:
        lobby = self.get_lobby(guild_id)
        lobby.discard(user_id)
        self._save_state()
        return len(lobby)

    def set_rank(self, guild_id: int, user_id: int, rank: str) -> None:
        ranks = self.get_ranks(guild_id)
        ranks[user_id] = self._normalize_rank(rank)
        self._save_state()

    def kick(self, guild_id: int, user_id: int) -> bool:
        self._ensure_guild(guild_id)
        lobby = LobbyStore._lobbies[guild_id]
        ranks = LobbyStore._ranks[guild_id]

        if user_id not in lobby:
            return False

        lobby.discard(user_id)
        ranks.pop(user_id, None)
        self._save_state()
        return True

    def set_members(self, guild_id: int, user_ids: Set[int]) -> None:
        """ロビーを指定メンバーで丸ごと置き換える（ランクは保持）"""
        LobbyStore._ranks.setdefault(guild_id, {})
        LobbyStore._lobbies[guild_id] = set(user_ids)
        self._save_state()

    def get_alias(self, guild_id: int, user_id: int) -> str | None:
        self._ensure_guild(guild_id)
        return LobbyStore._aliases[guild_id].get(user_id)

    def set_alias(self, guild_id: int, user_id: int, name: str | None) -> None:
        self._ensure_guild(guild_id)
        if name:
            LobbyStore._aliases[guild_id][user_id] = name
        else:
            LobbyStore._aliases[guild_id].pop(user_id, None)
        self._save_state()

    def snapshot(self, guild_id: int) -> Tuple[Set[int], Dict[int, str]]:
        self._ensure_guild(guild_id)
        return set(LobbyStore._lobbies[guild_id]), dict(LobbyStore._ranks[guild_id])
