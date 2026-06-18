# 2nd ステージのローカル検証（Terraform + LocalStack）

実 AWS に上げる前に、S3 → SQS → Lambda → pgvector の取り込みパイプラインを
**LocalStack 上で一気通貫**で確認するためのランブック。設計の経緯は
[ADR 0011](adr/0011-aws-serverless-pipeline.md)、タスクは [TASKS.md](../TASKS.md) 参照。

> MVP では LocalStack/AWS を使わない方針（[CLAUDE.md](../CLAUDE.md)）。本書は **2nd ステージ**の作業。

## 構成

```
ブラウザ/CLI
  → PUT s3://biblio/raw/{book_id}.pdf        （メタは S3 object metadata に URL エンコードで）
  → S3 Event(raw/)        → SQS(raw)    → λ-extract → s3 normalized/{id}.md
  → S3 Event(normalized/) → SQS(norm)   → λ-chunk   → s3 chunks/{id}/{n}.jsonl（分割）+ DELETE
  → S3 Event(chunks/)     → SQS(chunks) → λ-embed   → pgvector（upsert）
```

| 役割 | ローカル | 実 AWS（将来） |
|------|---------|---------------|
| S3 / SQS / Lambda | LocalStack(community) | AWS |
| DB | 既存 pgvector コンテナ | Aurora Serverless v2 + RDS Proxy |
| 埋め込み | 既存 Ollama コンテナ（bge-m3） | Bedrock Titan V2 |

- IaC: `infra/terraform/`（provider を LocalStack エンドポイントへ向けている）
- ハンドラ: `workers/lambda_fns/`（`events` + `extract_handler` / `chunk_handler` / `embed_handler`）
- Lambda zip ビルド + デプロイ + 実行: `scripts/2nd_local.sh`

## 使い方

```bash
# 1) インフラ（db / ollama / localstack）を起動。ollama に bge-m3 が必要。
docker compose -f docker/docker-compose.yml up -d

# 2) Lambda zip をビルドして Terraform apply
scripts/2nd_local.sh deploy

# 3) サンプル PDF を投入し、pgvector に入るまで待つ
scripts/2nd_local.sh run

# 後始末
scripts/2nd_local.sh down
```

E2E テスト（要 `deploy` 済み・マーカー `localstack`、既定では除外）:

```bash
uv run pytest -m localstack
```

## LocalStack community の制約と対応（ハマりどころ）

ここはローカル特有の落とし穴。**本番 AWS では当てはまらない**ものが多い。

1. **`localstack:latest` は Pro ライセンストークンを要求して起動失敗する**（exit 55）。
   → community を **`localstack/localstack:3.8`** に固定。S3/SQS/Lambda は community で使える。
2. **`SERVICES` を絞ると Lambda 依存の iam/sts が無効になる**（`Service 'iam' is not enabled`）。
   → `SERVICES` を指定せず全 community サービスをオンデマンドで有効化。
3. **Lambda のコンテナイメージは Pro 機能**（`Container images are a Pro feature`）。
   → ローカルは **zip 形式**でデプロイ。zip に `aarch64-manylinux2014 / cp312` の wheel
   （pymupdf・psycopg[binary]・httpx）を同梱する（`scripts/2nd_local.sh` の `build_zip`）。
4. **Lambda ランタイム上限は `python3.12`**（3.13 は enum 非対応）。
   → ローカルは 3.12。`itertools.batched(strict=...)` は **3.13+** なので付けない
   （`chunk_handler.py` 参照）。本番 AWS は 3.13 を使える。
5. **Aurora / RDS Proxy は community 非対応**。
   → ローカル DB は既存 pgvector コンテナを流用（`DATABASE_URL` 差し替えのみ）。
6. **spawn された Lambda コンテナのネットワーク到達性**。
   → docker-compose の既定ネットワークを `biblio-net` に固定し、LocalStack に
   `LAMBDA_DOCKER_NETWORK=biblio-net` を設定。これで Lambda が `db` / `ollama` /
   `localstack` をサービス名で解決できる。Lambda 内から見た S3 は `http://localstack:4566`。

## トラブルシュート

- **パイプラインが進まない**：`docker logs biblio-localstack` を確認。
  メッセージは maxReceiveCount=3 を超えると DLQ（`biblio-{stage}-dlq`）へ移る。
  再実行は同じ key に再 PUT すれば新しいイベントが出る。
- **メタが空 / chunk が必須エラー**：raw PDF の object metadata（title/author）が
  無い、または URL エンコードされていない。`scripts/2nd_local.sh run` は `%20` 込みで投入する。
- **`embed_model` / 次元エラー**：DB スキーマが古い可能性。`infra/db/002_add_embed_model.sql`
  が適用済みか（`\d chunks`）確認。
- **再現性のため state をリセットしたい**：LocalStack を作り直すと中の状態は消えるので、
  `infra/terraform/terraform.tfstate*` を消してから `scripts/2nd_local.sh deploy` し直す。

## 品質チェック（Terraform）

pre-commit で `infra/terraform/*.tf` 変更時に **fmt / validate / tflint / tfsec** を自動実行する
（`repo: local` の system フック）。事前に各バイナリをインストールしておくこと:

```bash
brew install tfsec                 # 静的セキュリティ解析
# tflint は GitHub リリースから（brew tap が無いため）:
#   curl -sL -o /tmp/tflint.zip \
#     https://github.com/terraform-linters/tflint/releases/latest/download/tflint_darwin_arm64.zip
#   unzip -o /tmp/tflint.zip -d /tmp && install -m0755 /tmp/tflint /opt/homebrew/bin/tflint
```

手動実行:

```bash
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate                 # 要 init 済み
( cd infra/terraform && tflint )
tfsec infra/terraform --minimum-severity HIGH             # ゲートは HIGH+
```

- **tfsec は HIGH 以上でゲート**。medium/low（versioning・access logging・X-Ray tracing・CMK 等）は
  助言扱いで、本番化（Phase B-aws）時に判断する。S3 は public access block + SSE-S3、SQS は
  managed SSE を有効化済み。CMK(KMS) は `#tfsec:ignore` で意図的に見送り（コスト/鍵管理）。
- ネイティブ `terraform test`（`.tftest.hcl`）は **1.6+** 機能で現行 1.5.7 では使えない。
  パイプラインの実挙動は `tests/test_localstack_e2e.py`（マーカー `localstack`）で担保する。
