# UniteChan

ポケモンユナイト向けの Discord Bot です。ロビー管理、チーム分け、VC移動、ロール・ポケモン割当、戦績管理をまとめて扱えます。

- Repository: `https://github.com/LaceCelestia/UniteChan`
- Releases: `https://github.com/LaceCelestia/UniteChan/releases`
- Package version: `0.2.0`

## 主な機能

- ロビー参加、VC からの一括回収、表示名の上書き
- `/split run` によるチーム分け
- `/guimode` によるボタン UI 操作
- ランク、通算戦績、当日戦績を使ったバランス調整
- ロール自動割当、ポケモン個別割当、チーム単位ポケモンセット割当
- バンポケモン、別チーム固定ペア
- VC 自動移動、スタート時刻告知
- 戦績の記録、取り消し、エクスポート、インポート

## セットアップ

### 必要環境

- Python `3.11` 以上

### インストール

```bash
git clone https://github.com/LaceCelestia/UniteChan.git
cd UniteChan
pip install -e .
```

### 環境変数

プロジェクトルートに `.env` を作成します。

```env
DISCORD_TOKEN=ここにBotトークン
```

## Discord 側の設定

### Bot の作成と招待

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリを作成する
2. `Bot` タブでトークンを発行し、`.env` に設定する
3. `OAuth2 > URL Generator` で `bot` と `applications.commands` を選ぶ
4. 下記の権限を付けてサーバーへ招待する

### 必要な Intent

- `Message Content Intent`: 必須
- `Server Members Intent`: 任意

`Server Members Intent` は必須ではありません。未有効でも動作しますが、一部ユーザー名の取得精度を上げたい場合は有効化すると安定します。

### 推奨 Bot 権限

| 権限 | 用途 |
|---|---|
| `Send Messages` | 結果通知、GUI パネル、Embed 送信 |
| `Embed Links` | チーム分け結果の表示 |
| `Add Reactions` | `/split run` 後のリアクション操作 |
| `Read Message History` | リアクションイベント処理 |
| `Move Members` | VC 移動 |
| `Attach Files` | `/stats export` |

### 招待時の Scope

- `bot`
- `applications.commands`

## 起動

### Windows

```bat
run.bat
```

クラッシュ時は 5 秒後に自動再起動します。

### 直接起動

```bash
python -m unitechan.app.bot
```

Bot 起動後にスラッシュコマンドが見えない場合は、サーバーで `/sync` を実行してください。

## 基本の流れ

1. `/lobby_collect` または `/join` で参加者を集める
2. `/split run` または `/guimode` でチーム分けする
3. `🎙️` リアクション、`/split move`、または GUI の `VC移動` で VC に振り分ける
4. 試合後に `🇦 / 🇧` または GUI の `A勝ち / B勝ち` で結果を記録する

## 権限メモ

実装上、権限条件は 3 種類あります。

- `Discord Administrator 必須`
  - `/split test`
  - `/split move`
  - `/sync`
- `管理権限が必要`
  - `Administrator` / `Manage Guild` / `Manage Roles` のいずれか
  - `/guimode`
  - `/config split`
  - `/config vc`
  - `/config start_announce`
  - `/result_undo`
  - `/ban ...`
  - `/separate ...`
  - `/lobby_collect`
  - `/lobby_clear`
  - `/kick`
  - `/join <member>`
  - `/name [name] <member>`
- `誰でも実行可能`
  - 上記以外

注意:
- `/config role_balance`、`/config avoid`、`/config reset` は現在の実装では誰でも実行できます。
- `/stats export` も現在の実装では誰でも実行できます。

## コマンド一覧

### ロビー

| コマンド | 説明 |
|---|---|
| `/join` | 自分をロビーに参加させる |
| `/join <member>` | 指定メンバーをロビーに追加する |
| `/leave` | ロビーから抜ける |
| `/lobby` | 現在のロビーメンバーを表示する |
| `/lobby_collect` | 自分が入っている VC のメンバーをロビーに一括登録する |
| `/lobby_clear` | ロビーを空にする |
| `/kick <member>` | ロビーから指定メンバーを削除する |
| `/rank <rank>` | 自分のランクを登録する |
| `/name [name] [member]` | Bot 内の表示名を変更する。`name` 省略でリセット |

### チーム分け

| コマンド | 説明 |
|---|---|
| `/split run [code]` | チーム分けを実行する。`code` 省略時は `/config split` の設定値を使う |
| `/split test [mode]` | デモデータでチーム分けを試す |
| `/split move [channel_a] [channel_b]` | 直前のチーム分け結果に従って VC 移動する |
| `/split prev` | 過去 5 試合分の履歴から後追いで勝敗記録する |

`/split run` の結果メッセージには以下のリアクションを付けられます。

| リアクション | 説明 |
|---|---|
| `🇦` | Team A 勝利として記録 |
| `🇧` | Team B 勝利として記録 |
| `🎙️` | そのメッセージのチーム構成で VC 移動 |
| `🔄` | そのメッセージの split code で再抽選 |
| `🔁` | 勝利記録後、同じ組み合わせで再戦記録 |

補足:
- `🔄` は現在表示中のメッセージが持っている split code を維持します。
- `🇦 / 🇧` はそのメッセージに紐づいたチーム構成を使って記録します。

### GUI モード

| コマンド | 説明 |
|---|---|
| `/guimode [code]` | ボタン UI のチーム分けパネルを作成する |

`/guimode` のボタン権限は以下です。

- 誰でも押せる
  - `Team A`
  - `Team B`
  - `観戦`
  - `離脱`
- 管理権限が必要
  - `ロビー同期`
  - `自動分け`
  - `リセット`
  - `VC移動`
  - `A勝ち`
  - `B勝ち`
  - `勝敗取消`

`/guimode` の仕様:

- `code` を省略したパネルは `/config split` の変更に追従します
- `code` を明示したパネルは固定です
- `VC移動` 後は `A勝ち / B勝ち` を入れるまで他の操作はロックされます
- `勝敗取消` で直前の GUI 記録を戻せます
- GUI の状態はメモリ保持です。Bot 再起動後は新しく `/guimode` を作り直してください

### 戦績

| コマンド | 説明 |
|---|---|
| `/result <team>` | `last_match` を使って手動で勝敗記録する |
| `/result_undo` | 直前の勝敗記録を取り消す |
| `/stats show [member] [period]` | 通算または当日の戦績を表示する |
| `/stats reset` | 戦績を全削除する |
| `/stats export` | 戦績 JSON をエクスポートする |
| `/stats import <file>` | 戦績 JSON をインポートしてマージする |

補足:
- 当日戦績は JST `05:00` 切り替えです
- `result_undo` 後は同じ試合を再記録できます

### バン・別チーム設定

| コマンド | 説明 |
|---|---|
| `/ban add <pokemon>` | ポケモンをバンする |
| `/ban remove <pokemon>` | バンを解除する |
| `/ban list` | バン中のポケモン一覧を表示する |
| `/ban clear` | バンを全解除する |
| `/separate add <member1> <member2>` | 2 人を必ず別チームにする |
| `/separate remove <member1> <member2>` | 別チーム設定を解除する |
| `/separate list` | 別チーム設定一覧を表示する |
| `/separate clear` | 別チーム設定を全解除する |

### 設定

| コマンド | 説明 |
|---|---|
| `/config split <code>` | デフォルトの split code を設定する |
| `/config role_balance <atk> <all> <spd> <deff> <sup>` | `b=2` 用のロール構成を設定する |
| `/config avoid <count>` | 連続ロール回避回数を設定する |
| `/config vc <team> <channel>` | デフォルト VC を設定する |
| `/config start_announce <minutes>` | VC 移動後のスタート告知時刻を設定する |
| `/config show` | 現在の設定を表示する |
| `/config reset` | `/split` 関連設定をリセットする |

## チーム分けコード

split code は 5 桁です。

```text
[a][b][c][d][e]
 a: バランス方式
 b: ロール割当
 c: ポケモン割当
 d: 連続ロール回避
 e: チーム間重複許可
```

| 桁 | 意味 | 値 |
|---|---|---|
| `a` | バランス方式 | `0` ランダム / `1` ランク / `2` 通算戦績 / `3` 当日戦績 |
| `b` | ロール割当 | `0` なし / `1` 自動 / `2` `/config role_balance` を使用 |
| `c` | ポケモン割当 | `0` なし / `1` 個人割当 / `2` チーム割当 |
| `d` | 連続ロール回避 | `0` OFF / `1` ON |
| `e` | チーム間重複 | `0` 禁止 / `1` 許可 |

### 例

| コード | 内容 |
|---|---|
| `00000` | 完全ランダム |
| `10000` | ランクバランスのみ |
| `11000` | ランクバランス + ロール自動 |
| `11100` | ランクバランス + ロール自動 + 個人ポケモン割当 |
| `12110` | ランクバランス + 設定ロール + 個人ポケモン割当 + 連続回避 |
| `21000` | 通算戦績バランス + ロール自動 |
| `30000` | 当日戦績バランス |

補足:
- `a=0` のときは、同じ人同士の組み合わせ被りを減らす方向で振り分けます
- `a=1/2/3` のときは、ランクや戦績の均等化を優先します
- `c=2` のときは、チームごとにロール別ポケモンセットが表示されます

## VC 移動とスタート告知

- `/split move` と GUI の `VC移動` は `/config vc` の設定を使います
- `/config start_announce <minutes>` を設定すると、VC 移動後に `HH:MM スタートです！` を通知します
- スタート告知メッセージは、告知時刻の 5 分後に自動削除されます
- `VC移動完了` などの短い通知は一定時間後に自動削除されます

## ポケモンデータ

ポケモン一覧は [data/pokemon_list.yaml](data/pokemon_list.yaml) で管理しています。ロールごとの候補を編集すると、割当対象も変わります。

## 永続化されるファイル

以下のファイルは実行中に更新されます。

- `data/config_state.json`
- `data/lobby_state.json`
- `data/stats_state.json`

以下はマスターデータです。

- `data/pokemon_list.yaml`

状態ファイルは Git 管理対象に入れない前提です。
