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
- [x] **検索精度のチューニング**：チャンク長/オーバーラップのスイープ完了（300/60・500/80・800/120）。**800/120 を新デフォルトに採用**（ハードクエリ MRR: 0.556 → 0.694、+24%）。`scripts/eval_search.py` 追加済み。Reranker は見送り。
- [ ] **コードブロックの扱い**：抽出/チャンクでコードが行途中分割される問題（通し検証で発覚）。
- [x] **page 付与**：extract が `<!-- page:N -->` マーカーを Markdown に挿入、chunk が `page` 列に反映（1-based）。
- [x] **WebUI 自動トリガー**：アップロード後に `/api/ingest` を呼びパイプラインをバックグラウンド起動。ステータスを 2 秒ポーリングで UI に表示。
- [ ] （任意）normalized/chunks も S3 へ寄せるか検討（現状は raw=S3 / 中間=ローカル FS）。

## 2nd ステージ（AWS 化）— 設計確定済み・実装待ち

> MVP では SQS / Lambda / LocalStack / AWS を一切使わない方針（CLAUDE.md）。設計方針は [ADR 0011](docs/adr/0011-aws-serverless-pipeline.md)。
> **進め方**：まず AWS 不要の Phase A（コード層）をローカル + moto で実装・テストし、動いてから Phase B（Terraform で AWS リソース作成）へ。IaC は **Terraform** 指定。

### 済（スキーマ・モデルの先行実装）
- [x] **`embed_model` カラム追加**：`infra/db/002_add_embed_model.sql` 追加済み。`pipeline.py` の `embed_and_store()` で `embed_model` を各レコードに付与。
- [x] **`BedrockEmbedder` 実装**：`workers/embed/bedrock_embedder.py` 追加済み。`EMBED_BACKEND=bedrock` で切替（`make_embedder()` / `active_embed_model()` ファクトリ経由）。

### Phase A：コード層（AWS 不要・ローカル + moto でテスト可）✅ 完了
- [x] **`ObjectStore` 拡張**：`workers/storage.py` に `put_text` / `get_text` / `put_jsonl` / `load_jsonl` / `put_bytes` / `get_meta` を追加（`put_meta` は `put_bytes(metadata=)` に統合）。`tests/test_storage.py`。
- [x] **meta を S3 object metadata で受け渡し**：`get_meta` が title/author を URL デコードして返す（S3 metadata は US-ASCII 限定）。presign 側の `Metadata` 付与は WebUI カットオーバー時（Phase C）。
- [x] **chunk の JSONL 分割出力**：`chunk_handler.SPLIT_SIZE` ごとに分割出力。**fan-out 前に一度だけ `delete_book()`**（並列 embed の競合回避・embed は純粋 upsert）。
- [x] **`workers/lambda_fns/` ハンドラ**：`events` + `extract_handler` / `chunk_handler` / `embed_handler`。`tests/test_lambda_handlers.py`（moto）。※`presign`/`search` ハンドラは WebUI/API Gateway 化時（Phase C）。
- [ ] **`PgVectorStore` トランザクションモード対応**：`autocommit=False` オプション。分割並列設計では不要（chunk が一度だけ DELETE→embed は upsert のみ）。非分割の単一 embed を採る場合に追加。

### Phase B-local：Terraform + LocalStack でローカル検証 ✅ 完了
> LocalStack community で S3→SQS→Lambda→pgvector を一気通貫で確認。ランブック: [docs/2nd-local.md](docs/2nd-local.md)。`scripts/2nd_local.sh` / `tests/test_localstack_e2e.py`（マーカー `localstack`）。
> **community の制約**：Lambda は **zip のみ**（コンテナイメージは Pro）／ランタイム上限 **python3.12**／**Aurora/RDS Proxy 非対応** → ローカル DB は既存 pgvector コンテナを流用。
- [x] **Terraform プロジェクト**：`infra/terraform/`（provider は LocalStack エンドポイント・ダミー資格情報）。
- [x] **S3 バケット + イベント通知**：プレフィックスごと（raw/ norm/ chunks/）に S3 Event → 対応 SQS。
- [x] **SQS × 3 + DLQ × 3**：maxReceiveCount=3・redrive。
- [x] **Lambda × 3（zip・arm64・python3.12）**：1 つの zip を共有し handler で振り分け。SQS イベントソースマッピングで起動。
- [x] **docker-compose に LocalStack 追加**：`LAMBDA_DOCKER_NETWORK=biblio-net` で spawn Lambda が db/ollama/localstack を解決。

### Phase B-aws：実 AWS 化（LocalStack に無い/本番固有のもの）
- [ ] **Terraform state を S3 backend 化**・本番 endpoints へ切替（ダミー資格情報を外す）。
- [ ] **Aurora Serverless v2**：PostgreSQL 互換・0 ACU 最小・pgvector 拡張。
- [ ] **RDS Proxy**：Lambda → Aurora の接続プール。
- [ ] **VPC + エンドポイント**：Lambda を VPC 内に配置。S3=Gateway（無料）、Bedrock + Secrets Manager=Interface 各 ~$7/月（or NAT ~$32/月）。
- [ ] **Secrets Manager**：`DATABASE_URL`・認証情報。
- [ ] **DLQ アラート**：EventBridge → SNS 通知。
- [ ] **Lambda パッケージング（本番）**：extract のみコンテナイメージ化も検討（zip 250MB 制限）。python ランタイムは 3.13 へ。
- [ ] **API Gateway + presign/search Lambda**・**CloudFront + S3 static**（WebUI）。

### Phase C：データ移行・検証・カットオーバー
- [ ] **全書籍の再埋め込み**：`chunks/*.jsonl` を正本に Bedrock で再埋め込み → Aurora へ投入（意味空間が変わるため必須）。
- [ ] **本番モデルでの検索精度評価**：`scripts/eval_search.py` で Titan V2 の MRR を計測。ローカル（bge-m3）の 0.694 と比較。
- [ ] **S3 イベント経路の並行稼働検証**：1 冊を S3 PUT → 自動で extract→chunk→embed が通ることを確認。
- [ ] **`webui/server.py` の `/api/ingest` 削除**（カットオーバー時）：S3 Event → SQS に切り替わったら BackgroundTasks ベースのエンドポイントは不要。
- [ ] **RDS Proxy コスト計測**：実際の ACU × $0.015/h を 1 ヶ月計測。Aurora + Proxy + VPC エンドポイントの合計が Neon 想定を超えるなら移行を検討。

- 経緯：ADR [0002](docs/adr/0002-execution-platforms.md) / [0003](docs/adr/0003-messaging-sqs.md) / [0004](docs/adr/0004-fargate-zero-scale.md) / [0006](docs/adr/0006-local-to-aws.md) / [0011](docs/adr/0011-aws-serverless-pipeline.md)。

## 既知の限界（MVP / design.md §既知の限界）

- 多段組み PDF の読み順は崩れうる（単段の素直な PDF を想定）。
- 見出しレベルが飛ぶ構成で chapter/section 割り当てがずれうる。
- チャンクの `page` は extract のページマーカーから取得するため、マーカーが付かないセクション（表紙など）では `null` になる場合がある。

## Open Questions（design.md §12）

- 回答生成パイプライン（検索 → コンテキスト付与 → 回答）の設計（**スコープ外・別 Doc**）。
- ハイブリッド検索（pgvector + pg_bigm 等）/ Reranker の要否（精度チューニング時に判断）。
- 冊数スケール見込み、同時処理上限 N、処理状況モニタリング（DLQ/キュー深度）。

## スコープ外（やらない）

- 検索クエリ → 回答生成（LLM）、スキャン PDF の OCR、DRM 付き書籍。
