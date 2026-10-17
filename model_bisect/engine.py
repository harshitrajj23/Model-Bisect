from __future__ import annotations

import math
import os
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Sequence

from model_bisect.models import (
    BisectResult,
    BisectStatus,
    CommitEvaluation,
    CommitStatus,
    Direction,
)


def parse_metric(stdout: str, metric_name: str) -> float | None:
    """Parse metric from stdout matching `name=value` (e.g. accuracy=0.9123).

    Handles scientific notation, integers, and floats. Rejects NaN, Inf, and
    accuracy values outside [0, 1].

    Metric Resolution Rules:
    1. Word boundary: Only matches exact metric_name followed by '='. Subwords
       or prefixed/suffixed metric names (e.g. 'val_accuracy=0.9' when looking for
       'accuracy', or 'accuracy=0.9' when looking for 'acc') are deliberately
       rejected to avoid confusing distinct metrics.
    2. Identical values: If the metric appears multiple times with identical
       values across lines, the unambiguous value is accepted.
    3. Final summary metric: If multiple different values appear and the final
       occurrence is marked with summary/final indicators (or prior occurrences
       are marked as intermediate epoch/batch/sample metrics while the last
       occurrence is the overall summary), the final summary metric is used.
    4. Ambiguous output: If multiple different values appear with no clear final
       summary designation (e.g. conflicting values printed without labels, or
       where summary is printed first followed by per-sample metrics), the metric
       is rejected as ambiguous (returns None, marking the commit as skipped).
       This avoids silently picking an arbitrary per-sample or conflicting value.
    """
    pattern = (
        rf"(?:^|[\s,;]){re.escape(metric_name)}=\s*"
        rf"([+-]?(?:nan|inf(?:inity)?|\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?=$|[\s,;])"
    )

    lines = stdout.splitlines()
    matches: list[tuple[float, str]] = []
    for line in lines:
        for m in re.finditer(pattern, line, flags=re.IGNORECASE):
            try:
                val = float(m.group(1))
            except (ValueError, TypeError):
                continue
            if math.isnan(val) or math.isinf(val):
                continue
            if metric_name.lower() == "accuracy" and not (0.0 <= val <= 1.0):
                continue
            matches.append((val, line))

    if not matches:
        return None

    values = [m[0] for m in matches]
    # If all matches have identical values, accept unambiguously
    if all(math.isclose(v, values[0], rel_tol=1e-7, abs_tol=1e-7) for v in values):
        return values[0]

    # Multiple distinct values: evaluate context
    summary_words = {"final", "summary", "overall", "total", "avg", "average", "mean", "test"}
    sample_words = {"sample", "step", "batch", "epoch", "iter", "iteration"}

    last_val, last_line = matches[-1]
    last_line_lower = last_line.lower()
    last_has_summary = any(re.search(rf"\b{w}\b", last_line_lower) for w in summary_words)
    last_has_sample = any(re.search(rf"\b{w}\b", last_line_lower) for w in sample_words)

    first_line_lower = matches[0][1].lower()
    first_has_summary = any(re.search(rf"\b{w}\b", first_line_lower) for w in summary_words)

    # Pattern b: Summary printed first, then per-sample metrics after it
    if first_has_summary and last_has_sample:
        # Ambiguous: summary first, then samples. Do not silently pick the last sample!
        return None

    # Check if prior lines were samples/epochs/steps
    prior_have_sample = any(
        any(re.search(rf"\b{w}\b", line.lower()) for w in sample_words)
        for _, line in matches[:-1]
    )

    # Pattern a: Summary metric printed last
    if last_has_summary or (prior_have_sample and not last_has_sample):
        return last_val

    # Pattern d: Conflicting values with no clear final/summary line -> ambiguous
    return None


def run_git_cmd(repo_path: Path, args: Sequence[str]) -> str:
    """Run a git command in the target repo and return stdout."""
    cmd = ["git", "-C", str(repo_path), *args]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


@contextmanager
def temporary_worktree(repo_path: Path, commit: str) -> Generator[Path, None, None]:
    """Create a temporary git worktree for a commit and guarantee cleanup.

    Never touches the user's working tree.
    """
    # Create temp directory outside the user's working tree
    temp_dir = Path(tempfile.mkdtemp(prefix="model_bisect_wt_"))
    # Git worktree requires the destination directory to not exist yet when adding
    # so we delete the empty dir created by mkdtemp
    temp_dir.rmdir()

    add_cmd = ["git", "-C", str(repo_path), "worktree", "add", "--detach", str(temp_dir), commit]
    proc = subprocess.run(add_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to create git worktree for {commit}: {proc.stderr.strip()}")

    try:
        yield temp_dir
    finally:
        # Always remove worktree and prune
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(temp_dir)],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "prune"],
            capture_output=True,
            text=True,
        )
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _execute_eval_run(
    worktree_path: Path,
    eval_command: str,
    metric_name: str,
    timeout: float,
) -> tuple[float | None, str | None]:
    """Execute a single run of the eval command in worktree_path.

    Returns (metric_value, skip_reason).
    """
    # Check if shell features are needed
    has_shell_chars = any(c in eval_command for c in ["|", ">", "<", "&", ";", "$", "\n", "`"])
    if has_shell_chars:
        args = ["/bin/sh", "-c", eval_command]
    else:
        try:
            args = shlex.split(eval_command)
        except ValueError as e:
            return None, f"Failed to parse eval_command with shlex: {e}"

    if not args:
        return None, "Empty eval command"

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(worktree_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.communicate()
            return None, f"Process timed out after {timeout}s"
    except Exception as e:
        return None, f"Process execution failed: {e}"

    if proc.returncode != 0:
        err_msg = stderr.strip() or stdout.strip() or f"exit code {proc.returncode}"
        return None, f"Process failed with exit code {proc.returncode}: {err_msg[:200]}"

    val = parse_metric(stdout, metric_name)
    if val is None:
        return None, f"Metric '{metric_name}' not found or invalid in stdout"

    return val, None


def evaluate_commit(
    repo_path: Path,
    commit: str,
    eval_command: str,
    metric_name: str,
    threshold: float,
    direction: Direction,
    repeat_count: int,
    timeout: float,
) -> CommitEvaluation:
    """Evaluate a candidate commit using a temporary git worktree."""
    start_time = time.perf_counter()

    try:
        with temporary_worktree(repo_path, commit) as wt:
            run_metrics: list[float] = []
            for run_idx in range(repeat_count):
                val, skip_reason = _execute_eval_run(wt, eval_command, metric_name, timeout)
                if skip_reason is not None:
                    duration = time.perf_counter() - start_time
                    reason = f"Run {run_idx + 1}/{repeat_count} failed: {skip_reason}" if repeat_count > 1 else skip_reason
                    return CommitEvaluation(
                        commit=commit,
                        metric=None,
                        status="skipped",
                        duration=duration,
                        skip_reason=reason,
                        eval_runs=run_metrics if run_metrics else None,
                    )
                run_metrics.append(val)

            metric = statistics.median(run_metrics)
            duration = time.perf_counter() - start_time

            # Classify based on threshold and direction
            if direction == "higher":
                status: CommitStatus = "good" if metric >= threshold else "bad"
            else:
                status = "good" if metric <= threshold else "bad"

            return CommitEvaluation(
                commit=commit,
                metric=metric,
                status=status,
                duration=duration,
                eval_runs=run_metrics if repeat_count > 1 else None,
            )
    except Exception as e:
        duration = time.perf_counter() - start_time
        return CommitEvaluation(
            commit=commit,
            metric=None,
            status="skipped",
            duration=duration,
            skip_reason=f"Worktree error: {e}",
        )


def check_non_monotonic(
    commit_order: list[str],
    evaluated_commits: dict[str, CommitEvaluation],
) -> bool:
    """Check if evaluated commits alternate good/bad/good or violate monotonicity.

    Monotonicity in bisecting from good to bad requires:
    An earlier commit cannot be 'bad' if a subsequent commit is 'good'.
    """
    # Filter evaluated commits that are good or bad, preserving chronological commit order
    ordered_statuses: list[tuple[int, CommitStatus]] = [
        (idx, evaluated_commits[c].status)
        for idx, c in enumerate(commit_order)
        if c in evaluated_commits and evaluated_commits[c].status in ("good", "bad")
    ]

    seen_bad = False
    for _, status in ordered_statuses:
        if status == "bad":
            seen_bad = True
        elif status == "good" and seen_bad:
            # Found good after bad -> non-monotonic alternation!
            return True

    return False


def get_commit_list(repo_path: Path, good_commit: str, bad_commit: str) -> list[str]:
    """Return chronological list of commit SHAs from good_commit to bad_commit."""
    good_sha = run_git_cmd(repo_path, ["rev-parse", "--verify", good_commit])
    bad_sha = run_git_cmd(repo_path, ["rev-parse", "--verify", bad_commit])

    if good_sha == bad_sha:
        raise ValueError(f"Good commit and bad commit cannot be the same commit ('{good_commit}')")

    # Verify that good_sha is ancestor of bad_sha
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "merge-base", "--is-ancestor", good_sha, bad_sha],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"Commit '{good_commit}' ({good_sha}) is not an ancestor of '{bad_commit}' ({bad_sha})"
        )

    # rev-list good..bad gives commits reachable from bad but not good
    # topo-order with reverse produces chronological order: oldest to newest
    rev_list_out = run_git_cmd(
        repo_path,
        ["rev-list", "--topo-order", "--reverse", f"{good_sha}..{bad_sha}"],
    )
    commits = [line.strip() for line in rev_list_out.splitlines() if line.strip()]
    return [good_sha] + commits


def bisect(
    repo_path: Path | str,
    good_commit: str,
    bad_commit: str,
    eval_command: str,
    metric_name: str,
    threshold: float,
    direction: Direction | str = "higher",
    repeat_count: int = 1,
    timeout: float = 120.0,
    out_path: Path | str | None = None,
) -> BisectResult:
    """Find the git commit that caused an ML model's eval metric to regress.

    Uses binary search over the commit history, classifying commits by metric threshold.
    """
    repo = Path(repo_path).resolve()
    if direction not in ("higher", "lower"):
        raise ValueError(f"Invalid direction '{direction}', must be 'higher' or 'lower'")

    try:
        commits = get_commit_list(repo, good_commit, bad_commit)
    except Exception as e:
        res = BisectResult(
            status="error",
            first_bad_commit=None,
            evaluated_commits=[],
            total_eval_count=0,
            error_message=str(e),
        )
        if out_path:
            _write_result(res, out_path)
        return res

    eval_cache: dict[str, CommitEvaluation] = {}
    eval_history: list[CommitEvaluation] = []

    def evaluate(commit_sha: str) -> CommitEvaluation:
        if commit_sha in eval_cache:
            return eval_cache[commit_sha]
        eval_res = evaluate_commit(
            repo_path=repo,
            commit=commit_sha,
            eval_command=eval_command,
            metric_name=metric_name,
            threshold=threshold,
            direction=direction,
            repeat_count=repeat_count,
            timeout=timeout,
        )
        eval_cache[commit_sha] = eval_res
        eval_history.append(eval_res)
        return eval_res

    # Handle edge case where good and bad are identical
    if len(commits) == 1:
        single_eval = evaluate(commits[0])
        status: BisectStatus = "found" if single_eval.status == "bad" else "not_found"
        res = BisectResult(
            status=status,
            first_bad_commit=commits[0] if status == "found" else None,
            evaluated_commits=eval_history,
            total_eval_count=len(eval_history),
        )
        if out_path:
            _write_result(res, out_path)
        return res

    # 1. Evaluate endpoints: good_commit (index 0) and bad_commit (index N - 1)
    good_eval = evaluate(commits[0])
    bad_eval = evaluate(commits[-1])

    # Check skipped endpoints
    if good_eval.status == "skipped":
        res = BisectResult(
            status="error",
            first_bad_commit=None,
            evaluated_commits=eval_history,
            total_eval_count=len(eval_history),
            error_message=f"Good commit {commits[0]} was skipped: {good_eval.skip_reason}",
        )
        if out_path:
            _write_result(res, out_path)
        return res

    if bad_eval.status == "skipped":
        res = BisectResult(
            status="error",
            first_bad_commit=None,
            evaluated_commits=eval_history,
            total_eval_count=len(eval_history),
            error_message=f"Bad commit {commits[-1]} was skipped: {bad_eval.skip_reason}",
        )
        if out_path:
            _write_result(res, out_path)
        return res

    # Check non-monotonic between endpoints
    if good_eval.status == "bad" and bad_eval.status == "good":
        res = BisectResult(
            status="non_monotonic",
            first_bad_commit=None,
            evaluated_commits=eval_history,
            total_eval_count=len(eval_history),
        )
        if out_path:
            _write_result(res, out_path)
        return res

    if good_eval.status == "bad" and bad_eval.status == "bad":
        res = BisectResult(
            status="not_found",
            first_bad_commit=None,
            evaluated_commits=eval_history,
            total_eval_count=len(eval_history),
        )
        if out_path:
            _write_result(res, out_path)
        return res

    if bad_eval.status == "good":
        # Check if any commit in between regressed and then recovered (non-monotonic)
        max_budget = math.ceil(math.log2(len(commits))) + 2
        stack = [(0, len(commits) - 1)]
        while stack and len(eval_history) < max_budget:
            l, r = stack.pop()
            if l + 1 >= r:
                continue
            m = (l + r) // 2
            if m not in eval_cache:
                mid_eval = evaluate(commits[m])
                if check_non_monotonic(commits, eval_cache) or mid_eval.status == "bad":
                    res = BisectResult(
                        status="non_monotonic",
                        first_bad_commit=None,
                        evaluated_commits=eval_history,
                        total_eval_count=len(eval_history),
                    )
                    if out_path:
                        _write_result(res, out_path)
                    return res
                stack.append((l, m))
                stack.append((m, r))

        res = BisectResult(
            status="not_found",
            first_bad_commit=None,
            evaluated_commits=eval_history,
            total_eval_count=len(eval_history),
        )
        if out_path:
            _write_result(res, out_path)
        return res

    # At this point: commits[0] is good, commits[-1] is bad.
    # 2. Binary search across the range (low, high)
    low = 0
    high = len(commits) - 1

    while low + 1 < high:
        mid = (low + high) // 2
        candidate = mid
        eval_cand = evaluate(commits[candidate])

        # If candidate is skipped, search outward for an unevaluated commit in (low, high)
        if eval_cand.status == "skipped":
            found_alt = False
            max_dist = max(mid - low, high - mid)
            for dist in range(1, max_dist + 1):
                for delta in (-dist, dist):
                    alt = mid + delta
                    if low < alt < high and commits[alt] not in eval_cache:
                        candidate = alt
                        eval_cand = evaluate(commits[candidate])
                        if eval_cand.status != "skipped":
                            found_alt = True
                            break
                if found_alt:
                    break

            if not found_alt and eval_cand.status == "skipped":
                # All candidates between low and high are skipped or evaluated as skipped
                break

        # Check non-monotonicity
        if check_non_monotonic(commits, eval_cache):
            res = BisectResult(
                status="non_monotonic",
                first_bad_commit=None,
                evaluated_commits=eval_history,
                total_eval_count=len(eval_history),
            )
            if out_path:
                _write_result(res, out_path)
            return res

        if eval_cand.status == "good":
            low = candidate
        elif eval_cand.status == "bad":
            high = candidate

    max_budget = math.ceil(math.log2(len(commits))) + 2
    # Verify predecessor within budget to avoid jumping over an earlier bad commit
    if low > 0 and commits[low - 1] not in eval_cache and len(eval_history) < max_budget:
        evaluate(commits[low - 1])
        if check_non_monotonic(commits, eval_cache):
            res = BisectResult(
                status="non_monotonic",
                first_bad_commit=None,
                evaluated_commits=eval_history,
                total_eval_count=len(eval_history),
            )
            if out_path:
                _write_result(res, out_path)
            return res

    # Verify successor within budget to avoid guessing when candidate first_bad is followed by a recovery
    if high < len(commits) - 1 and commits[high + 1] not in eval_cache and len(eval_history) < max_budget:
        evaluate(commits[high + 1])
        if check_non_monotonic(commits, eval_cache):
            res = BisectResult(
                status="non_monotonic",
                first_bad_commit=None,
                evaluated_commits=eval_history,
                total_eval_count=len(eval_history),
            )
            if out_path:
                _write_result(res, out_path)
            return res

    first_bad = commits[high]
    res = BisectResult(
        status="found",
        first_bad_commit=first_bad,
        evaluated_commits=eval_history,
        total_eval_count=len(eval_history),
    )
    if out_path:
        _write_result(res, out_path)
    return res


def _write_result(result: BisectResult, out_path: Path | str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
