#!/usr/bin/env bash
# PostToolUse フック: Claude が編集した .py を Ruff で整形・自動修正する。
# commit 時に ruff-format が走って再 stage になる摩擦を、編集直後に潰すのが狙い。
# npm 非依存方針のため black/prettier は使わず uv run ruff のみ。
# PostToolUse なので結果はブロックしない（常に exit 0）。
set -euo pipefail

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""')

# .py 以外、または存在しないパスは何もしない
case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

# 整形 → lint 自動修正。失敗してもコミット前の pre-commit が最終ゲートなので握りつぶす。
uv run ruff format "$file" >/dev/null 2>&1 || true
uv run ruff check --fix "$file" >/dev/null 2>&1 || true
exit 0
