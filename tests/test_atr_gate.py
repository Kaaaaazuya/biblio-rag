"""ATR pre-commit ゲートの評価ロジックのテスト（Docker 不要）。"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "atr_gate", Path(__file__).parent.parent / "scripts" / "atr_gate.py"
)
atr_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atr_gate)


def _result(file, rule_id, severity):
    return {"results": [{"file": file, "matches": [{"rule_id": rule_id, "severity": severity}]}]}


def test_high_triggers():
    data = _result("/scan/.claude/skills/foo/SKILL.md", "ATR-2026-99999", "high")
    bad = atr_gate.evaluate(data, ignore=[])
    assert len(bad) == 1 and bad[0][1] == "high"


def test_medium_does_not_trigger():
    data = _result("/scan/.claude/skills/foo/SKILL.md", "ATR-2026-99999", "medium")
    assert atr_gate.evaluate(data, ignore=[]) == []


def test_known_false_positive_is_suppressed_for_that_file():
    data = _result("/scan/.claude/skills/allowlist/SKILL.md", "ATR-2026-00576", "critical")
    ignore = [".claude/skills/allowlist/SKILL.md:ATR-2026-00576"]
    assert atr_gate.evaluate(data, ignore=ignore) == []


def test_same_rule_on_other_file_still_detected():
    # 誤検知の除外はファイル単位。別ファイルで同ルールが当たれば検知は生きる
    data = _result("/scan/.claude/skills/other/SKILL.md", "ATR-2026-00576", "critical")
    ignore = [".claude/skills/allowlist/SKILL.md:ATR-2026-00576"]
    bad = atr_gate.evaluate(data, ignore=ignore)
    assert len(bad) == 1 and bad[0][0].endswith("other/SKILL.md")
