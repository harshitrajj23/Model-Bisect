from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from model_bisect.api import app
from model_bisect.db import (
    claim_next_job,
    create_job,
    fail_stale_running_jobs,
    get_connection,
    get_job,
    init_db,
)
from model_bisect.models import BisectResult, CommitEvaluation
from model_bisect.worker import execute_job, run_worker_loop
from tests.test_engine import create_git_repo_with_commits


@pytest.fixture
def worker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up database, repo root, and targets for worker testing."""
    db_file = tmp_path / "worker.db"
    repo_root = tmp_path / "repos"
    repo_root.mkdir(parents=True, exist_ok=True)
    targets_file = tmp_path / "targets.toml"

    monkeypatch.setenv("MODEL_BISECT_DB_PATH", str(db_file))
    monkeypatch.setenv("MODEL_BISECT_TARGETS_FILE", str(targets_file))
    monkeypatch.setenv("MODEL_BISECT_REPO_ROOT", str(repo_root))

    init_db(db_file)
    return db_file, targets_file, repo_root


def test_stale_running_job_marked_failed_on_startup(worker_env):
    db_file, _, _ = worker_env

    # Insert a job directly in 'running' status simulating a crashed worker
    job = create_job("target_a", "c0", "c1", db_path=db_file)
    conn = get_connection(db_file)
    conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job.id,))
    conn.close()

    updated_count = fail_stale_running_jobs(db_path=db_file)
    assert updated_count == 1

    recovered_job = get_job(job.id, db_path=db_file)
    assert recovered_job is not None
    assert recovered_job.status == "failed"
    assert "Worker terminated unexpectedly" in recovered_job.error_message


def test_two_claim_attempts_atomic_race(worker_env):
    db_file, _, _ = worker_env
    job = create_job("target_a", "c0", "c1", db_path=db_file)

    results = []

    def try_claim():
        claimed = claim_next_job(db_path=db_file)
        if claimed:
            results.append(claimed.id)

    # Launch two concurrent claim attempts
    t1 = threading.Thread(target=try_claim)
    t2 = threading.Thread(target=try_claim)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one thread must have succeeded
    assert len(results) == 1
    assert results[0] == job.id

    # Third attempt gets None
    assert claim_next_job(db_path=db_file) is None


def test_job_execution_success_and_progress(worker_env):
    db_file, targets_file, repo_root = worker_env
    repo_dir = repo_root / "repo_dummy"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)

    targets_content = f"""
    [targets.dummy]
    repo_path = "{repo_dir}"
    eval_command = "echo test"
    metric_name = "acc"
    threshold = 0.5
    """
    targets_file.write_text(targets_content, encoding="utf-8")

    job = create_job("dummy", "c0", "c1", db_path=db_file)
    claimed = claim_next_job(db_path=db_file)
    assert claimed is not None

    dummy_result = BisectResult(
        status="found",
        first_bad_commit="c1",
        evaluated_commits=[
            CommitEvaluation(commit="c0", metric=0.9, status="good", duration=0.1),
            CommitEvaluation(commit="c1", metric=0.4, status="bad", duration=0.1),
        ],
        total_eval_count=2,
        monotonicity_verified=True,
    )

    with patch("model_bisect.worker.bisect") as mock_bisect:
        def side_effect(*args, **kwargs):
            on_progress = kwargs.get("on_progress")
            if on_progress:
                on_progress(dummy_result.evaluated_commits[0])
                on_progress(dummy_result.evaluated_commits[1])
            return dummy_result

        mock_bisect.side_effect = side_effect

        execute_job(claimed, db_path=db_file, config_path=targets_file, repo_root=repo_root)

    finished_job = get_job(job.id, db_path=db_file)
    assert finished_job.status == "done"
    assert finished_job.progress_count == 2
    assert finished_job.result is not None
    assert finished_job.result.status == "found"
    assert finished_job.result.first_bad_commit == "c1"
    assert finished_job.result.monotonicity_verified is True


def test_job_engine_exception_marks_job_failed(worker_env):
    db_file, targets_file, repo_root = worker_env
    repo_dir = repo_root / "repo_dummy"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)

    targets_content = f"""
    [targets.dummy]
    repo_path = "{repo_dir}"
    eval_command = "echo test"
    metric_name = "acc"
    threshold = 0.5
    """
    targets_file.write_text(targets_content, encoding="utf-8")

    job = create_job("dummy", "c0", "c1", db_path=db_file)
    claimed = claim_next_job(db_path=db_file)
    assert claimed is not None

    with patch("model_bisect.worker.bisect") as mock_bisect:
        mock_bisect.side_effect = RuntimeError("Fatal git worktree crash")
        execute_job(claimed, db_path=db_file, config_path=targets_file, repo_root=repo_root)

    failed_job = get_job(job.id, db_path=db_file)
    assert failed_job.status == "failed"
    assert "Fatal git worktree crash" in failed_job.error_message


@pytest.mark.slow
def test_end_to_end_planted_regression_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """E2E test: submit job via API, run worker step, verify API returns planted culprit."""
    db_file = tmp_path / "e2e.db"
    repo_root = tmp_path / "repos"
    repo_root.mkdir(parents=True, exist_ok=True)
    targets_file = tmp_path / "targets.toml"

    # Create planted regression repository
    planted_repo = repo_root / "planted_model"
    regression_index = 12
    _, commits = create_git_repo_with_commits(
        planted_repo,
        num_commits=20,
        regression_commit=regression_index,
        good_metric=0.98,
        bad_metric=0.82,
    )

    targets_content = f"""
    [targets.planted_model]
    repo_path = "{planted_repo}"
    eval_command = "{sys.executable} eval.py"
    metric_name = "accuracy"
    threshold = 0.95
    direction = "higher"
    valid_range = [0.0, 1.0]
    timeout = 30.0
    repeat_count = 1
    """
    targets_file.write_text(targets_content, encoding="utf-8")

    monkeypatch.setenv("MODEL_BISECT_DB_PATH", str(db_file))
    monkeypatch.setenv("MODEL_BISECT_TARGETS_FILE", str(targets_file))
    monkeypatch.setenv("MODEL_BISECT_REPO_ROOT", str(repo_root))

    init_db(db_file)

    # 1. Create job via API client
    with TestClient(app) as client:
        resp = client.post(
            "/jobs",
            json={
                "target": "planted_model",
                "good_commit": commits[0],
                "bad_commit": commits[-1],
            },
        )
        assert resp.status_code == 202
        job_data = resp.json()
        job_id = job_data["id"]
        assert job_data["status"] == "queued"

        # 2. Run one worker step to claim and execute the job
        run_worker_loop(
            poll_interval=0.1,
            run_once=True,
            db_path=db_file,
            config_path=targets_file,
            repo_root=repo_root,
        )

        # 3. Retrieve completed job from API
        fetch_resp = client.get(f"/jobs/{job_id}")
        assert fetch_resp.status_code == 200
        completed = fetch_resp.json()
        assert completed["status"] == "done"
        assert completed["progress_count"] > 0
        assert completed["result"] is not None
        assert completed["result"]["status"] == "found"
        assert completed["result"]["first_bad_commit"] == commits[regression_index]
        assert completed["result"]["monotonicity_verified"] is True
