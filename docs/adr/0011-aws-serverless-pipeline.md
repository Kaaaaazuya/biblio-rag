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
  → S3 Event Notification → SQS(raw)
  → λ-extract  → S3 normalized/  → SQS(norm)
  → λ-chunk    → S3 chunks/      → SQS(chunks)
  → λ-embed    → Aurora pgvector（RDS Proxy 経由）
```

**Lambda のサイジング目安**

| 関数 | パッケージ | メモリ | タイムアウト | SQS Visibility |
|------|-----------|--------|-------------|----------------|
| λ-extract | コンテナイメージ（pymupdf） | 1.5 GB | 5 分 | 30 分 |
| λ-chunk | zip | 512 MB | 1 分 | 6 分 |
| λ-embed | zip | 512 MB | 10 分 | 60 分 |

SQS Visibility Timeout = Lambda タイムアウト × 6（AWS 推奨）。

### DLQ と冪等性

各 SQS キューに DLQ を付ける（maxReceiveCount=3）。DLQ 到達時は EventBridge → SNS で通知。

embed Lambda はチャンクの洗い替え時に旧データが残らないよう **トランザクション内で DELETE + INSERT** を行う。
`PgVectorStore` の `autocommit=True` は embed Lambda 向けに無効化する。

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
| **合計（月 5 冊想定）** | **~$0.01** | **~$15〜25** |

## 却下した代替

- **Fargate（常時起動）**: アイドルコストが発生する。月数冊の処理頻度には不向き。
- **Fargate（Lambda の代替）**: テキスト埋め込み PDF の抽出は 30〜60 秒で収まり、Lambda 15 分制限に余裕がある。Fargate は不要。
- **RDS t4g.small（固定インスタンス）**: ~$23/月固定。Aurora Serverless v2 の方がアイドル時に安い。
- **Step Functions**: 現規模では SQS 連鎖で十分。失敗可視化が必要になった段階で検討する。
- **フロントから `/api/ingest` を叩く**: S3 イベント通知の方が疎結合で信頼性が高い。

## 結果

- 良い点: アイドルコストほぼゼロ・AWS ネイティブ・既存 workers/ コードをそのまま Lambda に乗せられる。
- 注意点: RDS Proxy のコスト次第では Neon 移行が有利になる。早期にコストを計測して判断する。
- 注意点: Aurora のコールドスタート（スケールアップ）時に RAG クエリのレイテンシスパイクが発生しうる。
  RAG クエリが増えてアイドルしなくなれば自然に解消する。
