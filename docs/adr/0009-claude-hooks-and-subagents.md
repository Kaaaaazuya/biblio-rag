# 0009. Claude のフックを最小導入し、チェック系は code-review / pre-commit に集約

- 状態: Accepted
- 日付: 2026-06-17
- 関連: [CLAUDE.md](../../CLAUDE.md)、[ATR](../atr.md)、[ADR 0008](0008-object-storage-minio.md)

## コンテキスト

開発体験を上げるため Claude Code の Sub-agents / Hooks 導入を検討した。一般的なガイドは
チーム運用・npm 前提（black / prettier / npx / npm test、code-reviewer・通知・各種ガード等）で、
本プロジェクトは **個人開発・npm 非依存・uv 管理**であり、かつ既に
**gitleaks（commit 時の秘密ブロック）/ Ruff pre-commit / ATR pre-commit / allowlist（permission の ask）/
`/code-review ultra`** を持つ。

検討の過程で、レビュー/監査を担う仕組みが複数になりうることが分かった。サブエージェント
（security-auditor 等）の**自動委譲はモデル判断に依存し「必ず走る」保証がない**一方、pre-commit と
`/code-review` は決定的・明示的に走る。チェック責務を分散させると、どこが最終ゲートか曖昧になる。

## 決定

1. **フックは最小2つだけ導入**（コミット対象 `.claude/settings.json`）:
   - **ruff 自動整形**（PostToolUse, `Edit|Write|MultiEdit`）:
     [.claude/hooks/ruff-format.sh](../../.claude/hooks/ruff-format.sh)。編集された `.py` に
     `uv run ruff format` + `uv run ruff check --fix`（npm 非依存）。常に exit 0（非ブロック）。
   - **デスクトップ通知**（Notification）: [.claude/hooks/notify.sh](../../.claude/hooks/notify.sh)。

2. **チェック/レビュー/監査系は集約する**:
   - **決定的ゲート（必ず走る）= pre-commit の gitleaks / Ruff / ATR**。秘密情報・書籍本文の漏洩防止と
     スキル改ざん検知はここで担保する。
   - **レビュー/セキュリティ監査 = `/code-review`（`ultra`）に寄せる**（ユーザー起動・明示的）。
   - **専用の security-auditor サブエージェントは採用しない**（自動委譲が不確実で、上記と責務が重複するため）。

## 理由

- **ruff 整形フック**: 「commit 時に ruff-format が走って再 stage になる」摩擦を編集直後に潰せる。
  最終ゲートは pre-commit なので非ブロックで十分。
- **通知**: docker 起動・埋め込み・docker pull 等の長時間待ちが多く、個人開発で実利が大きい。
- **集約**: 「必ず効く層（pre-commit）」と「明示的に呼ぶ層（/code-review）」に二分し、
  不確実な自動委譲に依存しない。チェックの入口が一本化され、過剰実装も避けられる。

## 見送ったもの（過剰実装回避 / 既存と重複）

- security-auditor / code-reviewer サブエージェント → `/code-review` + pre-commit に集約。
- secret-guard・.env 読取ガード → gitleaks が commit でブロック済み。
- 危険コマンドガード → allowlist で破壊的コマンドは既に「ask」。
- test-writer / pr-summarizer / doc-writer / session-start / audit-log / debugger → 個人開発で費用対効果が低い。

## 結果

- 良い点: 整形の手戻りが減り、待ち時間が可視化される。チェック責務が pre-commit と /code-review に
  一本化され、「どこで止まるか」が明確。
- 悪い点: フックは Docker/CI と違い各自の環境依存（macOS 通知 / `jq` / `uv` 前提）。
  **フックはセッション開始時に読み込まれるため、追加後は Claude Code の再起動（または `/hooks` 再読込）が必要**。
- 継ぎ目: 2nd ステージで認証・Secrets まわりが増えたら、監査強化は `/code-review` の観点追加か
  pre-commit ルール追加で対応する（サブエージェントは再導入しない方針）。
