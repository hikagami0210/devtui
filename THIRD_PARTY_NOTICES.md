# 外部ツールとライセンス

## 本リポジトリのコード

本アプリ独自コードの公開ライセンスは未指定です。MIT等のオープンソースライセンスを適用したという意味ではありません。所有者は自身の別PCへcloneして使用できますが、第三者への改変・再配布等の許諾範囲は別途決定してください。

## 外部依存

次のツールは各PCで別途インストールして使用します。実行ファイル、ライブラリ、ソースをこのリポジトリへ同梱・転載していません。

| ツール | 利用方法 | 上流のライセンス |
| --- | --- | --- |
| Python / 標準ライブラリ | ホストのPythonから実行 | [Python Software Foundation License等](https://docs.python.org/3/license.html) |
| mise | 外部CLIとして呼び出し | [MIT](https://github.com/jdx/mise/blob/main/LICENSE) |
| pitchfork | mise経由でサーバーを管理 | [MIT](https://github.com/jdx/pitchfork/blob/main/LICENSE) |
| Node.js | デモHTTPサーバーの実行 | [MITおよび同梱依存の各ライセンス](https://github.com/nodejs/node/blob/v22.20.0/LICENSE) |
| Git | 現在ブランチの参照 | [GPLv2](https://github.com/git/git/blob/master/COPYING) |

Pythonの追加パッケージは不要です。スクリーンショット用のフォントファイル、画面生成に使ったPillow等は配布対象に含みません。preview.pngはアプリのデモ表示画像です。

mise/pitchforkのMITライセンスは、ソフトウェアのコピーや重要部分を再配布する場合に著作権表示・許諾文の保持を要求しています。将来これらのバイナリを同梱する配布に変更する場合は、Node/Python等の同梱依存も含め、配布物のライセンス表示を改めて整備してください。
