# 別PCでのセットアップ

対象はmacOS/Linuxです。Python 3.11以上（標準のcursesモジュール付き）、mise 2026.11.8以上、Gitが必要です。Windowsネイティブは未対応です。Linuxでcursesが不足する場合は、OSのPython/cursesパッケージを用意してください。

## 初回

```sh
git clone https://github.com/hikagami0210/devtui.git
cd devtui
mise trust ./mise.toml
mise install
./run-tests
./install
export PATH="$HOME/.local/bin:$PATH"
devtui --demo
```

`mise install`はNode 22.20.0とpitchfork 2.33.1を、そのPCのOS/CPUに合わせて取得します。初回はネットワーク接続が必要です。PythonはランチャーがPATH上から3.11以上を探します。見つからない場合は`DEVTUI_PYTHON=/path/to/python3.13`で指定できます。

PATHの設定を次回以降も使うには、必要に応じてシェルの設定ファイルに追加してください。インストール先を変える場合は`./install /path/to/bin`を使えます。登録するのはソースへのリンクなので、clone先は利用中に移動・削除しないでください。

セットアップを確認する場合:

```sh
devtui --help
devtui --demo --inspect
devtui --demo --doctor
```

デモの5台は起動時点では自動起動しません。`2 miseタスク`（英語表示ではMise tasks）で`servers:start`を選ぶと全台起動します。ポート4317〜4321を使用します。終了時のKEEP/STOPは`e`で切り替えます。

デモは日本語表示で起動します。`L`（Shift + `l`）で日英を切り替えられ、`devtui --demo --lang en`で英語起動も可能です。自分のワークスペースでは`[_.devtui]`に`language = "ja"`を追加すると日本語を初期値にできます。`--lang ja` / `--lang en`は設定ファイルより優先され、切り替え操作自体はファイルを書き換えません。

## 自分の開発環境を管理する

各repoと共通の`mise.toml`、そのタスクが参照するスクリプトを別PCにも配置します。repoパスは共通mise.tomlからの相対パスにすると、PCを替えても使いやすくなります。

```sh
devtui /path/to/development-workspace --inspect
devtui /path/to/development-workspace --doctor
devtui /path/to/development-workspace
```

このリポジトリに入っているのはTUIと5台のデモです。DEV/ITAのDB切替タスク、Lima/Docker設定、DBデータ、DB接続先の認証情報は各開発環境で別途用意します。

## 更新

```sh
git pull --ff-only
mise trust ./mise.toml
mise install
./run-tests
```

TUIを終了してから更新し、更新後に起動し直してください。ソースへのリンクで登録しているため、同じclone先なら`./install`のやり直しは不要です。タスクや起動設定を変更した場合はmise.tomlの内容を確認してから信頼してください。

## ローカル設定と公開対象

`try-demo`は通常PATH上のmiseを使用します。必要なら`DEVTUI_MISE=/path/to/mise`で別バイナリを指定できます。このPC専用の設定は`.devtui-demo.local.sh`に置けますが、Gitには登録しません。このファイルはシェルスクリプトとして実行されるため、自分が用意した設定だけを使ってください。

`.env`、`mise.local.toml`、ログ、dump、実行状態、依存バイナリをpushしない設定にしています。既存の別PCのコマンドやシェル設定はインストーラーから上書きしません。
