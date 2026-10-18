from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from model_bisect.engine import bisect

pytestmark = pytest.mark.slow

try:
    import sklearn  # noqa: F401
    has_sklearn = True
except ImportError:
    has_sklearn = False


@pytest.mark.skipif(not has_sklearn, reason="scikit-learn is not installed in the environment")
def test_sklearn_demo_bisect(tmp_path: Path):
    repo_dir = tmp_path / "sklearn_demo_repo"
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "make_sklearn_repo.sh"

    # Generate the demo repo using the generator script
    subprocess.run(
        ["bash", str(script_path), str(repo_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Read commits in chronological order (c0 to c11)
    rev_list_proc = subprocess.run(
        ["git", "rev-list", "--topo-order", "--reverse", "main"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    )
    commits = [c.strip() for c in rev_list_proc.stdout.strip().splitlines() if c.strip()]
    assert len(commits) == 12, f"Expected 12 commits, got {len(commits)}"

    good_commit = commits[0]
    bad_commit = commits[11]
    expected_culprit = commits[6]

    # Run bisect using the repo configuration
    result = bisect(
        repo_path=repo_dir,
        good_commit=good_commit,
        bad_commit=bad_commit,
        eval_command="python train.py",
        metric_name="accuracy",
        threshold=0.95,
        direction="higher",
        repeat_count=1,
        timeout=60.0,
    )

    assert result.status == "found"
    assert result.first_bad_commit == expected_culprit
    assert result.monotonicity_verified is True
    assert result.total_eval_count > 0
