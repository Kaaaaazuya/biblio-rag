# Architecture Decision Records (ADR)

意思決定の経緯を1ファイル1決定で残す。後から参加する人が背景を追えるようにする。

| # | タイトル | 状態 |
|---|---|---|
| [0001](0001-layer-separation.md) | 層分離と中間成果物（normalized/chunks を正本） | Accepted |
| [0002](0002-execution-platforms.md) | 実行基盤の使い分け（①Fargate / ②③Lambda） | Accepted |
| [0003](0003-messaging-sqs.md) | メッセージングに SQS を採用 | Accepted |
| [0004](0004-fargate-zero-scale.md) | Fargate ゼロスケール（方式A） | Accepted |
| [0005](0005-ollama-dev-embeddings.md) | 開発埋め込みに Ollama を採用 | Accepted |
| [0006](0006-local-to-aws.md) | 開発ローカル完結 → 本番 AWS 移行 | Accepted |
| [0007](0007-chunking-strategy.md) | ② チャンク戦略（ヒューリスティック採用・AI は将来差し替え） | Accepted |
| [0008](0008-object-storage-minio.md) | raw PDF をオブジェクトストレージに（開発 MinIO / 本番 S3） | Accepted |
| [0009](0009-claude-hooks-and-subagents.md) | フックを最小導入・チェック系は code-review / pre-commit に集約 | Accepted |
| [0010](0010-e2e-testing-strategy.md) | E2E テスト戦略（パイプライン=pytest / WebUI=Playwright） | Accepted |
| [0011](0011-aws-serverless-pipeline.md) | AWS サーバーレスパイプライン（2nd ステージ） | Accepted |
| [0012](0012-chat-webui.md) | 最小 RAG チャット UI の追加（Ollama + SSE） | Accepted |

0001〜0006 は既存 Design Doc の決定を ADR 化したもの。
