# ADR 0014 — 検索精度の評価基盤: hit@k / MRR + fixture クエリセット

- ステータス: 採用済み
- 決定日: 2026-06-28

---

## 背景

RAG 精度改善（ADR 0013）の各手法が「実際に効いているか」を定量的に判断する手段がなかった。
設定を変えるたびに手動で確認するのは非再現的で、手法間の比較もできない。
評価基盤が先に整っていないと改善の方向性を誤るリスクがある。

---

## 決定

`scripts/eval_search.py` を拡張し、以下の評価基盤を整備する。

### 指標

| 指標 | 定義 |
|---|---|
| **hit@k** | クエリ集のうち、top-k 件に relevant なチャンクが含まれた割合 |
| **MRR** (Mean Reciprocal Rank) | 各クエリの最初の relevant チャンクの順位の逆数の平均 |

relevance の判定は「期待キーワードのいずれかが取得チャンク本文に含まれるか」の軽量プロキシを採用する。
人手ラベル不要・即計測できる反面、キーワード選定の精度に依存するトレードオフがある。

### クエリセット

| 種類 | 場所 | 用途 |
|---|---|---|
| fixture クエリ（著作権フリー） | `tests/fixtures/eval_queries.json` | CI で回せる再現性ある評価 |
| 書籍固有クエリ | `books/eval_queries.json`（gitignore） | 実書籍での精度計測 |

fixture クエリは同梱の著作権フリー PDF（`sample_book.pdf`）の内容から作成し、
CI の全テスト実行で DB なしに単体テストとして動作することを保証する。

### `--compare` フラグ

4条件（baseline / RERANK / HyDE / RERANK+HyDE）を一括評価してサマリ表を出力する。

```bash
uv run python scripts/eval_search.py tests/fixtures/eval_queries.json --compare
```

環境変数を一時的に上書き・`config` を `importlib.reload` することで、
1プロセス内で複数条件の切り替えを実現している。

---

## 却下した代替案

| 案 | 却下理由 |
|---|---|
| 人手 relevance ラベル | 小規模プロジェクトにはコストが高い。まずプロキシで相対比較する |
| NDCG など多段グレード指標 | relevance ラベルが必要。現フェーズでは過剰 |
| 外部評価フレームワーク（RAGAS 等） | 依存が増える。シンプルなスクリプトで十分 |

---

## トレードオフ・リスク

- キーワードプロキシは「キーワードが含まれても文脈が違う」偽陽性が出うる。
  精度が上がってきたら人手ラベルへの移行を検討する。
- `--compare` の `importlib.reload` は副作用があるため本番コードでは使わない。評価スクリプト専用。
- fixture クエリは著作権フリー素材ベースのため、実書籍とのドメイン差がある。
  実書籍向けには `books/eval_queries.json`（gitignore）を別途作成して計測する。
