#!/usr/bin/env bash
# ATR (Agent Threat Rules) でエージェント成果物をローカル・オフラインでスキャンする。
#
# 方針:
#   - npm 非依存: 公式 Docker イメージを使う（プロジェクト方針で npm/npx は使わない）。
#   - 外部報告ゼロ: --no-report（匿名 Threat Cloud 報告の無効化）に加え、
#     --network none でコンテナをネットワーク遮断 → 外部送信が原理的に不可能。
#   - 供給チェーン安全: イメージは digest 固定（更新は IMAGE を貼り替える）。
#
# 使い方:
#   ./scripts/atr-scan.sh                       # .claude/skills を severity=medium でスキャン
#   ATR_SEVERITY=high ./scripts/atr-scan.sh     # 重大度の閾値を変更
#   ATR_TARGET=.mcp.json ./scripts/atr-scan.sh  # 対象を変更（.md=SKILL.md / .json=MCP を自動判別）
#   ./scripts/atr-scan.sh --json                # 追加フラグはそのまま atr に渡る
#
# 注意: セキュリティ方針を“説明”するドキュメント（例 allowlist/SKILL.md）は
#       誤検知（false positive）しやすい。検出は必ず人間がレビューする。詳細は docs/atr.md。
set -euo pipefail

# 公式イメージ（digest 固定）。更新時は `docker pull ghcr.io/agent-threat-rule/agent-threat-rules:latest`
# で取得した digest に差し替える。
IMAGE="ghcr.io/agent-threat-rule/agent-threat-rules@sha256:f116ccdb9efd6b417e0f430a88b5dc75cd56fd295dc756d11172aaa738baae52"

TARGET="${ATR_TARGET:-.claude/skills}"
SEVERITY="${ATR_SEVERITY:-medium}"

exec docker run --rm --network none --platform linux/amd64 \
  -v "$PWD:/scan:ro" "$IMAGE" \
  scan "/scan/${TARGET}" --no-report --severity "${SEVERITY}" "$@"
