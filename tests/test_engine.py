from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from model_bisect.engine import bisect, validate_found_result
from model_bisect.models import BisectResult, CommitEvaluation



def _run_git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def create_git_repo_with_commits(
    repo_dir: Path,
    num_commits: int = 20,
    regression_commit: int = 12,
    good_metric: float = 0.98,
    bad_metric: float = 0.82,
) -> tuple[Path, list[str]]:
    """Initialize a git repo with num_commits. Commits < regression_commit

    have good_metric, commits >= regression_commit have bad_metric.
    """
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run_git(repo_dir, "init")
    _run_git(repo_dir, "config", "user.name", "ModelBisect Tester")
    _run_git(repo_dir, "config", "user.email", "tester@example.com")
    _run_git(repo_dir, "config", "commit.gpgSign", "false")

    commits: list[str] = []
    for i in range(num_commits):
        metric_val = bad_metric if i >= regression_commit else good_metric
        eval_script = repo_dir / "eval.py"
        eval_script.write_text(f"print('accuracy={metric_val}')\n", encoding="utf-8")

        # Track commit index file to ensure each commit is unique
        (repo_dir / "version.txt").write_text(f"commit_{i}\n", encoding="utf-8")

        _run_git(repo_dir, "add", ".")
        _run_git(repo_dir, "commit", "-m", f"commit {i}: metric {metric_val}")
        head_sha = _run_git(repo_dir, "rev-parse", "HEAD")
        commits.append(head_sha)

    return repo_dir, commits


def test_bisect_regression_commit_12_loop_20(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Test creating a repo with 20 commits where commit 12 regresses.

    Asserts commit 12 is found, eval count <= ceil(log2(20)) + 2, and the
    working tree remains unchanged. Ran in a loop of 20 to confirm
    determinism.
    """
    num_commits = 20
    regression_idx = 12
    max_eval_count = math.ceil(math.log2(num_commits)) + 2  # ceil(4.32) + 2 = 7

    # Run in a loop of 20 iterations to confirm determinism
    for iteration in range(20):
        test_dir = tmp_path_factory.mktemp(f"repo_run_{iteration}")
        repo, commits = create_git_repo_with_commits(
            test_dir,
            num_commits=num_commits,
            regression_commit=regression_idx,
            good_metric=0.98,
            bad_metric=0.82,
        )

        good_sha = commits[0]
        bad_sha = commits[-1]
        expected_bad_sha = commits[regression_idx]

        # Record initial working tree state
        head_before = _run_git(repo, "rev-parse", "HEAD")
        status_before = _run_git(repo, "status", "--porcelain")
        worktrees_before = _run_git(repo, "worktree", "list")

        eval_cmd = f"{sys.executable} eval.py"
        result: BisectResult = bisect(
            repo_path=repo,
            good_commit=good_sha,
            bad_commit=bad_sha,
            eval_command=eval_cmd,
            metric_name="accuracy",
            threshold=0.95,
            direction="higher",
            repeat_count=1,
        )

        # 1. Assert engine found commit 12's hash
        assert result.status == "found"
        assert result.first_bad_commit == expected_bad_sha

        # 2. Assert eval count <= ceil(log2(20)) + 2
        assert result.total_eval_count <= max_eval_count

        # 3. Assert test repo's working tree is completely unchanged
        head_after = _run_git(repo, "rev-parse", "HEAD")
        status_after = _run_git(repo, "status", "--porcelain")
        worktrees_after = _run_git(repo, "worktree", "list")

        assert head_after == head_before
        assert status_after == status_before
        assert worktrees_after == worktrees_before


def test_non_monotonic_detection(tmp_path: Path) -> None:
    """Test that alternating good/bad/good commits returns status

    'non_monotonic'.
    """
    repo = tmp_path / "repo_non_monotonic"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "ModelBisect Tester")
    _run_git(repo, "config", "user.email", "tester@example.com")
    _run_git(repo, "config", "commit.gpgSign", "false")

    # Commit 0: 0.98 (good)
    # Commit 1: 0.80 (bad)
    # Commit 2: 0.99 (good)
    # Commit 3: 0.80 (bad)
    metrics = [0.98, 0.80, 0.99, 0.80]
    commits: list[str] = []
    for i, m in enumerate(metrics):
        (repo / "eval.py").write_text(f"print('accuracy={m}')\n")
        (repo / "data.txt").write_text(f"v{i}\n")
        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"c{i}")
        commits.append(_run_git(repo, "rev-parse", "HEAD"))

    result = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[-1],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.95,
        direction="higher",
    )

    assert result.status == "non_monotonic"
    assert result.first_bad_commit is None
    assert len(result.evaluated_commits) >= 2


def test_skipped_commits_handling(tmp_path: Path) -> None:
    """Test that missing metric, failure, or timeout marks commit as skipped and

    never classifies as good or bad.
    """
    repo = tmp_path / "repo_skipped"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "ModelBisect Tester")
    _run_git(repo, "config", "user.email", "tester@example.com")
    _run_git(repo, "config", "commit.gpgSign", "false")

    # 0: good
    # 1: script crashes / exits 1
    # 2: regression (bad)
    (repo / "eval.py").write_text("print('accuracy=0.99')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "good 0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("import sys; sys.exit(1)\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "broken 1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "bad 2")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c2,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )

    # c1 was evaluated and skipped
    c1_evals = [e for e in result.evaluated_commits if e.commit == c1]
    if c1_evals:
        assert c1_evals[0].status == "skipped"
        assert c1_evals[0].skip_reason is not None

    assert result.status == "found"
    assert result.first_bad_commit == c2


def test_repeat_median(tmp_path: Path) -> None:
    """Test repeat_count taking median of noisy evaluations."""
    repo = tmp_path / "repo_repeat"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "ModelBisect Tester")
    _run_git(repo, "config", "user.email", "tester@example.com")
    _run_git(repo, "config", "commit.gpgSign", "false")

    # Good commit
    (repo / "eval.py").write_text("print('loss=0.10')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # Bad commit
    (repo / "eval.py").write_text("print('loss=0.50')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="loss",
        threshold=0.20,
        direction="lower",
        repeat_count=3,
    )

    assert result.status == "found"
    assert result.first_bad_commit == c1
    for ev in result.evaluated_commits:
        assert ev.eval_runs is not None
        assert len(ev.eval_runs) == 3


def test_cli_execution_and_out_json(tmp_path: Path) -> None:
    """Test CLI python -m model_bisect with --out result.json."""
    repo = tmp_path / "cli_repo"
    create_git_repo_with_commits(repo, num_commits=5, regression_commit=3)

    c0 = _run_git(repo, "rev-parse", "HEAD~4")
    c_head = _run_git(repo, "rev-parse", "HEAD")
    c_bad = _run_git(repo, "rev-parse", "HEAD~1")  # commit 3

    out_file = tmp_path / "result.json"

    cmd = [
        sys.executable,
        "-m",
        "model_bisect",
        "--repo",
        str(repo),
        "--good",
        c0,
        "--bad",
        c_head,
        "--cmd",
        f"{sys.executable} eval.py",
        "--metric",
        "accuracy",
        "--threshold",
        "0.95",
        "--direction",
        "higher",
        "--out",
        str(out_file),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"CLI error: {proc.stderr}"
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["status"] == "found"
    assert data["first_bad_commit"] == c_bad
    assert data["total_eval_count"] >= 2


def test_bisect_on_progress_callback(tmp_path: Path) -> None:
    """Test that on_progress callback is invoked for each evaluated commit."""
    repo = tmp_path / "progress_repo"
    create_git_repo_with_commits(repo, num_commits=5, regression_commit=3)

    c0 = _run_git(repo, "rev-parse", "HEAD~4")
    c_head = _run_git(repo, "rev-parse", "HEAD")

    progress_events: list[str] = []

    def on_progress(eval_res):
        progress_events.append(eval_res.commit)

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c_head,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.95,
        direction="higher",
        on_progress=on_progress,
    )

    assert result.status == "found"
    assert len(progress_events) == result.total_eval_count
    assert set(progress_events) == {e.commit for e in result.evaluated_commits}


def test_skipped_commit_before_culprit_allowed(tmp_path: Path) -> None:
    """Test that a skipped commit sitting before the culprit on an otherwise monotonic

    history is allowed, returning status='found' and the correct first_bad_commit.
    """
    repo = tmp_path / "skipped_before_culprit_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "Tester")
    _run_git(repo, "config", "user.email", "tester@example.com")
    _run_git(repo, "config", "commit.gpgSign", "false")

    # 5 commits: c0 (0.95), c1 (0.95), c2 (skipped), c3 (0.80 - culprit), c4 (0.80)
    commits: list[str] = []
    for i in range(5):
        eval_script = repo / "eval.py"
        if i == 2:
            # Commit 2 crashes / exits non-zero to produce a 'skipped' status
            eval_script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
        elif i >= 3:
            eval_script.write_text("print('accuracy=0.80')\n", encoding="utf-8")
        else:
            eval_script.write_text("print('accuracy=0.95')\n", encoding="utf-8")

        (repo / "version.txt").write_text(f"commit_{i}\n", encoding="utf-8")
        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"commit {i}")
        sha = _run_git(repo, "rev-parse", "HEAD")
        commits.append(sha)

    res = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[4],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )

    assert res.status == "found"
    assert res.first_bad_commit == commits[3]
    # Verify commit 2 was evaluated and marked as skipped
    c2_eval = next((e for e in res.evaluated_commits if e.commit == commits[2]), None)
    assert c2_eval is not None
    assert c2_eval.status == "skipped"


def test_validate_found_result_raises_on_invariant_violations() -> None:
    """Test that validate_found_result raises AssertionError when the invariant is violated."""
    commits = ["c0", "c1", "c2", "c3"]

    # Valid result: c0 good, c1 skipped, c2 bad (culprit), c3 bad
    valid_res = BisectResult(
        status="found",
        first_bad_commit="c2",
        evaluated_commits=[
            CommitEvaluation(commit="c0", metric=0.95, status="good", duration=0.1),
            CommitEvaluation(commit="c1", metric=None, status="skipped", duration=0.1, skip_reason="error"),
            CommitEvaluation(commit="c2", metric=0.80, status="bad", duration=0.1),
            CommitEvaluation(commit="c3", metric=0.80, status="bad", duration=0.1),
        ],
        total_eval_count=4,
    )
    # Must not raise
    validate_found_result(valid_res, commits)

    # Violation 1: An earlier commit in search order (c1) is evaluated as 'bad'
    invalid_earlier_bad = BisectResult(
        status="found",
        first_bad_commit="c2",
        evaluated_commits=[
            CommitEvaluation(commit="c0", metric=0.95, status="good", duration=0.1),
            CommitEvaluation(commit="c1", metric=0.75, status="bad", duration=0.1),
            CommitEvaluation(commit="c2", metric=0.80, status="bad", duration=0.1),
        ],
        total_eval_count=3,
    )
    with pytest.raises(AssertionError, match="earlier in search order.*has status 'bad'"):
        validate_found_result(invalid_earlier_bad, commits)

    # Violation 2: The culprit commit is evaluated as 'good'
    invalid_culprit_good = BisectResult(
        status="found",
        first_bad_commit="c2",
        evaluated_commits=[
            CommitEvaluation(commit="c0", metric=0.95, status="good", duration=0.1),
            CommitEvaluation(commit="c2", metric=0.95, status="good", duration=0.1),
        ],
        total_eval_count=2,
    )
    with pytest.raises(AssertionError, match="not evaluated as 'bad'"):
        validate_found_result(invalid_culprit_good, commits)

    # Violation 3: first_bad_commit is None
    invalid_no_culprit = BisectResult(
        status="found",
        first_bad_commit=None,
        evaluated_commits=[],
        total_eval_count=0,
    )
    with pytest.raises(AssertionError, match="first_bad_commit is None"):
        validate_found_result(invalid_no_culprit, commits)


def _create_alternating_repo(repo_dir: Path) -> tuple[Path, list[str]]:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run_git(repo_dir, "init")
    _run_git(repo_dir, "config", "user.name", "Tester")
    _run_git(repo_dir, "config", "user.email", "tester@example.com")
    _run_git(repo_dir, "config", "commit.gpgSign", "false")

    metrics = [0.95, 0.80, 0.95, 0.80, 0.95]
    commits: list[str] = []
    for i, m in enumerate(metrics):
        eval_script = repo_dir / "eval.py"
        eval_script.write_text(f"print('accuracy={m}')\n", encoding="utf-8")
        (repo_dir / "version.txt").write_text(f"commit_{i}\n", encoding="utf-8")
        _run_git(repo_dir, "add", ".")
        _run_git(repo_dir, "commit", "-m", f"commit {i}: {m}")
        commits.append(_run_git(repo_dir, "rev-parse", "HEAD"))
    return repo_dir, commits


def test_bad_endpoint_evaluates_good_returns_error(tmp_path: Path) -> None:
    repo, commits = _create_alternating_repo(tmp_path / "repo_bad_ep")
    # bad_commit is c4 in the 0.95, 0.80, 0.95, 0.80, 0.95 history
    res = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[4],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )
    assert res.status == "error"
    assert commits[4] in (res.error_message or "")
    assert res.first_bad_commit is None


def test_good_endpoint_evaluates_bad_returns_error(tmp_path: Path) -> None:
    repo, commits = _create_alternating_repo(tmp_path / "repo_good_ep")
    # good_commit is a 0.80 commit (c1), bad_commit is c4 (0.95)
    res = bisect(
        repo_path=repo,
        good_commit=commits[1],
        bad_commit=commits[4],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )
    assert res.status == "error"
    assert commits[1] in (res.error_message or "")
    assert res.first_bad_commit is None


def test_valid_alternating_endpoints_non_monotonic(tmp_path: Path) -> None:
    repo, commits = _create_alternating_repo(tmp_path / "repo_alt_nm")
    # good = c0, bad = c3 on the same history
    res = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[3],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )
    assert res.status == "non_monotonic"
    assert res.first_bad_commit is None

