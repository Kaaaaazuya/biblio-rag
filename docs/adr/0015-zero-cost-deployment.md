# 0015. 無課金デプロイ構成の決定

- 状態: Accepted
- 日付: 2026-07-04
- 関連: [ADR 0006](0006-local-to-aws.md)、[ADR 0008](0008-object-storage-minio.md)、[ADR 0011](0011-aws-serverless-pipeline.md)、Issue #39〜#44

## コンテキスト

個人開発のため、ローカル以外で使えるようにするデプロイ構成は**アイドル時課金ゼロ**を第一目標にしたい。
ADR 0006/0011 は本番を AWS（Bedrock + Aurora Serverless v2 + Lambda/SQS/S3）と想定していたが、
Aurora Serverless v2 は 0 ACU 設定でも最低課金が発生し、RDS Proxy・VPC エンドポイントも
月 $14〜32 かかる（ADR 0011 のコスト見積もり参照）。**ベクトル DB だけがアイドル課金ゼロを満たせない要素**
だったため、ベクトル DB のみ非 AWS の無料枠サービスに置き換え、それ以外（埋め込み・生成・ホスティング・
オブジェクトストレージ）は既存の ADR 0006/0011 の AWS 構成をそのまま踏襲する。

## 決定

| 要素 | 選定 | 備考 |
|------|------|------|
| ベクトル DB | **Neon**（サーバレス Postgres、非 AWS） | Aurora は真のゼロアイドルを満たせないため唯一の例外 |
| 埋め込み | **AWS Bedrock**（Titan V2、1024次元） | ADR 0006 を踏襲。意味空間が Ollama `bge-m3` と異なるため**再埋め込みが必須** |
| 生成 | **AWS Bedrock**（チャットモデルは #41/#42 で選定） | ストリーミングは `invoke_model_with_response_stream` を使用 |
| アプリホスティング | **AWS Lambda**（Function URL + レスポンスストリーミング、Lambda Web Adapter 経由で FastAPI をそのまま動かす） | Fargate/App Runner は常時起動コストが発生するため不採用（ADR 0011 で Fargate は既に却下済み） |
| 取り込みパイプライン | **AWS Lambda × 3 + SQS**（ADR 0011 の既存設計） | 変更なし |
| オブジェクトストレージ | **AWS S3** | ADR 0008 で計画済みの本番先を踏襲 |

### ベクトル DB: Neon（唯一の非 AWS 要素）

- 無料枠: 0.5GB ストレージ・月 100 CU-hours・**5分無操作で自動サスペンド → 次の接続で自動レジューム**。
- pgvector を含む 80+ 拡張に対応。既存スキーマ（`VECTOR(1024)` + HNSW + cosine）・`PgVectorStore` の
  SQL 実装をそのまま流用できる。全文検索（`tsvector`/`pg_trgm`）は Postgres 標準機能のため拡張不要で
  ハイブリッド検索の既存挙動も維持できる。接続文字列は `DATABASE_URL` を差し替えるだけ
  （`workers/config.py:database_url()` が対応済み）。ただし、Lambda からの並行接続によるコネクション枯渇を防ぐため、本番環境では Neon が提供するコネクションプーラー（PgBouncer）経由の接続文字列（`-pooler` を含む URL）を使用することを考慮する必要がある。
- Aurora Serverless v2 を選ばなかった理由: 0 ACU 設定でも最低課金が発生し、RDS Proxy を挟む構成では
  さらに月 $14〜32 のネットワークコストが乗る（ADR 0011）。個人開発でアイドル時課金ゼロを守れる
  マネージド Postgres が AWS 内に存在しないため、ここだけ非 AWS の Neon を採用する。

### 埋め込み・生成: AWS Bedrock（ADR 0006 を踏襲）

- 埋め込みは Titan V2（1024次元、スキーマと一致）。ただし Ollama `bge-m3` とは別モデルのため
  **意味空間が異なり、再埋め込みが必須**（ADR 0006 が既に受け入れていた制約をそのまま維持）。
- 生成モデルの具体機種は本 ADR のスコープ外とし、#41（チャット生成バックエンドの抽象化）・
  #42（無課金/AWS バックエンド対応）で選定する。Bedrock はどのモデルでも
  `invoke_model_with_response_stream` によるストリーミングに対応するため、既存の SSE 出力方式を維持できる。
- 料金はトークン従量課金でアイドル時課金は発生しない。

### アプリホスティング: AWS Lambda（Function URL + レスポンスストリーミング）

- Lambda Function URL の response streaming モードを使い、**Lambda Web Adapter** 経由で既存の
  FastAPI アプリ（`webui/server.py`）をコード変更なしでそのまま動かす。SSE もストリーミングレスポンス
  としてそのまま透過する。
- 無料枠: 月 100万リクエスト + 400,000 GB秒（永続的な Always Free 枠、期限なし）。リクエストが無ければ
  課金ゼロ。
- Fargate・App Runner を採用しなかった理由: いずれも常時起動 or 最小インスタンスの課金が発生し、
  月数冊・低頻度アクセスの個人利用ではアイドルコストが無視できない（ADR 0011 で Fargate は
  「アイドルコストが発生し月数冊の処理頻度には不向き」として既に却下済み。App Runner も同様の理由で不採用）。
- **注意点（#44 と関係）**: 現行の `/api/ingest` はレスポンス返却後にバックグラウンドで取り込み処理を
  同一プロセスで実行しているが、Lambda はレスポンス返却後に実行環境が凍結されるためこの方式は動かない。
  取り込みは ADR 0011 で設計済みの S3 イベント駆動 Lambda×3 パイプラインに委譲し、WebUI の Lambda は
  リクエスト応答（presign 発行・ステータス参照・チャット）のみを担当する（#44 の対応方針を裏付ける）。

### 取り込みパイプライン: 変更なし（ADR 0011 を踏襲）

- S3 イベント通知 → SQS → Lambda×3（extract/chunk/embed）の構成はそのまま採用する。

### オブジェクトストレージ: AWS S3（ADR 0008 を踏襲）

- raw/normalized/chunks すべて S3 に置く。boto3 の接続先を本番では空（AWS S3 を指す）にするだけで
  既存の `ObjectStore`（`workers/storage.py`）をそのまま流用できる。presigned PUT/GET・CORS も
  標準機能で対応済み。

## 理由（まとめ）

- ベクトル DB 以外は ADR 0006/0011 で既に設計・一部実装済み（Lambda ハンドラ、Terraform、S3 連携）の
  AWS 構成をそのまま活かせるため、実装済み資産の再利用性が最大化される。
- ベクトル DB だけが「AWS 内にアイドル課金ゼロのマネージド Postgres が存在しない」という制約に当たるため、
  ここのみ Neon（pgvector 互換）に置き換える。`PgVectorStore` の SQL・スキーマは書き換え不要。
- Bedrock・S3・Lambda はいずれも従量課金でアイドル時課金が発生しないため、「Neon 以外は AWS」という
  制約と「アイドル課金ゼロ」という目標を両立できる。

## 却下した代替

- **Aurora Serverless v2（ベクトル DB）**: 0 ACU でも最低課金・RDS Proxy 込みで月 $14〜32。アイドル課金ゼロを
  満たせないため、ここだけ非 AWS の Neon を採用する理由になった。
- **Fargate（アプリホスティング）**: 常時起動コストが発生する。ADR 0011 で既に却下済み（本 ADR でも踏襲）。
- **App Runner（アプリホスティング）**: 最小インスタンスの常時課金が発生するため不採用。

## 結果

- 良い点: ベクトル DB 以外は既存の ADR 0006/0011 実装資産（Lambda ハンドラ、Terraform、S3 連携、
  `BedrockEmbedder` 想定）をそのまま活かせる。変更点がベクトル DB の接続先切り替えのみに閉じるため
  移行コストが小さい。
- 悪い点/注意点:
  - Ollama `bge-m3` → Bedrock Titan V2 で意味空間が変わるため、本番投入時は既存の `chunks/*.jsonl`
    からの**再埋め込みが必須**（ADR 0006 と同じ制約）。
  - Neon の無料枠（0.5GB ストレージ・月100 CU-h）を超えた場合は Aurora への切替を再検討する。
  - Neon の自動サスペンド（5分無操作）および Lambda のコールドスタートにより、アイドル状態からの初回リクエスト時に数秒〜十数秒の遅延（コールドスタート）が発生する可能性がある。
  - Lambda Function URL のレスポンスストリーミングは Lambda Web Adapter の設定・SSE の透過確認が
    実装時に必要（#41 で検証）。
  - Lambda のリクエスト後 CPU 凍結により、取り込みのバックグラウンド処理を WebUI Lambda 内で
    完結させることはできない → #44 で ADR 0011 の S3 イベント駆動パイプラインへの分離が必須。
  - 認証（#40）・生成バックエンド抽象化（#41）・接続まわり（#43, #22）は本 ADR の対象外で、
    各子 issue で個別に実装する。
- 本 ADR は ADR 0006/0011 を**ベクトル DB のみ Aurora → Neon に置き換える**形で補完するものであり、
  それ以外の AWS 構成（Bedrock・S3・Lambda×3・SQS）は無効化されない。

## 調査メモ（2026-07-04 時点、根拠）

- Neon: 無料枠 0.5GB/100 CU-h、5分でサスペンド、pgvector 含む80+拡張対応。
  ([neon.com/pricing](https://neon.com/pricing), [neon.com blog](https://neon.com/blog/how-to-make-the-most-of-neons-free-plan))
- Supabase（検討したが不採用）: 無料枠 500MB DB、7日無操作で一時停止・手動再開必須（90日超で復元不可）。
  Neon は自動レジュームのためこちらを採用しなかった。
  ([supabase.com/docs](https://supabase.com/docs/guides/platform/free-project-pausing))
- AWS Lambda Function URL: レスポンスストリーミング対応、無料枠は月100万リクエスト+400,000GB秒（永続）。
- AWS Bedrock: トークン従量課金でアイドル課金なし。`invoke_model_with_response_stream` でストリーミング対応。
