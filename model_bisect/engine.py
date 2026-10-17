from __future__ import annotations

import json
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
from typing import Callable, Generator, Sequence

from model_bisect.models import (
    BisectResult,
    BisectStatus,
    CommitEvaluation,
    CommitStatus,
    Direction,
)


def parse_metrics_file(file_path: Path, metric_name: str) -> tuple[float | None, str | None]:
    """Parse metric from the JSON metrics file specified by MODEL_BISECT_METRICS_FILE.

    Validates that:
    1. The file exists and is valid JSON.
    2. The JSON is a flat or top-level dictionary/object.
    3. The requested metric_name key exists.
    4. The metric value is a finite number (not NaN or Inf, and not a boolean).
    """
    if not file_path.exists():
        return None, "Metrics JSON file does not exist"
    try:
        content = file_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        return None, f"Failed to parse metrics JSON file: {e}"

    if not isinstance(data, dict):
        return None, "Metrics JSON file is not a JSON object"

    if metric_name not in data:
        return None, f"Metric '{metric_name}' not found in JSON metrics file"

    val = data[metric_name]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None, f"Metric '{metric_name}' in JSON metrics file is not a number"

    float_val = float(val)
    if math.isnan(float_val) or math.isinf(float_val):
        return None, f"Metric '{metric_name}' in JSON metrics file is not finite (NaN or Inf)"

    return float_val, None


def parse_metric(stdout: str, metric_name: str) -> float | None:
    """Parse metric from stdout fallback matching `name=value` (e.g. accuracy=0.9123).

    Note: Stdout is treated as logs and this fallback is a convenience, not a contract.
    The primary metric contract is the JSON file specified by MODEL_BISECT_METRICS_FILE.

    Rules for stdout fallback:
    1. The metric must appear in the form `name=value` with exact word boundary.
    2. If the metric appears more than once with different values, it is skipped as ambiguous.
    3. If all occurrences have identical values (or it appears on a single line), the value is accepted.
    4. Non-finite values (NaN, Inf) are rejected.
    """
    pattern = (
        rf"(?:^|[\s,;]){re.escape(metric_name)}=\s*"
        rf"([+-]?(?:nan|inf(?:inity)?|\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?=$|[\s,;])"
    )

    lines = stdout.splitlines()
    matches: list[float] = []
    for line in lines:
        for m in re.finditer(pattern, line, flags=re.IGNORECASE):
            try:
                val = float(m.group(1))
            except (ValueError, TypeError):
                continue
            if math.isnan(val) or math.isinf(val):
                return None
            matches.append(val)

    if not matches:
        return None

    # If multiple occurrences appear with different values, ambiguous!
    if not all(math.isclose(v, matches[0], rel_tol=1e-7, abs_tol=1e-7) for v in matches):
        return None

    return matches[0]


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

    # Create temporary metrics JSON file path for MODEL_BISECT_METRICS_FILE contract
    temp_fd, metrics_file_path_str = tempfile.mkstemp(prefix="model_bisect_metrics_", suffix=".json")
    os.close(temp_fd)
    metrics_file = Path(metrics_file_path_str)
    metrics_file.unlink(missing_ok=True)

    env = {**os.environ, "MODEL_BISECT_METRICS_FILE": str(metrics_file)}

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(worktree_path),
            env=env,
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

    try:
        if proc.returncode != 0:
            err_msg = stderr.strip() or stdout.strip() or f"exit code {proc.returncode}"
            return None, f"Process failed with exit code {proc.returncode}: {err_msg[:200]}"

        # 1. Primary source: read MODEL_BISECT_METRICS_FILE
        json_err: str | None = None
        if metrics_file.exists() and metrics_file.stat().st_size > 0:
            val, json_err = parse_metrics_file(metrics_file, metric_name)
            if json_err is None and val is not None:
                return val, None
        else:
            json_err = "Metrics JSON file was not produced"

        # 2. Fallback source: stdout
        val = parse_metric(stdout, metric_name)
        if val is not None:
            return val, None

        return None, f"Metric '{metric_name}' not found: {json_err}; stdout fallback also failed"
    finally:
        if metrics_file.exists():
            metrics_file.unlink(missing_ok=True)


def evaluate_commit(
    repo_path: Path,
    commit: str,
    eval_command: str,
    metric_name: str,
    threshold: float,
    direction: Direction,
    repeat_count: int,
    timeout: float,
    valid_range: tuple[float, float] | None = None,
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

            # Validate against optional valid range
            if valid_range is not None and not (valid_range[0] <= metric <= valid_range[1]):
                return CommitEvaluation(
                    commit=commit,
                    metric=metric,
                    status="skipped",
                    duration=duration,
                    skip_reason=f"Metric value {metric} is outside valid range [{valid_range[0]}, {valid_range[1]}]",
                    eval_runs=run_metrics if repeat_count > 1 else None,
                )

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
    valid_range: tuple[float, float] | None = None,
    out_path: Path | str | None = None,
    on_progress: Callable[[CommitEvaluation], None] | None = None,
) -> BisectResult:
    """Find the git commit that caused an ML model's eval metric to regress.

    Uses binary search over the commit history, classifying commits by metric threshold.

    Note on Monotonicity:
    Non-monotonic detection is best-effort within the evaluation budget ceil(log2(N)) + 2.
    An early regression that recovers can be missed when binary search uses its full budget.
    The returned `BisectResult.monotonicity_verified` indicates whether the predecessor
    verification check was executed and confirmed.
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
            monotonicity_verified=False,
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
            valid_range=valid_range,
        )
        eval_cache[commit_sha] = eval_res
        eval_history.append(eval_res)
        if on_progress is not None:
            try:
                on_progress(eval_res)
            except Exception:
                pass
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
    monotonicity_verified = False

    # Verify predecessor within budget to avoid jumping over an earlier bad commit
    if low > 0 and commits[low - 1] not in eval_cache:
        if len(eval_history) < max_budget:
            evaluate(commits[low - 1])
            if check_non_monotonic(commits, eval_cache):
                res = BisectResult(
                    status="non_monotonic",
                    first_bad_commit=None,
                    evaluated_commits=eval_history,
                    total_eval_count=len(eval_history),
                    monotonicity_verified=False,
                )
                if out_path:
                    _write_result(res, out_path)
                return res
            monotonicity_verified = True
        else:
            # Full budget reached before predecessor check could run
            monotonicity_verified = False
    elif low == 0 or (low > 0 and commits[low - 1] in eval_cache):
        monotonicity_verified = True

    # Verify successor within budget to avoid guessing when candidate first_bad is followed by a recovery
    if high < len(commits) - 1 and commits[high + 1] not in eval_cache and len(eval_history) < max_budget:
        evaluate(commits[high + 1])
        if check_non_monotonic(commits, eval_cache):
            res = BisectResult(
                status="non_monotonic",
                first_bad_commit=None,
                evaluated_commits=eval_history,
                total_eval_count=len(eval_history),
                monotonicity_verified=False,
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
        monotonicity_verified=monotonicity_verified,
    )
    if out_path:
        _write_result(res, out_path)
    return res


def _write_result(result: BisectResult, out_path: Path | str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
