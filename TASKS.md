# TASKS — biblio-rag

進捗と残タスクの一覧。詳細設計は [docs/design.md](docs/design.md)、決定の経緯は [docs/adr/](docs/adr/)。
日付は 2026-06-17 時点。

## 現在地

- **MVP 完了**：抽出 → チャンク → 埋め込み → pgvector → 検索が、AWS 抜き・1冊・直列スクリプトで縦串で通る（T1〜T5）。
- MVP 後に **MinIO/S3 化・増分処理・WebUI 足場・ATR・Claude フック**を追加済み。
- テストは全パス（`uv run pytest`）。
- **直近の保留**：Claude フック（ruff 自動整形・通知）は追加済みだが、有効化に **Claude Code の再起動が必要**（下記「直近のアクション」）。

## 直近のアクション

- [ ] **Claude Code を再起動してフックを有効化**（デスクトップアプリは `Cmd+Q` → 再起動 → この会話を履歴から再開）。
  - 補足：本セッション中に ruff 整形フックは既に発火を確認（編集後に未使用 import を自動除去した）。
    通知フック等も含め確実に効かせるなら再起動が安全。

## 通し検証の結果（2026-06-17 / 実書籍 クリーンコードクックブック）

中間成果物（normalized/chunks/DB）を全削除してクリーンに再実行 → 全段成功。
- 抽出 457k 文字 → **1228 チャンク** → 埋め込み・格納まで通過。章/節の付与は正しく機能。
- 検索は topical クエリで妥当（cosine 0.55〜0.67）。章・節クラム表示も機能。
- **気づき（→ 精度チューニング/抽出改善の材料）**:
  - コードブロックが行途中で分割される（例 `mparator()).sorted();`）。コード塊の扱いは要検討。
  - 用語集など一部で文字の混ざり/順序乱れ（多段組み読み順の既知限界に該当）。
  - スコアが 0.55〜0.57 に固まるクエリあり → top_k 拡大 or Reranker 検討の余地。

## 完了

### MVP（T1〜T5）
- [x] **T1 インフラ**：docker-compose（db=pgvector / ollama / minio）+ pgvector スキーマ `VECTOR(1024)`。
- [x] **T2 抽出**：PyMuPDF で PDF → 構造つき Markdown（フォント相対で見出し判定、ヘッダ/フッタ除去）。
- [x] **T3 チャンク**：`HeuristicChunker`（`Chunker` 抽象、ADR 0007）。`book_id/title/author/chapter/section/page/text`。
- [x] **T4 埋め込み/格納**：`OllamaEmbedder`（bge-m3, 1024次元）+ `PgVectorStore`（HNSW cosine, UNIQUE 制約）。
- [x] **T5 検索**：クエリ埋め込み → pgvector 近傍検索 → 整形表示。
- [x] **T6 ドキュメント**：design.md / commit-convention.md / quickstart.md / ADR 0001〜0006。

### MVP 後の追加
- [x] リンタ/フォーマッタ Ruff 導入（pre-commit 連携）。
- [x] コミット規約（Conventional Commits）と `/commit`・`/allowlist` スキル。
- [x] 許可なし実行コマンドの allowlist（`.claude/settings.json`）。
- [x] raw PDF を MinIO(S3互換) に置き、extract が S3 から読む（ADR 0008）。
- [x] `upload` に `--title/--author`、メタ未整備でも chunk が止まらない。
- [x] 増分処理：既定で処理済みスキップ、`--force` で洗い替え。
- [x] WebUI 足場：presigned URL で PDF を S3 へ直接アップロードする静的フロント + 最小 API。
- [x] ATR（Agent Threat Rules）でローカル脅威スキャン → pre-commit ゲート（high+、誤検知除外）。
- [x] Claude フック（ruff 自動整形・通知）を追加、チェック系は `/code-review` + pre-commit に集約（ADR 0009）。
- [x] E2E テスト2層（パイプライン=pytest `-m e2e` / WebUI=Playwright `-m webui`）。既定除外・隔離・後始末（ADR 0010）。

## 次の候補（MVP 範囲の磨き込み・優先度順は要相談）

- [x] **実書籍での通し評価**：クリーンコードクックブックで実施（上「通し検証の結果」参照）。
- [ ] **コードブロックの扱い**：抽出/チャンクでコードが行途中分割される問題（通し検証で発覚）。
- [ ] **検索精度のチューニング**：top_k、チャンク長/オーバーラップ、見出し前後の扱い、Reranker 要否。
- [ ] **page 付与の検討**：① がページ境界を中間データに残す拡張（現状 `page=null`、列は保持済み）。
- [ ] **WebUI の実利用確認**：アップロード → extract が拾う導線（必要なら自動トリガ）。※ブラウザ→MinIO は webui E2E で担保済み。
- [ ] （任意）normalized/chunks も S3 へ寄せるか検討（現状は raw=S3 / 中間=ローカル FS）。

## 2nd ステージ（AWS 化）— 記録のみ・MVP 完了後に着手

> MVP では SQS / Lambda / Fargate / LocalStack / AWS を一切使わない方針（CLAUDE.md）。

- [ ] AWS リソース作成（SQS×3+DLQ×3 / S3 / Fargate / Lambda×2 / Aurora pgvector / Secrets Manager）。
- [ ] 設定切替：`Embedder` → `BedrockEmbedder`（Titan V2）、エンドポイントを本番へ（環境変数制御）。
- [ ] `chunks/*.jsonl` を正本に **再埋め込み** → Aurora pgvector へ投入（意味空間が変わるため必須）。
- [ ] 本番モデルでの検索精度を再評価。
- 経緯：ADR [0002](docs/adr/0002-execution-platforms.md) / [0003](docs/adr/0003-messaging-sqs.md) / [0004](docs/adr/0004-fargate-zero-scale.md) / [0006](docs/adr/0006-local-to-aws.md)。

## 既知の限界（MVP / design.md §既知の限界）

- 多段組み PDF の読み順は崩れうる（単段の素直な PDF を想定）。
- 見出しレベルが飛ぶ構成で chapter/section 割り当てがずれうる。
- チャンクの `page` は MVP では `null`。

## Open Questions（design.md §12）

- 回答生成パイプライン（検索 → コンテキスト付与 → 回答）の設計（**スコープ外・別 Doc**）。
- ハイブリッド検索（pgvector + pg_bigm 等）/ Reranker の要否（精度チューニング時に判断）。
- 冊数スケール見込み、同時処理上限 N、処理状況モニタリング（DLQ/キュー深度）。

## スコープ外（やらない）

- 検索クエリ → 回答生成（LLM）、スキャン PDF の OCR、DRM 付き書籍。
