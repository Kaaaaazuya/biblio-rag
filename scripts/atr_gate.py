"""ATR の JSON 結果を評価する pre-commit ゲート。

high 以上（critical/high）の脅威があれば exit 1。ただし「既知の誤検知」は除外する。
ATR に素の無視機能が無いため、ここで file+rule_id 単位に限定して抑制する
（ファイル単位なので、他ファイルで同じルールが当たれば検知は生きる）。詳細は docs/atr.md。

使い方: ATR の `scan ... --json` 出力を stdin で受け取る。
"""

import json
import sys

# 既知の誤検知 "<パス接尾辞>:<rule_id>"。
# allowlist/SKILL.md は方針記述ゆえ credential ルールに反応する（良性）。
IGNORE = [".claude/skills/allowlist/SKILL.md:ATR-2026-00576"]

GATE_SEVERITIES = {"critical", "high"}


def evaluate(data: dict, ignore: list[str] = IGNORE) -> list[tuple[str, str, str, str]]:
    """除外後に残る high+ のマッチを (file, severity, rule_id, title) で返す。"""
    pairs = [tuple(x.split(":", 1)) for x in ignore]
    bad: list[tuple[str, str, str, str]] = []
    for result in data.get("results", []):
        path = result.get("file", "")
        for match in result.get("matches", []):
            rule_id = match.get("rule_id", "")
            severity = match.get("severity", "")
            if any(rule_id == irid and path.endswith(suffix) for suffix, irid in pairs):
                continue
            if severity in GATE_SEVERITIES:
                bad.append((path, severity, rule_id, match.get("title", "")))
    return bad


def main() -> int:
    data = json.load(sys.stdin)
    bad = evaluate(data)
    if bad:
        print("ATR: high+ の脅威を検出（誤検知なら IGNORE に追加）:", file=sys.stderr)
        for path, severity, rule_id, title in bad:
            print(f"  [{severity.upper()}] {rule_id} {title}\n    {path}", file=sys.stderr)
        return 1
    print("ATR: high+ の脅威なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
