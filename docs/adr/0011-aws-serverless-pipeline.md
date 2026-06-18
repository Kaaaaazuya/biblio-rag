# 0011. AWS 2nd ステージ：サーバーレスパイプライン設計

- 状態: Accepted
- 日付: 2026-06-17
- 関連: [ADR 0002](0002-execution-platforms.md)、[ADR 0003](0003-messaging-sqs.md)、[ADR 0004](0004-fargate-zero-scale.md)、[ADR 0006](0006-local-to-aws.md)

## コンテキスト

MVP（ローカル直列スクリプト）が完了し、AWS 移行設計を固める。
要件：

- WebUI は S3 + CloudFront の静的配信で完結させる（常時起動サーバー不要）。
- 書籍取り込みは非同期・イベント駆動で実行する。
- アイドルコストを最小化する（月数冊の処理頻度）。
- 開発（Ollama + Docker pgvector）→ 本番（Bedrock + Aurora）を設定切替で移行できる。

## 決定

### フロントエンド

**S3 + CloudFront** による静的配信のみ。バックエンドサーバーは持たない。
API 呼び出し（presign・search）はすべて API Gateway + Lambda を経由する。

### 取り込みパイプライン

S3 イベント通知をトリガーとした **Lambda × 3 の SQS 連鎖**とする。Fargate は使わない。

```
ブラウザ
  → PUT s3://bucket/raw/{book_id}.pdf  （presigned URL 直接 PUT）
  → S3 Event(raw/)        → SQS(raw)    → λ-extract → S3 normalized/
  → S3 Event(normalized/) → SQS(norm)   → λ-chunk   → S3 chunks/
  → S3 Event(chunks/)     → SQS(chunks) → λ-embed   → Aurora pgvector（RDS Proxy 経由）
```

**SQS メッセージ契約**: 各ステージは **S3 イベント通知**でトリガーする（Lambda が次段へ明示的に SQS send しない）。
プレフィックス（raw/ / normalized/ / chunks/）ごとに S3 イベント → 対応 SQS を設定する。
利点: 全ステージが S3 イベント形式に統一でき、Lambda に SQS 送信権限/コードが不要。

**メタデータ（title/author）の受け渡し**: PDF の **S3 object metadata** に載せる。
presign 発行時に `Metadata` を指定し、ブラウザは `x-amz-meta-*` ヘッダ付きで PUT する。
S3 object metadata は **US-ASCII 限定**のため、日本語の title/author は **URL エンコードして格納し、Lambda 側でデコード**する。
λ-extract が `head_object` で取得し、normalized/ 書き出し時に引き継ぐ（後段はサイドカー JSON 不要）。

**Lambda のサイジング目安**

| 関数 | パッケージ | メモリ | タイムアウト | SQS Visibility |
|------|-----------|--------|-------------|----------------|
| λ-extract | コンテナイメージ（pymupdf） | 1.5 GB | 5 分 | 30 分 |
| λ-chunk | zip | 512 MB | 1 分 | 6 分 |
| λ-embed | zip | 512 MB | 10 分 | 60 分 |

SQS Visibility Timeout = Lambda タイムアウト × 6（AWS 推奨）。

### DLQ と冪等性

各 SQS キューに DLQ を付ける（maxReceiveCount=3）。DLQ 到達時は EventBridge → SNS で通知。

**Bedrock 逐次呼び出し対策（JSONL 分割）**: `BedrockEmbedder` はバッチ API 非対応で 1 チャンクずつ
`invoke_model` する。1228 チャンク級の書籍では逐次だと 400〜600s かかり λ-embed の 10 分制限に余裕がない。
対策として **chunks JSONL を N チャンクごとに分割**して S3 に書き出し（λ-chunk 側）、分割ファイル単位で
λ-embed を並列起動する。1 起動あたりの処理量を抑えて確実にタイムアウト内に収める。

**洗い替えと並列の両立**: 分割を並列で embed するため、embed Lambda 内で DELETE + INSERT を行うと
「ある split の DELETE が、先に走った別 split の INSERT を消す」競合が起きる。これを避けるため：

- **fan-out 前に一度だけ DELETE**：λ-chunk が分割 JSONL を書き出す前に `delete_book(book_id)` を実行する。
- λ-embed は **各 split を純粋に upsert（`ON CONFLICT` で冪等）するのみ**。DELETE は持たない。

`PgVectorStore` の `autocommit=True` は λ-chunk の DELETE / λ-embed の upsert を 1 トランザクションに
まとめられるよう、`autocommit=False` オプションを追加して切替可能にする。

```python
# embed Lambda での処理順
with conn.transaction():
    store.delete_book(book_id)       # 旧チャンク全削除
    store.upsert(chunks, vectors)    # 新チャンク一括挿入
# クラッシュ時は ROLLBACK → 旧データが残る（消えない）
```

### データベース

**Aurora Serverless v2（PostgreSQL 互換、0 ACU 最小）** から始める。

選択理由：
- 0 ACU 設定でアイドル時の課金を最小化できる。
- AWS ネイティブで RDS Proxy・Secrets Manager と統合しやすい。
- `PgVectorStore` は標準 psycopg のみ使用（Aurora 固有 API なし）。

Lambda → Aurora の接続は **RDS Proxy** を挟む（Lambda の大量コネクション生成によるプール枯渇防止）。

**Neon（サーバーレス Postgres）への移行パス**を保持する。
`DATABASE_URL` 環境変数を差し替えるだけでコード変更ゼロで移行できる。
Neon への移行判断目安：RDS Proxy コストが Aurora 節約分を上回る場合（月 $10 超）。

### 埋め込みモデルのバージョン管理

`chunks` テーブルに `embed_model TEXT` カラムを追加する。
用途：再埋め込み時の対象特定（`WHERE embed_model != 'new-model'`）。

`is_current` フラグは現規模（月数冊）では不要。モデル変更時は全チャンク再埋め込みで対応する。
大規模化（書籍数百冊超）の段階で is_current ＋ ブルーグリーン移行を検討する。

## コスト見積もり（クリーンコードクックブック相当、795 チャンク）

| 項目 | 1冊あたり | 月固定 |
|------|----------|--------|
| Lambda 実行 | ~$0.002（Free Tier 内） | - |
| Bedrock Titan V2 埋め込み | ~$0.007（350K tokens） | - |
| S3 | ~$0.001 | ~$0.5 |
| Aurora Serverless v2 | - | ~$3〜8 |
| RDS Proxy | - | 要確認（ACU × $0.015/h） |
| VPC エンドポイント（下記参照） | - | ~$14（Interface×2）or ~$32（NAT） |
| **合計（月 5 冊想定）** | **~$0.01** | **~$30〜40** |

**VPC ネットワークコスト**: Lambda を RDS Proxy / Aurora に繋ぐため VPC 内に置く必要がある。
VPC 内 Lambda が S3 / Bedrock / Secrets Manager へ出る経路が要る：

- S3 = **Gateway エンドポイント（無料）**
- Bedrock + Secrets Manager = **Interface エンドポイント 各 ~$7/月**（計 ~$14）、または **NAT Gateway ~$32/月**

月数冊・低トラフィックなら Interface エンドポイント 2 本（~$14）が NAT より安い。
このコストは Neon 移行判断（RDS Proxy が Aurora 節約分を上回るか）の前提に含めること。

## 却下した代替

- **Fargate（常時起動）**: アイドルコストが発生する。月数冊の処理頻度には不向き。
- **Fargate（Lambda の代替）**: テキスト埋め込み PDF の抽出は 30〜60 秒で収まり、Lambda 15 分制限に余裕がある。Fargate は不要。
- **RDS t4g.small（固定インスタンス）**: ~$23/月固定。Aurora Serverless v2 の方がアイドル時に安い。
- **Step Functions**: 現規模では SQS 連鎖で十分。失敗可視化が必要になった段階で検討する。
- **フロントから `/api/ingest` を叩く**: S3 イベント通知の方が疎結合で信頼性が高い。

## IaC と進め方

- **インフラは Terraform で管理する**（コンソール手作業は不可・再現性のため）。
- **段階的に進める**：まず AWS 不要のコード層（`ObjectStore` 拡張・`PgVectorStore` txn モード・
  ハンドラ本体）をローカル + moto で実装・テストし、動いてから Terraform で AWS リソースを作る。

## ローカル検証（Terraform + LocalStack）

実 AWS の前に LocalStack で一気通貫を確認した（`infra/terraform/` / `scripts/2nd_local.sh` /
`tests/test_localstack_e2e.py`）。S3(raw) PUT → SQS → λ-extract → λ-chunk → λ-embed → pgvector が通る。

LocalStack **community** の制約と対応：

- **Lambda はコンテナイメージ非対応（Pro 機能）** → ローカルは **zip** でデプロイ。
  zip は `aarch64-manylinux2014 / cp312` の wheel（pymupdf・psycopg[binary] 等）を同梱。
  本番 AWS では extract のみコンテナイメージ化も選べる（zip 250MB 制限のため）。
- **ランタイム上限 python3.12** → ローカルは 3.12。`itertools.batched` の `strict=` は 3.13+ なので付けない。
  本番 AWS は python3.13 を使う。
- **Aurora / RDS Proxy 非対応** → ローカル DB は既存 pgvector コンテナを流用（`DATABASE_URL` 差し替えのみ）。
- spawn される Lambda は `LAMBDA_DOCKER_NETWORK` で compose 網に参加し、db / ollama / localstack を
  サービス名で解決する。

## 結果

- 良い点: アイドルコストほぼゼロ・AWS ネイティブ・既存 workers/ コードをそのまま Lambda に乗せられる。
- 注意点: RDS Proxy のコスト次第では Neon 移行が有利になる。早期にコストを計測して判断する。
- 注意点: Aurora のコールドスタート（スケールアップ）時に RAG クエリのレイテンシスパイクが発生しうる。
  RAG クエリが増えてアイドルしなくなれば自然に解消する。
