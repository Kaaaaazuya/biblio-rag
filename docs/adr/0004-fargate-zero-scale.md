# 0004. Fargate ゼロスケール（方式A）

- 状態: Accepted
- 日付: 2026-06-16
- 関連: [design.md](../design.md) §5、[ADR 0002](0002-execution-platforms.md)

## コンテキスト

個人利用で書籍投入は散発的。① 抽出の Fargate を常駐させるとアイドル課金が無駄になる。
②③ は Lambda なので元々アイドル課金はない。

## 決定

**方式A: SQS 連動オートスケール（最小タスク数=0）** を採用する。

- ECS Service の最小タスク数を `0`、最大を `N`（個人利用なら 1〜3）。
- Application Auto Scaling を `ApproximateNumberOfMessagesVisible + NotVisible` に連動。
- メッセージ ≥1 で 0→N 起動、=0 で →0 停止。

## 理由

- 投入したら勝手に処理して停止する即時性があり、アイドル時は¥0。
- 処理中（NotVisible）も監視に含めることで、処理途中の誤停止を防ぐ。

## 結果

- 良い点: 3フェーズすべてアイドル¥0。
- 悪い点: 投入からタスク起動まで数分のスケール待ち（散発バッチでは許容）。
- 代替: B=EventBridge Scheduler（夜間バッチ・最安・即時性なし）、
  C=Step Functions（順序制御・複雑なリトライが必要になったら）。まず A で開始し、必要に応じ C。
