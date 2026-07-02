"""CI ワークフローの構成テスト。

`.github/workflows/ci.yml` が存在し、以下を自動実行：
- Lint (ruff check, ruff format --check)
- Test (pytest)
- Secret Scanning (gitleaks detect)
- トリガー: push, pull_request
"""

from pathlib import Path

import yaml


def test_ci_workflow_exists():
    """CI ワークフローファイルが存在する。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    assert workflow_file.exists(), ".github/workflows/ci.yml が見つかりません"


def test_ci_workflow_triggers():
    """CI ワークフローが push と pull_request でトリガーされる。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    # YAML では 'on' はブール値 True に解析されるため、両方をチェック
    on_config = workflow.get("on") or workflow.get(True)
    assert on_config is not None, "'on' キーが定義されていません"

    # triggers リストを正規化（型による分岐を避ける）
    if isinstance(on_config, str):
        triggers = [on_config]
    elif isinstance(on_config, list):
        triggers = on_config
    elif isinstance(on_config, dict):
        triggers = list(on_config.keys())
    else:
        triggers = []

    assert "push" in triggers, "push トリガーが定義されていません"
    assert "pull_request" in triggers, "pull_request トリガーが定義されていません"


def test_ci_workflow_has_jobs():
    """CI ワークフローが jobs セクションを持つ。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    assert "jobs" in workflow, "'jobs' セクションが定義されていません"
    assert len(workflow["jobs"]) > 0, "少なくとも1つのジョブが必要です"


def test_ci_workflow_has_lint_job():
    """CI ワークフローが lint ジョブを持つ。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    assert any("lint" in job_name.lower() or "check" in job_name.lower() for job_name in jobs), (
        "lint または check という名前のジョブが見つかりません"
    )


def test_ci_workflow_has_test_job():
    """CI ワークフローが test ジョブを持つ。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    assert any("test" in job_name.lower() for job_name in jobs), (
        "test という名前のジョブが見つかりません"
    )


def test_ci_workflow_has_secret_scan_job():
    """CI ワークフローが secret scanning ジョブを持つ。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    assert any(
        "secret" in job_name.lower() or "gitleaks" in job_name.lower() for job_name in jobs
    ), "secret または gitleaks という名前のジョブが見つかりません"


def test_lint_job_runs_ruff_check():
    """lint ジョブが `uv run ruff check` を実行する。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    lint_job = None
    for name, job in jobs.items():
        if "lint" in name.lower() or "check" in name.lower():
            lint_job = job
            break

    assert lint_job is not None, "lint ジョブが見つかりません"

    # steps の中に "ruff check" が含まれるステップがあるか確認
    steps = lint_job.get("steps", [])
    has_ruff_check = False
    for step in steps:
        if "run" in step and "ruff check" in step["run"]:
            has_ruff_check = True
            break

    assert has_ruff_check, "lint ジョブが 'uv run ruff check' を実行していません"


def test_lint_job_runs_ruff_format_check():
    """lint ジョブが `uv run ruff format --check` を実行する。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    lint_job = None
    for name, job in jobs.items():
        if "lint" in name.lower() or "check" in name.lower():
            lint_job = job
            break

    assert lint_job is not None, "lint ジョブが見つかりません"

    # steps の中に "ruff format --check" が含まれるステップがあるか確認
    steps = lint_job.get("steps", [])
    has_ruff_format = False
    for step in steps:
        if "run" in step and "ruff format" in step["run"] and "--check" in step["run"]:
            has_ruff_format = True
            break

    assert has_ruff_format, "lint ジョブが 'uv run ruff format --check' を実行していません"


def test_test_job_runs_pytest():
    """test ジョブが `uv run pytest` を実行する。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    test_job = None
    for name, job in jobs.items():
        if "test" in name.lower():
            test_job = job
            break

    assert test_job is not None, "test ジョブが見つかりません"

    # steps の中に "pytest" が含まれるステップがあるか確認
    steps = test_job.get("steps", [])
    has_pytest = False
    for step in steps:
        if "run" in step and "pytest" in step["run"]:
            has_pytest = True
            break

    assert has_pytest, "test ジョブが 'pytest' を実行していません"


def test_secret_scan_job_runs_gitleaks():
    """secret scan ジョブが `gitleaks detect` を実行する。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    secret_job = None
    for name, job in jobs.items():
        if "secret" in name.lower() or "gitleaks" in name.lower():
            secret_job = job
            break

    assert secret_job is not None, "secret scan ジョブが見つかりません"

    # steps の中に "gitleaks" が含まれるステップがあるか確認
    # uses または run に gitleaks が含まれる可能性がある
    steps = secret_job.get("steps", [])
    has_gitleaks = False
    for step in steps:
        if "uses" in step and "gitleaks" in step["uses"]:
            has_gitleaks = True
            break
        if "run" in step and "gitleaks" in step["run"]:
            has_gitleaks = True
            break

    assert has_gitleaks, "secret scan ジョブが gitleaks を使用していません"


def test_workflow_runs_on_ubuntu_latest():
    """ワークフローが ubuntu-latest で実行される。"""
    workflow_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    with open(workflow_file) as f:
        workflow = yaml.safe_load(f)

    jobs = workflow["jobs"]
    for name, job in jobs.items():
        if "runs-on" in job:
            assert job["runs-on"] == "ubuntu-latest", (
                f"ジョブ '{name}' が ubuntu-latest で実行されていません"
            )
