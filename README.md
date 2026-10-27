# repo-publish-check

非公開の Git リポジトリを公開に切り替える前に、公開してはいけないものが履歴に残っていないか、公開に必要なものがそろっているかを確かめる、AI エージェント用の skill です。

**状態：開発中です。** まだ検査の項目を実装していないため、使える状態ではありません。
予定している検査は [#2](https://github.com/yutotamura55/repo-publish-check/issues/2) にあります。

- 対象のリポジトリを読むだけで、変更しません。GitHub にも書き込みません。
- 履歴の秘密情報・個人情報、LICENSE、README、`.gitignore` などを確かめ、項目ごとに「満たす」「不足」「確認できない」と根拠を報告します。
- [Agent Skills](https://agentskills.io/) の形式（`SKILL.md`）で、Claude Code・Codex などから同じ手順で使えるようにします。

## 構成

| 場所 | 内容 |
| --- | --- |
| `skills/repo-publish-check/` | skill 本体。今は検査を行うスクリプト（`scripts/scan.py`）の入口だけがある。`SKILL.md` は #2 で追加する |
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
