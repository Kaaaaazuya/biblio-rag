#!/usr/bin/env bash
# Notification フック: Claude が入力待ち/確認待ちになったらデスクトップ通知する。
# 個人開発で docker 起動・埋め込み・docker pull 等の長時間タスクを別作業で待つ用。
set -euo pipefail

msg=$(cat | jq -r '.message // "Claude が入力待ちです"')

# macOS
if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"$msg\" with title \"Claude Code · biblio-rag\"" >/dev/null 2>&1 || true
fi

# Linux (notify-send があれば)
if command -v notify-send >/dev/null 2>&1; then
  notify-send "Claude Code · biblio-rag" "$msg" || true
fi
exit 0
