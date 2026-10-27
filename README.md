# shipkeep

バイブコーディングで作ったソフトウェアを、公開やローンチに耐える状態にし、その後も保ち続けるための、AI エージェント用の skill 群です。
名前は、出す（ship）と保ち続ける（keep）から取りました。

**状態：開発中です。** まだ使える skill はありません。

## 構成する skill

| skill | いつ使うか | 内容 | 状態 |
| --- | --- | --- | --- |
| `shipkeep-publish-check` | 非公開のリポジトリを公開する前 | 履歴の秘密情報・個人情報、LICENSE、README、`.gitignore` などを確かめる | 実装中（[#2](https://github.com/yutotamura55/shipkeep/issues/2)） |
| 定期的な点検 | 公開した後、毎週や脆弱性のアラートのとき | 新しい脆弱性の報告が自分のコードに関わるかを判断し、証拠付きの Issue にする | 予定 |
| ローンチ前の点検 | 利用者に提供する前 | 既存の点検ツールの結果を、共通の形式と Issue につなぐ | 予定 |
| リファクタリング | 点検の後、運用中 | 複雑度・重複・責務の偏りを測り、直す計画を立てる | 予定 |

どの skill も、対象のリポジトリを読むだけで変更しません。結果は項目ごとに「満たす」「不足」「確認できない」と根拠で報告します。
[Agent Skills](https://agentskills.io/) の形式（`SKILL.md`）で、Claude Code・Codex などから同じ手順で使えるようにします。

## リポジトリの構成

| 場所 | 内容 |
| --- | --- |
| `skills/<skill 名>/` | 各 skill。`SKILL.md` と、決まった検査を行うスクリプト（`scripts/`）を置く。今は `shipkeep-publish-check` の検査スクリプトの入口（`scripts/scan.py`）だけがあり、`SKILL.md` は #2 で追加する |
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

CI（`.github/workflows/ci.yml`）は、PR と `main` への push のたびに、Python 3.11（対応する最も古い版）と 3.14（コンテナの版）で同じ検査を実行します。

## ライセンス

[MIT](LICENSE)
