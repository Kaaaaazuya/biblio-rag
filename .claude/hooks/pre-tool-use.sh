#!/usr/bin/env bash
# PreToolUse フック: 破壊的な git コマンドをブロックする。
# exit 2 でブロック（メッセージが Claude にフィードバックされる）、exit 0 で通過。
set -euo pipefail

input=$(cat)
tool=$(printf '%s' "$input" | jq -r '.tool_name // ""')

# Bash ツール以外はスルー
[ "$tool" = "Bash" ] || exit 0

cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')

case "$cmd" in
  # force push（リモート履歴の破壊）
  *"git push"*"--force"* | *"git push"*"-f "* | *"git push -f")
    echo "🚫 git push --force はブロックされています。リモートの履歴を破壊する可能性があります。"
    exit 2
    ;;
  # hard reset（コミット済み変更の消失）
  *"git reset --hard"*)
    echo "🚫 git reset --hard はブロックされています。コミット済みの変更が失われます。"
    exit 2
    ;;
  # 未追跡ファイルの強制削除
  *"git clean -f"* | *"git clean -fd"* | *"git clean -fx"* | *"git clean -dfx"*)
    echo "🚫 git clean -f はブロックされています。未追跡ファイルが失われます。"
    exit 2
    ;;
  # ローカル変更の強制破棄
  *"git checkout -- "* | *"git restore ."* | *"git restore --staged ."*)
    echo "🚫 ローカル変更を一括破棄するコマンドはブロックされています。"
    exit 2
    ;;
esac

exit 0
