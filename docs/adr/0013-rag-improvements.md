# ADR 0013 — RAG 改善機能を機能フラグで実装

- ステータス: Accepted
- 決定日: 2026-06-22
- 関連ドキュメント: [docs/rag-improvements.md](../rag-improvements.md)

---

## 背景

MVP（T1〜T5）完了後、チャット UI が動く状態になった。
次の課題は **検索精度の向上** で、以下の 4 手法を検討した。

| 手法 | 問題の解消 |
|---|---|
| **Rerank** | ベクトル検索の「意味的に近いが答えでない」チャンクを後段で排除 |
| **HyDE** | クエリ（問い）と文書（答え）の言語ギャップによる検索ミスを緩和 |
| **Hybrid Retrieval** | 固有名詞・技術用語はベクトル検索が苦手→キーワード検索で補完 |
| **Grounded Citation** | 回答と sources の対応が不明瞭な問題を引用番号で解消 |

---

## 決定

**4 機能すべてを実装し、機能フラグ（環境変数）で個別に ON/OFF 可能にする。**

```python
# workers/config.py
RERANK_ENABLED     = os.getenv("RERANK_ENABLED",    "false").lower() == "true"
HYBRID_ENABLED     = os.getenv("HYBRID_ENABLED",    "false").lower() == "true"
HYDE_ENABLED       = os.getenv("HYDE_ENABLED",      "false").lower() == "true"
CITATION_ENABLED   = os.getenv("CITATION_ENABLED",  "false").lower() == "true"
RERANK_CANDIDATE_K = int(os.getenv("RERANK_CANDIDATE_K", "20"))
```

---

## 各機能の設計判断

### Rerank — `RERANK_ENABLED`

- **モデル**: `BAAI/bge-reranker-v2-m3`（多言語 CrossEncoder、日本語対応）
- **フロー**: vector search で候補 `RERANK_CANDIDATE_K=20` 件取得 → rerank → 上位 `top_k=5` 件返却
- **キャッシュ**: `SentenceReranker` はクラスレベルの `_cache` dict + `threading.Lock` でモデルをプロセス内 1 回だけロード
  - `run_in_executor` 経由のマルチスレッド呼び出しで競合が起きないよう Lock を追加
- **フォールバック**: `contextlib.suppress(Exception)` で囲み、OOM 等のエラー時はベクター結果をそのまま返す
- **却下した代替**: FlagEmbedding の FlagReranker（API が不安定）、Cohere Rerank（外部 API 料金）

### HyDE — `HYDE_ENABLED`

- **生成モデル**: `config.CHAT_MODEL`（`qwen2.5:7b`）を共用。追加モデル不要
- **フォールバック**: Ollama 障害・空レスポンス（`content == ""`）時は元クエリへフォールバック
  - `get("content", query)` では空文字列がフォールバックしないため `(content or "") if content else query` のロジックを採用
- **副作用**: 仮説生成で 1〜2 秒レイテンシが増加。フラグ OFF がデフォルトのため非 HyDE ユーザーへの影響なし

### Hybrid Retrieval — `HYBRID_ENABLED`

- **融合方式**: RRF（Reciprocal Rank Fusion）スコア = Σ `1/(k + rank + 1)`、k=60
  - 重みチューニング不要・リストの順位のみで融合できる点を優先
- **キーワード検索**: `pg_bigm` の `LIKE` パターンマッチ（`bigm_similarity` でスコアリング）
  - クエリ中の `%` `_` `\` を LIKE ワイルドカードインジェクション防止のためエスケープ
- **VectorStore 契約**: `VectorStore` ABC に `search_keyword()` をデフォルト実装（`return []`）で追加
  - pg_bigm 非対応の実装を壊さず、PgVectorStore だけでオーバーライド
- **フォールバック**: `pg_bigm` 未インストール環境では SQL エラーが起きるため `contextlib.suppress(Exception)` でベクター結果に自動フォールバック

### Grounded Citation — `CITATION_ENABLED`

- **実装**: `_SYSTEM_PROMPT` に `{citation_instruction}` プレースホルダーを追加。CITATION_ENABLED 時のみ引用指示を挿入
- **コンテキスト形式**: `[1] 【タイトル｜章】\n本文` の番号付きでフォーマット
- **UI 変更なし**: 既存のソースチップをそのまま活用（番号とインデックスの対応は LLM が解釈）

---

## フォールバック戦略

各機能は「失敗しても全体を止めない」設計とする。

| 機能 | 失敗時の動作 |
|---|---|
| HyDE | 元クエリで検索続行 |
| Hybrid | ベクター検索結果のみで続行 |
| Rerank | ベクター検索結果をそのまま返す |
| Citation | フラグ OFF と同じプロンプトにフォールバック（エラー自体が起きにくい） |

---

## 却下した選択肢

| 選択肢 | 却下理由 |
|---|---|
| GraphRAG | エンティティ関係構築が複雑。書籍単体 RAG には過剰 |
| Parent-Child チャンク | チャンクスキーマ変更を伴う。精度寄与を計測してから検討 |
| CRAG（自動再検索ループ） | レイテンシ増・ループ制御の複雑さ。MVP 後に再評価 |
| ColPali | テキスト書籍が対象。図表中心ではないためスキップ |

---

## 結果と既知の限界

- 4 機能はすべて環境変数で独立して有効化できる
- デフォルト OFF のため既存動作はそのまま維持される
- `pg_bigm` は Docker イメージに未組み込みのため `HYBRID_ENABLED=true` は要 Dockerfile 追加
- 検索精度の定量評価（MRR@5 / Hit@3）は今後 `scripts/eval_search.py` で計測予定
