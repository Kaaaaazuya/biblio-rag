# RAG 改善ロードマップ

- 作成日: 2026-06-21
- 参考: [Qiita — RAG改善テクニック集](https://qiita.com/engchina/items/3060140b10a9a35021f4)

---

## 1. 現状のパイプライン

```
PDF → ① 抽出 → ② チャンク → ③ 埋め込み → pgvector → ④ 検索 → ⑤ 生成
```

| フェーズ | 現在の実装 |
|---|---|
| **① 抽出** | pymupdf4llm で PDF → Markdown。ヘッダ/フッタ除去（margins）、フォントサイズ相対判定で見出し検出、段落行結合、`<!-- page:N -->` 注入 |
| **② チャンク** | 800字 + overlap 120字、句点優先で文中カットなし、見出し境界尊重、見出し prefix を本文頭に付与、コードフェンス保護 |
| **③ 埋め込み** | Ollama `bge-m3`（1024次元）、バッチ32件、pgvector にコサイン距離で格納 |
| **④ 検索** | ベクトル検索のみ（`<=>` コサイン距離、top_k=5） |
| **⑤ 生成** | Ollama `qwen2.5:7b` SSE ストリーム、見出し付き context 注入、ペルソナ/言語指定、sources 別送 |

---

## 2. 改善候補の評価

### ★★★ 高優先

#### Rerank（クロスエンコーダ再スコアリング）

- **概要**: 候補を多めに取得したあと、クロスエンコーダで再スコアリングして絞る。ベクトル検索の「意味的に近いが答えでない」問題を緩和する。
- **実装方針**: `workers/rerank/` を新設し `Reranker` ABC + `SentenceReranker` 実装クラスを置く。`_retrieve()` の後段に追加。`VectorStore.search` インターフェースは変えない。
- **ライブラリ**: `sentence-transformers` + `bge-reranker-v2-m3`（多言語対応・日本語◎）
- **top_k**: 候補 20件 → rerank → 返却 5件
- **実装コスト**: 低（インターフェース追加のみ）
- **副作用**: `torch` 依存が増える（約 2GB）、rerank 推論分のレイテンシ増

#### Hybrid Retrieval（ベクトル + キーワード融合）

- **概要**: ベクトル検索と全文検索を RRF（Reciprocal Rank Fusion）で融合する。固有名詞・技術用語（例: "pgvector", "pytest"）はベクトル検索が苦手なためキーワード検索で補う。
- **実装方針**: `pg_bigm`（日本語トライグラム）を Docker イメージに追加し、RRF で融合。`PgVectorStore.search()` を拡張。
- **融合アルゴリズム**: RRF（`score = 1/(k + rank)` の和。重みチューニング不要）
- **実装コスト**: 中（Dockerfile 追加 + スキーマ変更 + SQL 変更）
- **前提条件**: `pgvector/pgvector:pg17` ベースの カスタム Dockerfile で `pg_bigm` をビルド

### ★★☆ 中優先

#### Grounded Citation（回答内引用番号）

- **概要**: LLM の回答文中に `[1]` `[2]` の形で引用番号を付与し、sources との対応を明示する。現状は sources を SSE 別送しているが、本文との紐付けがない。
- **実装方針**: システムプロンプトのみ変更。context を `[1] ...\n[2] ...` 形式で番号付きにし、「引用番号を使って回答せよ」と指示。フロントエンドは既存のソースチップをそのまま活用。
- **実装コスト**: 低（プロンプト変更のみ、UI 変更なし）

#### HyDE（Hypothetical Document Embeddings）

- **概要**: クエリをそのままベクトル化するのではなく、まず Ollama で仮説的な回答文を生成し、その回答文をベクトル化して検索する。「問いの言語」と「答えの言語」のギャップを埋める。
- **実装方針**: `_retrieve()` の前段に仮説生成ステップを追加。生成モデルは `config.CHAT_MODEL`（`qwen2.5:7b`）を共用。ローカル Ollama なので追加 API コストなし。
- **実装コスト**: 低（`_retrieve` 前段に1ステップ追加）
- **副作用**: レイテンシが 1〜2 秒増加（仮説生成の推論時間）

### ★☆☆ 将来検討

#### Parent-Child チャンク

- **概要**: 小さいチャンク（200字程度）で検索し、ヒットしたチャンクの親チャンク（800字）を LLM に渡す。検索精度と文脈量を両立する。
- **実装コスト**: 中（チャンク構造変更、スキーマ変更を伴う）

### スキップ（このプロジェクトでは対費用効果が低い）

| 手法 | 理由 |
|---|---|
| **GraphRAG** | エンティティ関係の構築が複雑。書籍単体のRAGには過剰 |
| **ColPali** | テキスト書籍が対象。図表中心の資料ではないのでスキップ |
| **CRAG** | 自動再検索ループは複雑かつレイテンシ増。MVP後に再評価 |
| **Guardrail** | 個人利用のため優先度低 |

---

## 3. 設計決定（2026-06-21）

### アーキテクチャ

`_retrieve` に直列追加。各機能は環境変数フラグで個別に ON/OFF する。

```
query
  ↓ [HYDE_ENABLED]    仮説回答生成（qwen2.5:7b）→ 仮説文でベクトル化
  ↓                   embed（bge-m3）
  ↓                   pgvector ベクトル検索（候補 RERANK_CANDIDATE_K=20 件）
  ↓ [HYBRID_ENABLED]  pg_bigm キーワード検索 → RRF 融合
  ↓ [RERANK_ENABLED]  sentence-transformers bge-reranker-v2-m3 → 上位 top_k=5 件
  → list[dict]

生成プロンプト
  ↓ [CITATION_ENABLED] context に [1][2] 番号付与、引用指示を追加
  ↓ Ollama qwen2.5:7b SSE ストリーム
```

### 機能フラグ（`workers/config.py` に追加）

```python
RERANK_ENABLED      = os.getenv("RERANK_ENABLED",    "false").lower() == "true"
HYBRID_ENABLED      = os.getenv("HYBRID_ENABLED",    "false").lower() == "true"
HYDE_ENABLED        = os.getenv("HYDE_ENABLED",      "false").lower() == "true"
CITATION_ENABLED    = os.getenv("CITATION_ENABLED",  "false").lower() == "true"
RERANK_CANDIDATE_K  = int(os.getenv("RERANK_CANDIDATE_K", "20"))
```

### 新規ディレクトリ

```
workers/rerank/
  __init__.py
  base.py               # Reranker ABC
  sentence_reranker.py  # sentence-transformers 実装
```

---

## 4. 実装ロードマップ

```
[Step 1] config.py にフラグ追加          ✅ 完了
  ↓
[Step 2] Rerank — workers/rerank/ 新設、_retrieve 後段に追加  ✅ 完了
  ↓
[Step 3] Grounded Citation — システムプロンプト改修  ✅ 完了（main PR #3）
  ↓
[Step 4] Hybrid Retrieval — Dockerfile + スキーマ + _retrieve 統合  ✅ 完了（main PR #3）
  ↓
[Step 5] HyDE — _retrieve 前段に仮説生成追加  ✅ 完了（main PR #3）
```

---

## 5. 効果の計測方針

改善前後を比較するための評価指標として、`scripts/eval_search.py` を拡張して以下を測る。

- **MRR@5**（Mean Reciprocal Rank）: 正解チャンクが何番目に来るか
- **Hit@3**: 上位3件に正解が含まれる割合
- テストセット: `tests/fixtures/` に評価用クエリ・期待チャンクを追加（著作権フリー素材）
