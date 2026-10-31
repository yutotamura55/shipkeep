# shipkeep

バイブコーディングで作ったソフトウェアを、公開やローンチに耐える状態にし、その後も保ち続けるための、AI エージェント用の skill 群です。
名前は、出す（ship）と保ち続ける（keep）から取りました。

**状態：開発中です。** 使える skill は `shipkeep-publish-check`（公開前の確認）だけです。

## 構成する skill

| skill | いつ使うか | 内容 | 状態 |
| --- | --- | --- | --- |
| `shipkeep-publish-check` | 非公開のリポジトリを公開する前 | 履歴の秘密情報・個人情報、LICENSE、README、`.gitignore` などを確かめる | 初版 |
| 定期的な点検 | 公開した後、毎週や脆弱性のアラートのとき | 新しい脆弱性の報告が自分のコードに関わるかを判断し、証拠付きの Issue にする | 予定 |
| ローンチ前の点検 | 利用者に提供する前 | 既存の点検ツールの結果を、共通の形式と Issue につなぐ | 予定 |
| リファクタリング | 点検の後、運用中 | 複雑度・重複・責務の偏りを測り、直す計画を立てる | 予定 |

どの skill も、対象のリポジトリを読むだけで変更しません。結果は項目ごとに「満たす」「不足」「確認できない」と根拠で報告します。
[Agent Skills](https://agentskills.io/) の形式（`SKILL.md`）で、Claude Code・Codex などから同じ手順で使えるようにします。

## 使い方

[skills CLI](https://github.com/vercel-labs/skills) で、使うエージェントに skill を入れます。次は Claude Code に入れる例です。

```bash
npx skills add yutotamura55/shipkeep --skill shipkeep-publish-check --agent claude-code
```

エージェントに「このリポジトリを公開してよいか確かめて」のように頼むと、skill が履歴を調べ、項目ごとに「満たす」「不足」「確認できない」と根拠を報告します。
Python 3.10 以上と Git が必要です。GitHub の設定も確かめるには、認証済みの `gh` を使います。

公開してはいけない語（取引先の名前など）は、リポジトリの外のファイル（既定は `~/.config/shipkeep/denylist.txt`）に 1 行に 1 語ずつ書きます。報告には語そのものを出さず、何行目の語かだけを示します。

## リポジトリの構成

| 場所 | 内容 |
| --- | --- |
| `skills/<skill 名>/` | 各 skill。`SKILL.md` と、決まった検査を行うスクリプト（`scripts/`）を置く |
| `tests/` | スクリプトのテスト |
| `scripts/check.sh` | 書式・lint・型検査・テストをまとめて実行する |

検査のスクリプトは Python の標準ライブラリだけを使います。skill を使う環境に追加のパッケージは要りません。

## 開発

Docker と Docker Compose が必要です。リポジトリのルートで次を実行すると、コンテナの中で CI と同じ検査（Ruff の書式と lint、mypy、pytest）が動きます。

```bash
export HOST_UID=$(id -u) HOST_GID=$(id -g)
docker compose run --rm dev scripts/check.sh
```

`HOST_UID` と `HOST_GID` は、コンテナの中で作られたファイル（キャッシュなど）の所有者を、ホストの利用者にそろえるための値です。設定していないと、compose はエラーで止まります。

検査が失敗したときに、書式と import の順序を自動で直すには次を実行します。

```bash
docker compose run --rm dev sh -c "ruff format . && ruff check --fix ."
```

CI（`.github/workflows/ci.yml`）は、PR と `main` への push のたびに、Python 3.10（対応する最も古い版）、3.11、3.14（コンテナの版）で同じ検査を実行します。

## ライセンス

[MIT](LICENSE)
