from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from model_bisect.engine import bisect
from model_bisect.models import BisectResult


def _run_git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def init_git_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run_git(repo_dir, "init", "-b", "main")
    _run_git(repo_dir, "config", "user.name", "Adversarial Tester")
    _run_git(repo_dir, "config", "user.email", "adv@example.com")
    _run_git(repo_dir, "config", "commit.gpgSign", "false")
    return repo_dir


@pytest.fixture(autouse=True)
def assert_no_leaked_worktrees_or_tempdirs(tmp_path: Path) -> None:
    """Fixture running after each test asserting no leaked git worktrees
    or temporary directories remain.
    """
    yield
    # 1. Assert no temporary worktrees exist in temp dir
    temp_base = Path(tempfile.gettempdir())
    leaked_dirs = list(temp_base.glob("model_bisect_wt_*"))
    assert not leaked_dirs, f"Leaked temporary worktree directories found: {leaked_dirs}"

    # 2. Assert git worktree list shows only the main worktree for all repos in test
    for git_dir in tmp_path.glob("**/.git"):
        repo_dir = git_dir.parent
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "worktree", "list"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            wt_lines = proc.stdout.strip().splitlines()
            assert len(wt_lines) <= 1, f"Found extra worktrees in {repo_dir}: {wt_lines}"


# ============================================================================
# 1. Git edge cases
# ============================================================================


def test_good_not_ancestor_of_bad(tmp_path: Path) -> None:
    """Good commit is not an ancestor of bad commit: expect a clear error status, not a crash."""
    repo = init_git_repo(tmp_path / "repo_ancestry")

    # Create initial commit on main
    (repo / "file.txt").write_text("root\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "root")

    # Create branch A
    _run_git(repo, "checkout", "-b", "branch_a")
    (repo / "eval.py").write_text("print('accuracy=0.99')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "branch a")
    sha_a = _run_git(repo, "rev-parse", "HEAD")

    # Create branch B from root
    _run_git(repo, "checkout", "main")
    _run_git(repo, "checkout", "-b", "branch_b")
    (repo / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "branch b")
    sha_b = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=sha_a,
        bad_commit=sha_b,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )

    assert result.status == "error"
    assert result.first_bad_commit is None
    assert result.error_message is not None
    assert "not an ancestor" in result.error_message


def test_good_and_bad_same_commit(tmp_path: Path) -> None:
    """Good and bad are the same commit: expect a clear error."""
    repo = init_git_repo(tmp_path / "repo_same_commit")
    (repo / "eval.py").write_text("print('accuracy=0.85')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    sha0 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=sha0,
        bad_commit=sha0,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )

    assert result.status == "error"
    assert result.first_bad_commit is None
    assert result.error_message is not None
    assert "cannot be the same commit" in result.error_message.lower()


def test_single_commit_repo(tmp_path: Path) -> None:
    """Repo with a single commit: good and bad are identical, expects error."""
    repo = init_git_repo(tmp_path / "repo_single_commit")
    (repo / "eval.py").write_text("print('accuracy=0.95')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "single commit")
    sha = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=sha,
        bad_commit=sha,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "error"


def test_two_commits_repo(tmp_path: Path) -> None:
    """Repo with exactly two commits."""
    repo = init_git_repo(tmp_path / "repo_two_commits")
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "commit 0")
    sha0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.80')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "commit 1")
    sha1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=sha0,
        bad_commit=sha1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == sha1
    assert result.total_eval_count <= 2


def test_branch_with_merge_commits(tmp_path: Path) -> None:
    """Branch with merge commits: topological ordering and binary search behave sensibly."""
    repo = init_git_repo(tmp_path / "repo_merges")

    # c0: good
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0: base good")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # side branch
    _run_git(repo, "checkout", "-b", "side")
    (repo / "side.txt").write_text("side\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1: side branch")

    # main branch: regression introduced
    _run_git(repo, "checkout", "main")
    (repo / "eval.py").write_text("print('accuracy=0.75')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c2: main regression")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    # merge side into main
    _run_git(repo, "merge", "side", "--no-ff", "-m", "c3: merge side")
    c3 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c3,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    # In git's topological linearization (c0 -> c2 -> c1 -> c3), c2 (accuracy=0.75) is bad
    # while c1 (accuracy=0.98) is good, producing an alternating sequence [good, bad, good, bad].
    # The engine sensibly detects this topological non-monotonicity or finds the regression culprit.
    assert result.status in ("non_monotonic", "found")
    if result.status == "found":
        assert result.first_bad_commit in (c2, c3)

    # Monotonic merge scenario: feature branch merged cleanly into main, followed by regression
    repo_mono = init_git_repo(tmp_path / "repo_merges_mono")
    (repo_mono / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo_mono, "add", ".")
    _run_git(repo_mono, "commit", "-m", "m0")
    m0 = _run_git(repo_mono, "rev-parse", "HEAD")

    _run_git(repo_mono, "checkout", "-b", "feature")
    (repo_mono / "feat.txt").write_text("feat\n")
    _run_git(repo_mono, "add", ".")
    _run_git(repo_mono, "commit", "-m", "m1")

    _run_git(repo_mono, "checkout", "main")
    _run_git(repo_mono, "merge", "feature", "--no-ff", "-m", "m2 merge")

    (repo_mono / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo_mono, "add", ".")
    _run_git(repo_mono, "commit", "-m", "m3 regression")
    m3 = _run_git(repo_mono, "rev-parse", "HEAD")

    res_mono = bisect(
        repo_path=repo_mono,
        good_commit=m0,
        bad_commit=m3,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert res_mono.status == "found"
    assert res_mono.first_bad_commit == m3


def test_short_hashes_branches_and_tags(tmp_path: Path) -> None:
    """Commit references given as short hashes, branch names, and tags."""
    repo = init_git_repo(tmp_path / "repo_refs")

    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    _run_git(repo, "tag", "v1.0.0")
    c0_full = _run_git(repo, "rev-parse", "HEAD")
    c0_short = c0_full[:7]

    _run_git(repo, "checkout", "-b", "feature_branch")
    (repo / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1_full = _run_git(repo, "rev-parse", "HEAD")

    # Run with tag and branch name
    res1 = bisect(
        repo_path=repo,
        good_commit="v1.0.0",
        bad_commit="feature_branch",
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert res1.status == "found"
    assert res1.first_bad_commit == c1_full

    # Run with short hash
    res2 = bisect(
        repo_path=repo,
        good_commit=c0_short,
        bad_commit=c1_full[:7],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert res2.status == "found"
    assert res2.first_bad_commit == c1_full


def test_detached_head_and_dirty_working_tree(tmp_path: Path) -> None:
    """Detached HEAD state and dirty working tree (staged & unstaged) remain unchanged."""
    repo = init_git_repo(tmp_path / "repo_dirty")

    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    # Detach HEAD to c1
    _run_git(repo, "checkout", "--detach", c1)
    head_before = _run_git(repo, "rev-parse", "HEAD")

    # Add dirty unstaged and staged changes
    dirty_file = repo / "uncommitted.txt"
    dirty_file.write_text("unstaged content\n")
    staged_file = repo / "staged.txt"
    staged_file.write_text("staged content\n")
    _run_git(repo, "add", str(staged_file))

    status_before = _run_git(repo, "status", "--porcelain")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1

    # Verify working tree and HEAD are completely unchanged
    head_after = _run_git(repo, "rev-parse", "HEAD")
    status_after = _run_git(repo, "status", "--porcelain")
    assert head_after == head_before
    assert status_after == status_before
    assert dirty_file.read_text() == "unstaged content\n"
    assert staged_file.read_text() == "staged content\n"


# ============================================================================
# 2. Eval command failures
# ============================================================================


def test_eval_exits_nonzero(tmp_path: Path) -> None:
    """Eval exits non-zero on some commits: they must be 'skipped', never good or bad."""
    repo = init_git_repo(tmp_path / "repo_exit_nonzero")

    # c0: good
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # c1: crash exit code 42
    (repo / "eval.py").write_text("import sys; sys.exit(42)\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    # c2: bad
    (repo / "eval.py").write_text("print('accuracy=0.60')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c2")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c2,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )

    c1_eval = next((e for e in result.evaluated_commits if e.commit == c1), None)
    if c1_eval:
        assert c1_eval.status == "skipped"
        assert "exit code 42" in (c1_eval.skip_reason or "")
        assert c1_eval.metric is None


def test_eval_hangs_and_child_processes_killed(tmp_path: Path) -> None:
    """Eval hangs: timeout fires, process & child processes are killed, commit is skipped."""
    repo = init_git_repo(tmp_path / "repo_hang")

    # c0: good
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # c1: hangs by spawning a child process that sleeps
    script_content = (
        "import subprocess, time\n"
        "p = subprocess.Popen(['sleep', '120'])\n"
        "time.sleep(120)\n"
    )
    (repo / "eval.py").write_text(script_content)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    # c2: bad
    (repo / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c2")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    start_time = time.perf_counter()
    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c2,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        timeout=1.0,  # 1 second timeout
    )
    elapsed = time.perf_counter() - start_time
    assert elapsed < 5.0, f"Timeout did not fire promptly: took {elapsed}s"

    c1_eval = next((e for e in result.evaluated_commits if e.commit == c1), None)
    if c1_eval:
        assert c1_eval.status == "skipped"
        assert "timed out" in (c1_eval.skip_reason or "")

    # Ensure no sleep 120 remains running
    check = subprocess.run(["pgrep", "-f", "sleep 120"], capture_output=True, text=True)
    assert not check.stdout.strip(), "Child process was not killed on timeout!"


def test_eval_prints_metric_twice(tmp_path: Path) -> None:
    """Eval prints metric twice: the engine must use the JSON metrics file when available."""
    repo = init_git_repo(tmp_path / "repo_twice")

    # c0: prints intermediate accuracy=0.50 then writes final accuracy=0.98 to MODEL_BISECT_METRICS_FILE
    (repo / "eval.py").write_text(
        "import os, json\n"
        "print('Epoch 1: accuracy=0.50')\n"
        "mf = os.environ.get('MODEL_BISECT_METRICS_FILE')\n"
        "if mf:\n"
        "    with open(mf, 'w') as f: json.dump({'accuracy': 0.98}, f)\n"
        "print('Final: accuracy=0.98')\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text(
        "import os, json\n"
        "print('Epoch 1: accuracy=0.95')\n"
        "mf = os.environ.get('MODEL_BISECT_METRICS_FILE')\n"
        "if mf:\n"
        "    with open(mf, 'w') as f: json.dump({'accuracy': 0.70}, f)\n"
        "print('Final: accuracy=0.70')\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1
    # Check that c0 parsed 0.98 (not 0.50) and c1 parsed 0.70 (not 0.95)
    e0 = next(e for e in result.evaluated_commits if e.commit == c0)
    e1 = next(e for e in result.evaluated_commits if e.commit == c1)
    assert e0.metric == 0.98
    assert e1.metric == 0.70


def test_eval_invalid_metrics(tmp_path: Path) -> None:
    """Eval prints no metric, NaN, inf, or value outside valid_range: must be marked skipped."""
    repo = init_git_repo(tmp_path / "repo_invalid_metrics")

    (repo / "eval.py").write_text("print('accuracy=0.95')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # c1: NaN
    (repo / "eval.py").write_text("print('accuracy=NaN')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1: nan")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    # c2: Inf
    (repo / "eval.py").write_text("print('accuracy=inf')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c2: inf")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    # c3: accuracy > 1.0
    (repo / "eval.py").write_text("print('accuracy=1.5')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c3: > 1.0")
    c3 = _run_git(repo, "rev-parse", "HEAD")

    # c4: accuracy < 0.0
    (repo / "eval.py").write_text("print('accuracy=-0.2')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c4: < 0.0")
    c4 = _run_git(repo, "rev-parse", "HEAD")

    # c5: real regression
    (repo / "eval.py").write_text("print('accuracy=0.65')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c5: bad")
    c5 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c5,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        valid_range=(0.0, 1.0),
    )
    for c in [c1, c2, c3, c4]:
        ev = next((e for e in result.evaluated_commits if e.commit == c), None)
        if ev:
            assert ev.status == "skipped", f"Commit {c} with invalid metric was not skipped: {ev.status}"

    assert result.status == "found"
    assert result.first_bad_commit == c5


def test_eval_warnings_and_progress_bars(tmp_path: Path) -> None:
    """Eval output contains warnings, ANSI escapes, and progress bars before metric."""
    repo = init_git_repo(tmp_path / "repo_noisy_stdout")

    (repo / "eval.py").write_text(
        "import sys\n"
        "sys.stderr.write('UserWarning: GPU not detected\\n')\n"
        "print('[=====>.....] 50% loss=0.5')\n"
        "print('accuracy=0.96')\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text(
        "import sys\n"
        "sys.stderr.write('UserWarning: GPU not detected\\n')\n"
        "print('[==========>] 100% loss=1.5')\n"
        "print('accuracy=0.72')\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1


def test_eval_command_with_quotes_and_special_args(tmp_path: Path) -> None:
    """Eval command with quotes, spaces, or special characters in arguments."""
    repo = init_git_repo(tmp_path / "repo_args")

    (repo / "eval.py").write_text(
        "import sys\n"
        "model_name = sys.argv[1]\n"
        "val = 0.98 if 'model v1' in model_name else 0.70\n"
        "print(f'accuracy={val}')\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text(
        "import sys\n"
        "model_name = sys.argv[1]\n"
        "val = 0.70\n"
        "print(f'accuracy={val}')\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    cmd = f'{sys.executable} eval.py "model v1 (special)"'
    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=cmd,
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1


# ============================================================================
# 3. Threshold and direction
# ============================================================================


def test_threshold_equality_inclusive(tmp_path: Path) -> None:
    """Exact threshold equality: 'meets threshold' is inclusive (>= for higher, <= for lower)."""
    repo = init_git_repo(tmp_path / "repo_threshold_equality")

    # Higher is better: 0.90 == threshold 0.90 must be GOOD
    (repo / "eval.py").write_text("print('accuracy=0.90')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0: exact threshold")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.899')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1: below threshold")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    res_higher = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )
    assert res_higher.status == "found"
    assert res_higher.first_bad_commit == c1

    # Lower is better: 50.0 == threshold 50.0 must be GOOD
    (repo / "eval.py").write_text("print('latency=50.0')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c2: exact latency")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('latency=50.1')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c3: regressed latency")
    c3 = _run_git(repo, "rev-parse", "HEAD")

    res_lower = bisect(
        repo_path=repo,
        good_commit=c2,
        bad_commit=c3,
        eval_command=f"{sys.executable} eval.py",
        metric_name="latency",
        threshold=50.0,
        direction="lower",
    )
    assert res_lower.status == "found"
    assert res_lower.first_bad_commit == c3


def test_direction_lower_latency(tmp_path: Path) -> None:
    """direction='lower' with latency metric where lower is better."""
    repo = init_git_repo(tmp_path / "repo_latency")

    (repo / "eval.py").write_text("print('latency=15.2')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0: good latency")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('latency=45.8')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1: regressed latency")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="latency",
        threshold=20.0,
        direction="lower",
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1


def test_threshold_all_pass_or_all_fail(tmp_path: Path) -> None:
    """Threshold that every commit passes, or that every commit fails: expect 'not_found'."""
    repo = init_git_repo(tmp_path / "repo_all_pass_fail")

    (repo / "eval.py").write_text("print('accuracy=0.85')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.87')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    # Every commit passes (threshold 0.50): bad endpoint evaluates as good -> expect error
    res_pass = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.50,
        direction="higher",
    )
    assert res_pass.status == "error"
    assert c1 in (res_pass.error_message or "")
    assert res_pass.first_bad_commit is None

    # Every commit fails (threshold 0.99): expect not_found
    res_fail = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.99,
        direction="higher",
    )
    assert res_fail.status == "not_found"
    assert res_fail.first_bad_commit is None


# ============================================================================
# 4. Monotonicity and search
# ============================================================================


def test_regression_at_first_and_last_commits(tmp_path: Path) -> None:
    """Regression at the very first commit after good, and at the very last commit before bad."""
    # Sub-case A: regression at commit 1 (first commit after good)
    repo_a = init_git_repo(tmp_path / "repo_reg_first")
    commits_a: list[str] = []
    for i in range(5):
        val = 0.95 if i == 0 else 0.70
        (repo_a / "eval.py").write_text(f"print('accuracy={val}')\n")
        (repo_a / "i.txt").write_text(f"{i}\n")
        _run_git(repo_a, "add", ".")
        _run_git(repo_a, "commit", "-m", f"c{i}")
        commits_a.append(_run_git(repo_a, "rev-parse", "HEAD"))

    res_a = bisect(
        repo_path=repo_a,
        good_commit=commits_a[0],
        bad_commit=commits_a[-1],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert res_a.status == "found"
    assert res_a.first_bad_commit == commits_a[1]

    # Sub-case B: regression at very last commit (index N - 1)
    repo_b = init_git_repo(tmp_path / "repo_reg_last")
    commits_b: list[str] = []
    for i in range(5):
        val = 0.70 if i == 4 else 0.95
        (repo_b / "eval.py").write_text(f"print('accuracy={val}')\n")
        (repo_b / "i.txt").write_text(f"{i}\n")
        _run_git(repo_b, "add", ".")
        _run_git(repo_b, "commit", "-m", f"c{i}")
        commits_b.append(_run_git(repo_b, "rev-parse", "HEAD"))

    res_b = bisect(
        repo_path=repo_b,
        good_commit=commits_b[0],
        bad_commit=commits_b[-1],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert res_b.status == "found"
    assert res_b.first_bad_commit == commits_b[4]


def test_two_regressions_then_recovery(tmp_path: Path) -> None:
    """Two regressions, then a recovery (good, bad, bad, good, good): confirm non_monotonic."""
    repo = init_git_repo(tmp_path / "repo_recovery")
    # Commits: good, bad, bad, good, good
    metrics = [0.98, 0.75, 0.72, 0.96, 0.97]
    commits: list[str] = []
    for i, m in enumerate(metrics):
        (repo / "eval.py").write_text(f"print('accuracy={m}')\n")
        (repo / "data.txt").write_text(f"{i}\n")
        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"c{i}")
        commits.append(_run_git(repo, "rev-parse", "HEAD"))

    result = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[-1],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status in ("non_monotonic", "error")
    assert result.first_bad_commit is None


def test_random_metric_sequences_property(tmp_path: Path) -> None:
    """Random metric sequences over 50 seeded trials: the engine must never

    return a first_bad_commit that is not actually the first bad commit
    in the sequence unless it reports non_monotonic.
    """
    rng = random.Random(42)
    threshold = 0.90

    for trial in range(50):
        n = rng.randint(4, 15)
        trial_repo = init_git_repo(tmp_path / f"repo_random_{trial}")

        # Choose scenario: 50% monotonic, 50% arbitrary/anomalous
        is_monotonic = rng.choice([True, False])
        if is_monotonic:
            reg_idx = rng.randint(1, n - 1)
            metrics = (
                [rng.uniform(0.91, 1.0) for _ in range(reg_idx)]
                + [rng.uniform(0.50, 0.89) for _ in range(n - reg_idx)]
            )
        else:
            metrics = [rng.uniform(0.50, 1.0) for _ in range(n)]
            metrics[0] = 0.95  # ensure start is good

        commits: list[str] = []
        for i, m in enumerate(metrics):
            (trial_repo / "eval.py").write_text(f"print('accuracy={m:.4f}')\n")
            (trial_repo / "seq.txt").write_text(f"{i}\n")
            _run_git(trial_repo, "add", ".")
            _run_git(trial_repo, "commit", "-m", f"c{i}")
            commits.append(_run_git(trial_repo, "rev-parse", "HEAD"))

        # Find true first bad commit index in full history
        pattern = ["good" if m >= threshold else "bad" for m in metrics]
        tfb = next((i for i, s in enumerate(pattern) if s == "bad"), None)

        result = bisect(
            repo_path=trial_repo,
            good_commit=commits[0],
            bad_commit=commits[-1],
            eval_command=f"{sys.executable} eval.py",
            metric_name="accuracy",
            threshold=threshold,
        )

        # The core invariant:
        # If result is "found", it MUST equal the true first bad commit!
        # Otherwise it must report "non_monotonic", "not_found", or "error"
        if result.status == "found":
            assert tfb is not None
            assert result.first_bad_commit == commits[tfb], (
                f"Trial {trial}: engine found {result.first_bad_commit} (idx {commits.index(result.first_bad_commit)}) "
                f"but true first bad was idx {tfb} ({commits[tfb]}) in pattern {pattern}"
            )
        elif result.status == "not_found":
            # Valid if no bad commit exists or all bad
            assert tfb is None or pattern[0] == "bad"
        else:
            assert result.status in ("non_monotonic", "error")


def test_eval_count_monotonic_budget(tmp_path: Path) -> None:
    """Eval count stays within the budget math.ceil(log2(N)) + 2 for monotonic

    histories of sizes 2, 3, 7, 8, 64, and 100 commits.
    """
    for n in [2, 3, 7, 8, 64, 100]:
        repo = init_git_repo(tmp_path / f"repo_budget_{n}")
        max_allowed_budget = math.ceil(math.log2(n)) + 2

        # Regression at middle commit
        reg_idx = n // 2
        commits: list[str] = []
        for i in range(n):
            val = 0.80 if i >= reg_idx else 0.98
            (repo / "eval.py").write_text(f"print('accuracy={val}')\n")
            (repo / "c.txt").write_text(f"{i}\n")
            _run_git(repo, "add", ".")
            _run_git(repo, "commit", "-m", f"c{i}")
            commits.append(_run_git(repo, "rev-parse", "HEAD"))

        result = bisect(
            repo_path=repo,
            good_commit=commits[0],
            bad_commit=commits[-1],
            eval_command=f"{sys.executable} eval.py",
            metric_name="accuracy",
            threshold=0.90,
        )

        assert result.status == "found"
        assert result.first_bad_commit == commits[reg_idx]
        assert result.total_eval_count <= max_allowed_budget, (
            f"Failed budget for N={n}: used {result.total_eval_count} > max {max_allowed_budget}"
        )


# ============================================================================
# 5. Repeats and noise
# ============================================================================


def test_repeat_one_run_crashes_skipped(tmp_path: Path) -> None:
    """repeat=3 where one run crashes: must mark commit as 'skipped'."""
    repo = init_git_repo(tmp_path / "repo_repeat_crash")

    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    counter_file = (tmp_path / "flaky_counter.txt").as_posix()
    if os.path.exists(counter_file):
        os.remove(counter_file)

    # c1: flaky script that crashes on 2nd run
    flaky_script = (
        "import sys, os\n"
        f"counter_file = '{counter_file}'\n"
        "cnt = 0\n"
        "if os.path.exists(counter_file):\n"
        "    with open(counter_file) as f: cnt = int(f.read().strip())\n"
        "cnt += 1\n"
        "with open(counter_file, 'w') as f: f.write(str(cnt))\n"
        "if cnt == 2: sys.exit(1)\n"
        "print('accuracy=0.85')\n"
    )
    (repo / "eval.py").write_text(flaky_script)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.60')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c2")
    c2 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c2,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        repeat_count=3,
    )

    c1_ev = next((e for e in result.evaluated_commits if e.commit == c1), None)
    if c1_ev:
        assert c1_ev.status == "skipped"
        assert c1_ev.skip_reason is not None
        assert "failed" in c1_ev.skip_reason


def test_repeat_even_number_median(tmp_path: Path) -> None:
    """repeat=even number: confirm median is computed correctly (average of middle two)."""
    repo = init_git_repo(tmp_path / "repo_repeat_even")

    (repo / "eval.py").write_text("print('accuracy=0.95')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    even_cnt_file = (tmp_path / "even_cnt.txt").as_posix()
    if os.path.exists(even_cnt_file):
        os.remove(even_cnt_file)

    # Outputs cycle between 0.80 and 0.90, median of 4 runs is 0.85
    script = (
        "import sys, os\n"
        "cnt = 0\n"
        f"f_path = '{even_cnt_file}'\n"
        "if os.path.exists(f_path):\n"
        "    with open(f_path) as f: cnt = int(f.read().strip())\n"
        "cnt += 1\n"
        "with open(f_path, 'w') as f: f.write(str(cnt))\n"
        "val = 0.80 if cnt % 2 == 1 else 0.90\n"
        "print(f'accuracy={val}')\n"
    )
    (repo / "eval.py").write_text(script)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        repeat_count=4,
    )

    e1 = next(e for e in result.evaluated_commits if e.commit == c1)
    # Middle values are 0.80 and 0.90 -> median 0.85
    assert e1.metric == pytest.approx(0.85)
    assert e1.eval_runs is not None
    assert len(e1.eval_runs) == 4


# ============================================================================
# 6. Output and CLI
# ============================================================================


def test_cli_out_nested_nonexistent_directory(tmp_path: Path) -> None:
    """--out to a path in a non-existent nested directory."""
    repo = init_git_repo(tmp_path / "repo_cli_out")
    (repo / "eval.py").write_text("print('accuracy=0.95')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.70')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    out_file = tmp_path / "deeply" / "nested" / "subdir" / "result.json"

    cmd = [
        sys.executable,
        "-m",
        "model_bisect",
        "--repo",
        str(repo),
        "--good",
        c0,
        "--bad",
        c1,
        "--cmd",
        f"{sys.executable} eval.py",
        "--metric",
        "accuracy",
        "--threshold",
        "0.90",
        "--out",
        str(out_file),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert data["status"] == "found"
    assert data["first_bad_commit"] == c1


def test_cli_invalid_arguments(tmp_path: Path) -> None:
    """Invalid CLI arguments: missing required flags, threshold not a number, unknown direction."""
    repo = init_git_repo(tmp_path / "repo_invalid_cli")
    (repo / "eval.py").write_text("print('accuracy=0.95')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # Missing --bad
    cmd1 = [sys.executable, "-m", "model_bisect", "--repo", str(repo), "--good", c0]
    p1 = subprocess.run(cmd1, capture_output=True, text=True)
    assert p1.returncode != 0
    assert "required" in p1.stderr.lower()

    # Threshold not a number
    cmd2 = [
        sys.executable,
        "-m",
        "model_bisect",
        "--repo",
        str(repo),
        "--good",
        c0,
        "--bad",
        c0,
        "--cmd",
        "python eval.py",
        "--metric",
        "accuracy",
        "--threshold",
        "not_a_float",
    ]
    p2 = subprocess.run(cmd2, capture_output=True, text=True)
    assert p2.returncode != 0
    assert "invalid float value" in p2.stderr.lower()

    # Unknown direction
    cmd3 = [
        sys.executable,
        "-m",
        "model_bisect",
        "--repo",
        str(repo),
        "--good",
        c0,
        "--bad",
        c0,
        "--cmd",
        "python eval.py",
        "--metric",
        "accuracy",
        "--threshold",
        "0.90",
        "--direction",
        "sideways",
    ]
    p3 = subprocess.run(cmd3, capture_output=True, text=True)
    assert p3.returncode != 0
    assert "invalid choice" in p3.stderr.lower()


def test_json_output_pydantic_roundtrip(tmp_path: Path) -> None:
    """JSON output validates against Pydantic model and round-trips."""
    repo = init_git_repo(tmp_path / "repo_roundtrip")
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.82')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    out_file = tmp_path / "res.json"
    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        out_path=out_file,
    )

    loaded_json = out_file.read_text(encoding="utf-8")
    loaded_model = BisectResult.model_validate_json(loaded_json)
    assert loaded_model.status == result.status
    assert loaded_model.first_bad_commit == result.first_bad_commit
    assert len(loaded_model.evaluated_commits) == len(result.evaluated_commits)
    assert loaded_model.total_eval_count == result.total_eval_count


def test_deterministic_identical_json(tmp_path: Path) -> None:
    """Running bisect twice with identical inputs produces identical JSON (ignoring duration)."""
    repo = init_git_repo(tmp_path / "repo_deterministic")
    (repo / "eval.py").write_text("print('accuracy=0.95')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.75')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    res1 = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    res2 = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )

    d1 = res1.model_dump()
    d2 = res2.model_dump()

    # Zero out non-deterministic durations
    for item in d1["evaluated_commits"]:
        item["duration"] = 0.0
    for item in d2["evaluated_commits"]:
        item["duration"] = 0.0

    assert d1 == d2


# ============================================================================
# 8. Metric parsing and disambiguation rules (Requirement 2)
# ============================================================================


def test_metric_summary_printed_last(tmp_path: Path) -> None:
    """Pattern a: Summary metric written to MODEL_BISECT_METRICS_FILE is used by engine."""
    repo = init_git_repo(tmp_path / "repo_summary_last")
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # c1 prints epoch/step metrics followed by writing final summary metric to JSON contract file
    script = (
        "import os, json\n"
        "print('Epoch 1: accuracy=0.45')\n"
        "print('Epoch 2: accuracy=0.60')\n"
        "mf = os.environ.get('MODEL_BISECT_METRICS_FILE')\n"
        "if mf:\n"
        "    with open(mf, 'w') as f: json.dump({'accuracy': 0.75}, f)\n"
        "print('Final: accuracy=0.75')\n"
    )
    (repo / "eval.py").write_text(script)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1
    c1_ev = next(e for e in result.evaluated_commits if e.commit == c1)
    assert c1_ev.metric == pytest.approx(0.75)


def test_valid_range_omitted_accepts_values_outside_zero_one(tmp_path: Path) -> None:
    """Without valid_range, only finite-number checks apply (e.g. latency or metrics > 1.0 are valid)."""
    repo = init_git_repo(tmp_path / "repo_no_range")
    (repo / "eval.py").write_text("print('score=150.0')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('score=50.0')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="score",
        threshold=100.0,
        direction="higher",
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1


def test_predecessor_check_budget_limit_documents_unverified(tmp_path: Path) -> None:
    """Requirement 3: When binary search uses its full budget on a history with an early recovery,

    it returns status 'found' with monotonicity_verified=False rather than falsely claiming verification.
    """
    repo = init_git_repo(tmp_path / "repo_pred_budget_limit")
    # For N=8, max_budget = ceil(log2(8)) + 2 = 5
    # Sequence: c0 (good), c1 (bad), c2 (good recovery), c3 (good), c4 (bad), c5 (bad), c6 (bad), c7 (bad)
    # Binary search samples: c0 (good), c7 (bad), mid=3 (good), mid=5 (bad), mid=4 (bad) -> 5 evals used.
    # The predecessor check for c2 (c1) cannot run because eval count reached 5.
    # Result must report status 'found', first_bad_commit == c4, and monotonicity_verified == False.
    metrics = [0.95, 0.70, 0.95, 0.95, 0.70, 0.70, 0.70, 0.70]
    commits: list[str] = []
    for i, m in enumerate(metrics):
        (repo / "eval.py").write_text(f"print('accuracy={m}')\n")
        (repo / "f.txt").write_text(f"{i}\n")
        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"c{i}")
        commits.append(_run_git(repo, "rev-parse", "HEAD"))

    result = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[-1],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == commits[4]
    assert result.monotonicity_verified is False
    assert result.total_eval_count <= math.ceil(math.log2(8)) + 2


def test_metric_summary_printed_first_then_samples_ambiguous(tmp_path: Path) -> None:
    """Pattern b: Summary printed first, then per-sample metrics after it.

    Skipped as ambiguous to avoid silently picking a single sample value.
    """
    repo = init_git_repo(tmp_path / "repo_summary_first")
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # c1 prints summary first, then per-sample outputs
    script = (
        "print('Summary: accuracy=0.75')\n"
        "print('Sample 1: accuracy=1.00')\n"
        "print('Sample 2: accuracy=0.50')\n"
    )
    (repo / "eval.py").write_text(script)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    # Since bad_commit is skipped due to ambiguous metric, bisect returns error
    assert result.status == "error"
    c1_ev = next(e for e in result.evaluated_commits if e.commit == c1)
    assert c1_ev.status == "skipped"


def test_metric_duplicate_identical_values_accepted(tmp_path: Path) -> None:
    """Pattern c: Same metric name printed on multiple lines with identical values.

    Result is unambiguous and should be accepted.
    """
    repo = init_git_repo(tmp_path / "repo_identical_multiline")
    (repo / "eval.py").write_text("print('accuracy=0.98')\nprint('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('accuracy=0.72')\nprint('accuracy=0.72')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert result.status == "found"
    assert result.first_bad_commit == c1
    c1_ev = next(e for e in result.evaluated_commits if e.commit == c1)
    assert c1_ev.metric == pytest.approx(0.72)


def test_metric_conflicting_values_no_clear_final_ambiguous(tmp_path: Path) -> None:
    """Pattern d: Same metric printed with different values and no clear final line.

    Skipped as ambiguous.
    """
    repo = init_git_repo(tmp_path / "repo_conflicting_unlabeled")
    (repo / "eval.py").write_text("print('accuracy=0.98')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # c1 prints two conflicting accuracy numbers with no labels
    (repo / "eval.py").write_text("print('accuracy=0.85')\nprint('accuracy=0.72')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    result = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    # c1 must be skipped as ambiguous
    assert result.status == "error"
    c1_ev = next(e for e in result.evaluated_commits if e.commit == c1)
    assert c1_ev.status == "skipped"


def test_metric_name_prefixes_and_word_boundaries(tmp_path: Path) -> None:
    """Test that metric name that is a prefix of another (acc vs accuracy) is not confused,

    and that longer words (val_accuracy=0.9) are rejected deliberately.
    """
    repo = init_git_repo(tmp_path / "repo_prefix_boundaries")

    # Script outputs both 'acc=0.85' and 'accuracy=0.95', plus 'val_accuracy=0.70'
    script = (
        "print('val_accuracy=0.70')\n"
        "print('acc=0.85')\n"
        "print('accuracy=0.95')\n"
    )
    (repo / "eval.py").write_text(script)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c0")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    (repo / "eval.py").write_text("print('acc=0.60')\nprint('accuracy=0.65')\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "c1")
    c1 = _run_git(repo, "rev-parse", "HEAD")

    # Searching for 'accuracy' must NOT match 'val_accuracy' or 'acc'
    res_accuracy = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
    )
    assert res_accuracy.status == "found"
    ev0_acc = next(e for e in res_accuracy.evaluated_commits if e.commit == c0)
    assert ev0_acc.metric == pytest.approx(0.95)

    # Searching for 'acc' must NOT match 'accuracy' or 'val_accuracy'
    res_acc = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="acc",
        threshold=0.80,
    )
    assert res_acc.status == "found"
    ev0_short = next(e for e in res_acc.evaluated_commits if e.commit == c0)
    assert ev0_short.metric == pytest.approx(0.85)

    # Searching for 'val_accuracy' matches 0.70 on c0, but c1 lacks it -> c1 skipped
    res_val = bisect(
        repo_path=repo,
        good_commit=c0,
        bad_commit=c1,
        eval_command=f"{sys.executable} eval.py",
        metric_name="val_accuracy",
        threshold=0.65,
    )
    assert res_val.status == "error"
    ev1_val = next(e for e in res_val.evaluated_commits if e.commit == c1)
    assert ev1_val.status == "skipped"


def test_alternating_pattern_non_monotonic_regression(tmp_path: Path) -> None:
    """Build exact alternating pattern (good, bad, good, bad, good).

    Confirm error when bad_commit evaluates good (c4), and non_monotonic
    when bad_commit evaluates bad (c3).
    """
    repo = init_git_repo(tmp_path / "repo_alternating")
    metrics = [0.95, 0.80, 0.95, 0.80, 0.95]
    commits = []
    for i, m in enumerate(metrics):
        (repo / "eval.py").write_text(f"print('accuracy={m}')\n")
        (repo / "version.txt").write_text(f"commit_{i}\n")
        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"commit {i}: metric {m}")
        commits.append(_run_git(repo, "rev-parse", "HEAD"))

    # When bad_commit is commits[-1] (metric=0.95, which is good), endpoint validation rejects with error
    res_err = bisect(
        repo_path=repo,
        good_commit=commits[0],
        bad_commit=commits[-1],
        eval_command=f"{sys.executable} eval.py",
        metric_name="accuracy",
        threshold=0.90,
        direction="higher",
    )
    assert res_err.status == "error"

    # When bad_commit is commits[3] (metric=0.80, which is bad), history c0..c3 is good, bad, good, bad
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
    assert res.monotonicity_verified is False
    assert res.first_bad_commit is None

