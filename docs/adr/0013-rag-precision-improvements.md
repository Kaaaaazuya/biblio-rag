# ADR 0013 — RAG 精度改善: Rerank / Hybrid / HyDE / Citation

- ステータス: 採用済み
- 決定日: 2026-06-21
- 参考: [Zenn — RAG の精度を上げる方法まとめ](https://zenn.dev/hironakamura_ai/articles/0a15a0c37b89ed)

---

## 背景

MVP（T1〜T5）の縦串が通った後、チャット UI でベクトル検索のみで質問したところ
「意味的には近いが答えでないチャンク」が上位に来るケースが観察された。
また固有名詞（例: `pgvector`, `pytest`）はベクトル距離で捕捉しにくいという既知の問題もある。

Zenn 記事を参考に、実装コストと精度改善効果のバランスが良い手法を選定した。

---

## 決定

以下の4手法を `workers/config.py` の環境変数フラグで制御する形で導入する。
デフォルトはすべて無効（`false`）とし、環境ごとに有効化できる。

| フラグ | 手法 | デフォルト |
|---|---|---|
| `RERANK_ENABLED` | クロスエンコーダ再スコアリング | false |
| `HYBRID_ENABLED` | ベクトル + キーワード RRF 融合 | false |
| `HYDE_ENABLED` | 仮説回答文でクエリを書き換え | false |
| `CITATION_ENABLED` | 回答内に引用番号を付与 | false |

### 検索フロー（`_retrieve` 内）

```
query
  ↓ [HYDE_ENABLED]    Ollama で仮説回答を生成 → 仮説文をベクトル化
  ↓                   embed（bge-m3）
  ↓                   pgvector ベクトル検索（候補 RERANK_CANDIDATE_K=20 件）
  ↓ [HYBRID_ENABLED]  pg_bigm キーワード検索 → RRF 融合
  ↓ [RERANK_ENABLED]  bge-reranker-v2-m3 → 上位 top_k 件
  → list[dict]
```

### 各手法の実装詳細

**Rerank**: `workers/rerank/` を新設。`Reranker` ABC + `SentenceReranker` 実装。
モデルは `sentence-transformers` の `BAAI/bge-reranker-v2-m3`（多言語対応・日本語◎）。
クラスレベルキャッシュ（`_cache: dict[str, object]`）でサーバプロセス内のモデル重複ロードを防止。
候補数は `max(RERANK_CANDIDATE_K, top_k)` とし、`top_k > RERANK_CANDIDATE_K` になる場合でも
候補不足が起きないよう保証する。

**Hybrid**: `pg_bigm`（日本語トライグラム）と RRF（Reciprocal Rank Fusion）で融合。
`score = Σ 1/(k + rank)`（k=60）。失敗時は contextlib.suppress で素通しし可用性を保つ。

**HyDE**: `_retrieve` の前段で `config.CHAT_MODEL`（qwen2.5:7b）に仮説回答を生成させ、
その文章をベクトル化して検索する。クエリ言語と回答言語のギャップを埋める。
レイテンシが 1〜2 秒増加するトレードオフがある。

**Citation**: システムプロンプトのみ変更。context を `[1] ... \n[2] ...` 形式で番号付きにし
「引用番号を使って回答せよ」と指示する。フロントエンドの変更は不要。

---

## 却下した代替案

| 手法 | 却下理由 |
|---|---|
| GraphRAG | エンティティ関係構築が複雑。書籍単体 RAG には過剰 |
| ColPali | テキスト書籍が対象。図表中心ではないためスキップ |
| CRAG（自動再検索ループ） | 複雑かつレイテンシ増。MVP 後に再評価 |
| Parent-Child チャンク | スキーマ変更を伴う。評価数値が出てから検討 |

---

## トレードオフ・リスク

- `RERANK_ENABLED` は `torch` + `sentence-transformers` 依存（約 2GB）が増える。初回モデルダウンロードが必要。
- `HYDE_ENABLED` はレイテンシが 1〜2 秒増加する。
- `HYBRID_ENABLED` は `pg_bigm` 拡張の Docker イメージカスタマイズが必要。
- `HYBRID_ENABLED` 時のキーワード検索失敗は `contextlib.suppress` でサイレントに握りつぶし、
  ベクトル検索結果にフォールバックする。`pg_bigm` が未インストールの場合も含めて警告ログは出ない。
  「有効にしたはずなのに機能していない」ことに気づきにくいトレードオフがある。
  改善策として `logger.warning` での通知追加を将来課題とする（現時点では可用性を優先）。
- すべてのフラグがデフォルト off のため、既存ユーザへの影響はゼロ。
