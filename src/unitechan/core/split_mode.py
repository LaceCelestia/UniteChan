from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SplitMode:
    """5桁コードを保持するモードクラス。

    桁の意味:
      1桁目 a: 分け方モード
        0 = ランダム
        1 = ランクバランス ON
        2 = 通算戦績（勝率）バランス
        3 = 当日戦績（勝率）バランス

      2桁目 b: ロールバランス
        0 = 無視
        1 = 自動 (ATK/ALL/SPD/DEF/SUP を1つずつ)
        2 = /config で指定した比率を使用

      3桁目 c: ポケモン割当
        0 = 割当なし
        1 = 個人割当（ロールに合ったポケモンを1人1匹）
        2 = チーム割当（全5ロール各1匹のセットをチームに提示、誰が使うかはチームで決める）

      4桁目 d: 連続ロール回避フラグ
        0 = 無効
        1 = 有効（/config avoid の回数を使用）

      5桁目 e: チーム間重複許可
        0 = 禁止（A/B間で同ポケ不可）
        1 = 許可（A/B間で同ポケ可、ただしチーム内重複は禁止）

    ※ 内部用に SplitMode("x1xxx") という特殊コンストラクタ呼び出しも
       使われているため、それも動くようにしてある。
    """

    mode_raw: str
    use_rank_balance: bool
    use_stats_balance: bool
    use_daily_stats_balance: bool
    role_balance_mode: int
    pokemon_assign_mode: int
    use_avoid: bool
    allow_cross_dup: bool

    # -------------------------------------------------- 生成まわり --

    def __init__(self, mode_raw: str) -> None:
        # デフォルト値
        self.mode_raw = mode_raw
        self.use_rank_balance = False
        self.use_stats_balance = False
        self.use_daily_stats_balance = False
        self.role_balance_mode = 0
        self.pokemon_assign_mode = 0
        self.use_avoid = False
        self.allow_cross_dup = False

        # 特殊: 内部用途 SplitService からの SplitMode("x1xxx")
        # → 「ロール自動 (b=1)」としてだけ扱う
        if mode_raw == "x1xxx":
            self.role_balance_mode = 1
            return

        # 通常の 5桁コード
        if len(mode_raw) == 5 and mode_raw.isdigit():
            a, b, c, d, e = mode_raw

            # 1桁目: バランス方式 (0=ランダム / 1=ランク / 2=通算戦績 / 3=当日戦績)
            self.use_rank_balance = (a == "1")
            self.use_stats_balance = (a == "2")
            self.use_daily_stats_balance = (a == "3")

            # 2桁目: ロールバランス (0/1/2)
            self.role_balance_mode = int(b)

            # 3桁目: ポケモン割当 (0/1/2)
            self.pokemon_assign_mode = int(c)

            # 4桁目: 連続ロール回避 (0/1)
            self.use_avoid = (d == "1")

            # 5桁目: チーム間重複許可 (0/1)
            self.allow_cross_dup = (e == "1")


    # -------------------------------------------------- parse（外部API） --

    @classmethod
    def parse(cls, text: str) -> "SplitMode":
        """ユーザー入力の5桁コードを検証して SplitMode を返す。"""
        if len(text) != 5 or not text.isdigit():
            raise ValueError("5桁の数字で指定してね。例: 00000 / 11110 / 12111")

        a, b, c, d, e = text

        # a は 0/1/2/3 だけ許可
        if a not in ("0", "1", "2", "3"):
            raise ValueError("1桁目（バランス方式）は 0〜3 で指定してね。0=ランダム / 1=ランク / 2=通算戦績 / 3=当日戦績")

        # b, c は 0/1/2 だけ許可
        if b not in ("0", "1", "2"):
            raise ValueError("2桁目（ロールバランス）は 0/1/2 で指定してね。")
        if c not in ("0", "1", "2"):
            raise ValueError("3桁目（ポケモン割当）は 0/1/2 で指定してね。")

        # d, e は 0/1 だけ許可
        if d not in ("0", "1"):
            raise ValueError("4桁目（連続ロール回避）は 0/1 で指定してね。")
        if e not in ("0", "1"):
            raise ValueError("5桁目（チーム間重複許可）は 0/1 で指定してね。")

        return cls(text)
