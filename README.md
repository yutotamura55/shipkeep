# shipkeep

バイブコーディングで作ったソフトウェアを、公開やローンチに耐える状態にし、その後も保ち続けるための、AI エージェント用の skill 群です。
名前は、出す（ship）と保ち続ける（keep）から取りました。

**状態：開発中です。** 使える skill は `shipkeep-publish-check`（公開前の確認）、`shipkeep-quality-tools`（品質の道具の導入）、`shipkeep-refactor`（リファクタリング）です。

## 構成する skill

| skill | いつ使うか | 内容 | 状態 |
| --- | --- | --- | --- |
| `shipkeep-publish-check` | 非公開のリポジトリを公開する前 | 履歴の秘密情報・個人情報、LICENSE、README、`.gitignore` などを確かめる | 初版 |
| `shipkeep-quality-tools` | 開発中、公開・ローンチの前 | 書式・lint・型検査・セキュリティ向けの lint・テストとカバレッジ・依存の監査・CI の不足を、どの言語でも調べ、利用者が選んだものを導入する | 初版 |
| 定期的な点検 | 公開した後、毎週や脆弱性のアラートのとき | 新しい脆弱性の報告が自分のコードに関わるかを判断して証拠付きで報告し、利用者が承認すれば修正する | 予定 |
| ローンチ前の点検 | 利用者に提供する前 | 既存の点検ツールの結果を、共通の形式と Issue につなぐ | 予定 |
| `shipkeep-refactor` | 点検の後、運用中 | よく変わり大きいファイルを候補に挙げ、今の動作をテストで固定し、複雑度を測りながら動作を変えずに作り直す | 初版 |

点検の工程は、対象のリポジトリを読むだけで変更しません。結果は項目ごとに「満たす」「不足」「確認できない」と根拠で報告します。
点検の後に直す工程（道具の導入、リファクタリング、定期的な点検で見つかった脆弱性の修正）は、対象のリポジトリを変更します。
変更は、利用者が計画を承認してから専用のブランチで行い、PR にして渡します。マージは利用者が行います。
守る原則は [`shared/change-principles.md`](shared/change-principles.md) にあります。
[Agent Skills](https://agentskills.io/) の形式（`SKILL.md`）で、Claude Code・Codex などから同じ手順で使えるようにします。

## 使い方

[skills CLI](https://github.com/vercel-labs/skills) で、使うエージェントに skill を入れます。次は Claude Code に入れる例です。

```bash
npx skills add yutotamura55/shipkeep --skill shipkeep-publish-check --agent claude-code
```

### 公開前の確認（`shipkeep-publish-check`）

エージェントに「このリポジトリを公開してよいか確かめて」のように頼むと、skill が履歴を調べ、項目ごとに「満たす」「不足」「確認できない」と根拠を報告します。
Python 3.10 以上と Git が必要です。GitHub の設定も確かめるには、認証済みの `gh` を使います。

公開してはいけない語（取引先の名前など）は、リポジトリの外のファイル（既定は `~/.config/shipkeep/denylist.txt`）に 1 行に 1 語ずつ書きます。報告には語そのものを出さず、何行目の語かだけを示します。

### 品質の道具の導入（`shipkeep-quality-tools`）

エージェントに「このプロジェクトに足りない品質の道具を調べて」のように頼みます。
skill は、使っている言語と、すでにある道具（例：Ruff はあるが型検査とカバレッジの計測がない）を観点ごとに調べ、足りない道具を理由と変わるファイルとともに提案します。
Python と JavaScript/TypeScript は推奨の一覧を持ち、ほかの言語はエージェントがその場で調べて出典を付けます。
利用者が選んだ道具だけを、専用のブランチで導入し、検査を実行して結果を報告します。既存の道具は置き換えません。
Python 3.10 以上と Git が必要です。導入には、そのプロジェクトのパッケージ管理の道具を使います。

### リファクタリング（`shipkeep-refactor`）

エージェントに「直す価値のある場所を探して」や「この関数を変えやすくして」のように頼みます。
場所を指定しない場合、skill は Git の履歴から、よく変わり、かつ大きいファイルを候補に挙げます。
始める前に、目的（例：「状態の表示を 1 つ追加するとき、変える場所を 1 か所にする」）、範囲、完了の条件、打ち切る条件を利用者と決めます。
今の動作をテストで固定し、変更の前後で複雑度を測り、小さな段階に分けて変更します。動作を変える必要が分かったら、そこで止めます。
Python 3.10 以上と Git が必要です。複雑度の計測には、言語ごとの道具（Lizard、jscpd など）を使います。

## リポジトリの構成

| 場所 | 内容 |
| --- | --- |
| `skills/<skill 名>/` | 各 skill。`SKILL.md` と、決まった検査を行うスクリプト（`scripts/`）を置く |
| `shared/` | 複数の skill が使う文書の原本。各 skill の `references/` に同じ内容の写しを置く |
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
