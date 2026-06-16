#!/usr/bin/env bash
# pre-commit ゲート: .claude/skills を ATR でスキャンし、high 以上の脅威があればコミットを止める。
# 既知の誤検知は scripts/atr_gate.py の IGNORE で除外。詳細は docs/atr.md。
#
# 実体は atr-scan.sh（オフライン・--no-report）の --json 出力を atr_gate.py で評価するだけ。
# Docker が起動している必要がある（.claude/skills 配下を変更したコミット時のみ実行される）。
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
ATR_SEVERITY=high "$here/atr-scan.sh" --json 2>/dev/null | python3 "$here/atr_gate.py"
