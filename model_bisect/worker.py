from __future__ import annotations

import logging
import os
import signal
import sys
import subprocess
import time
from pathlib import Path
from typing import Any

from model_bisect.config import (
    RepoPathNotAllowedError,
    TargetNotFoundError,
    get_validated_target,
    load_targets,
)
from model_bisect.db import (
    claim_next_job,
    fail_job,
    fail_stale_running_jobs,
    finish_job,
    increment_progress,
    init_db,
)
from model_bisect.engine import bisect
from model_bisect.models import CommitEvaluation, JobResponse

logger = logging.getLogger("model_bisect.worker")


def prune_target_worktrees(config_path: Path | str | None = None) -> dict[str, str]:
    """Run git worktree prune on each configured target's repository at startup.

    Logs what was removed (if any) for each target repository.
    Returns a dict mapping target names to prune output.
    """
    targets = load_targets(config_path=config_path)
    prune_results: dict[str, str] = {}
    for name, target in targets.items():
        repo = Path(target.repo_path)
        if not repo.exists() or not repo.is_dir():
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "worktree", "prune", "-v"],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (proc.stdout + proc.stderr).strip()
            prune_results[name] = output
            if output:
                logger.info(f"Target '{name}' ({repo}) worktree prune: {output}")
            else:
                logger.info(f"Target '{name}' ({repo}) worktree prune: 0 stale worktrees removed")
        except Exception as e:
            logger.warning(f"Failed to prune worktrees for target '{name}' ({repo}): {e}")
    return prune_results



def execute_job(
    job: JobResponse,
    db_path: Path | str | None = None,
    config_path: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> None:
    """Execute a single claimed job to completion or failure."""
    logger.info(f"Starting execution of job {job.id} for target '{job.target}'")
    try:
        # Validate target and ensure repo path is within allowlisted root
        target = get_validated_target(
            target_name=job.target,
            config_path=config_path,
            repo_root=repo_root,
        )
    except (TargetNotFoundError, RepoPathNotAllowedError) as e:
        logger.error(f"Target validation failed for job {job.id}: {e}")
        fail_job(job.id, str(e), db_path=db_path)
        return
    except Exception as e:
        logger.exception(f"Unexpected configuration error for job {job.id}: {e}")
        fail_job(job.id, f"Configuration error: {e}", db_path=db_path)
        return

    # Use threshold override if requested, otherwise default to target threshold
    threshold = (
        job.threshold_override
        if job.threshold_override is not None
        else target.threshold
    )

    def on_progress_callback(eval_res: CommitEvaluation) -> None:
        try:
            increment_progress(job.id, db_path=db_path)
            logger.debug(
                f"Job {job.id} progress: commit {eval_res.commit[:7]} status={eval_res.status}"
            )
        except Exception as e:
            logger.warning(f"Failed to increment progress for job {job.id}: {e}")

    try:
        result = bisect(
            repo_path=target.repo_path,
            good_commit=job.good_commit,
            bad_commit=job.bad_commit,
            eval_command=target.eval_command,
            metric_name=target.metric_name,
            threshold=threshold,
            direction=target.direction,
            repeat_count=target.repeat_count,
            timeout=target.timeout,
            valid_range=target.valid_range,
            on_progress=on_progress_callback,
        )
        if result.status == "error":
            fail_job(job.id, result.error_message or "Bisect failed with error", db_path=db_path)
            logger.warning(f"Job {job.id} failed with bisect error: {result.error_message}")
        else:
            culprit_subject = None
            if result.first_bad_commit:
                try:
                    proc = subprocess.run(
                        ["git", "-C", str(target.repo_path), "log", "-1", "--format=%s", result.first_bad_commit],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if proc.returncode == 0:
                        culprit_subject = proc.stdout.strip()
                except Exception:
                    pass

            finish_job(job.id, result, db_path=db_path, culprit_subject=culprit_subject)
            logger.info(f"Job {job.id} completed with status: {result.status}")
    except Exception as e:
        logger.exception(f"Engine failure during job {job.id}: {e}")
        fail_job(job.id, f"Evaluation error: {e}", db_path=db_path)


def run_worker_loop(
    poll_interval: float = 1.0,
    run_once: bool = False,
    db_path: Path | str | None = None,
    config_path: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> None:
    """Run worker loop polling for and executing queued jobs."""
    init_db(db_path=db_path)
    stale_count = fail_stale_running_jobs(db_path=db_path)
    if stale_count > 0:
        logger.warning(f"Marked {stale_count} stale running job(s) as failed on startup")

    prune_target_worktrees(config_path=config_path)

    running = True


    def _sig_handler(signum: int, frame: Any) -> None:
        nonlocal running
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        running = False

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, AttributeError):
        pass  # In sub-threads signals cannot be set

    logger.info("Model Bisect worker initialized and ready for jobs.")

    while running:
        try:
            job = claim_next_job(db_path=db_path)
            if job is not None:
                execute_job(
                    job,
                    db_path=db_path,
                    config_path=config_path,
                    repo_root=repo_root,
                )
                if run_once:
                    break
                continue
        except Exception as e:
            logger.exception(f"Unexpected worker loop exception: {e}")

        if run_once:
            break

        time.sleep(poll_interval)

    logger.info("Worker loop exiting.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    poll_sec = float(os.environ.get("MODEL_BISECT_POLL_INTERVAL", "1.0"))
    run_worker_loop(poll_interval=poll_sec)
