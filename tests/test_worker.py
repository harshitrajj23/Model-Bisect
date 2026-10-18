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
from model_bisect.worker import execute_job, prune_target_worktrees, run_worker_loop
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


def test_end_to_end_alternating_repo_non_monotonic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """E2E test: alternating history (0.95, 0.80, 0.95, 0.80, 0.95) submitted via API,

    executed by worker, returns non_monotonic status with monotonicity_verified=False and
    first_bad_commit=None.
    """
    db_file = tmp_path / "alternating_e2e.db"
    repo_root = tmp_path / "repos"
    repo_root.mkdir(parents=True, exist_ok=True)
    targets_file = tmp_path / "targets.toml"

    # Create 5-commit alternating repo
    alt_repo = repo_root / "alternating_model"
    alt_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(alt_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(alt_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(alt_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=str(alt_repo), check=True, capture_output=True)

    metrics = [0.95, 0.80, 0.95, 0.80, 0.95]
    commits: list[str] = []
    for i, m in enumerate(metrics):
        (alt_repo / "eval.py").write_text(f"print('accuracy={m}')\n", encoding="utf-8")
        (alt_repo / "v.txt").write_text(f"{i}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(alt_repo), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"c{i}: {m}"], cwd=str(alt_repo), check=True, capture_output=True)
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(alt_repo), check=True, capture_output=True, text=True)
        commits.append(out.stdout.strip())

    targets_content = f"""
    [targets.alternating_model]
    repo_path = "{alt_repo}"
    eval_command = "{sys.executable} eval.py"
    metric_name = "accuracy"
    threshold = 0.90
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

    with TestClient(app) as client:
        # Job B test: c0 and c3 (valid alternating endpoints -> non_monotonic)
        resp = client.post(
            "/jobs",
            json={
                "target": "alternating_model",
                "good_commit": commits[0],
                "bad_commit": commits[3],
            },
        )
        assert resp.status_code == 202
        job_id = resp.json()["id"]

        run_worker_loop(
            poll_interval=0.1,
            run_once=True,
            db_path=db_file,
            config_path=targets_file,
            repo_root=repo_root,
        )

        fetch_resp = client.get(f"/jobs/{job_id}")
        assert fetch_resp.status_code == 200
        completed = fetch_resp.json()
        assert completed["status"] == "done"
        assert completed["result"] is not None
        assert completed["result"]["status"] == "non_monotonic"
        assert completed["result"]["monotonicity_verified"] is False
        assert completed["result"]["first_bad_commit"] is None


def test_worker_endpoint_error_marks_job_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Submitting c4 as bad_commit returns error from bisect and marks job as failed with endpoint message."""
    db_file = tmp_path / "worker_err.db"
    repo_root = tmp_path / "repos_err"
    repo_root.mkdir(parents=True, exist_ok=True)
    targets_file = tmp_path / "targets_err.toml"

    alt_repo = repo_root / "alternating_model"
    alt_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(alt_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(alt_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(alt_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=str(alt_repo), check=True, capture_output=True)

    metrics = [0.95, 0.80, 0.95, 0.80, 0.95]
    commits: list[str] = []
    for i, m in enumerate(metrics):
        (alt_repo / "eval.py").write_text(f"print('accuracy={m}')\n", encoding="utf-8")
        (alt_repo / "v.txt").write_text(f"{i}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(alt_repo), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"c{i}: {m}"], cwd=str(alt_repo), check=True, capture_output=True)
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(alt_repo), check=True, capture_output=True, text=True)
        commits.append(out.stdout.strip())

    targets_content = f"""
    [targets.alternating_model]
    repo_path = "{alt_repo}"
    eval_command = "{sys.executable} eval.py"
    metric_name = "accuracy"
    threshold = 0.90
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

    with TestClient(app) as client:
        # Job A test: submits c4 (commits[4]) as bad_commit
        resp = client.post(
            "/jobs",
            json={
                "target": "alternating_model",
                "good_commit": commits[0],
                "bad_commit": commits[4],
            },
        )
        assert resp.status_code == 202
        job_id = resp.json()["id"]

        run_worker_loop(
            poll_interval=0.1,
            run_once=True,
            db_path=db_file,
            config_path=targets_file,
            repo_root=repo_root,
        )

        fetch_resp = client.get(f"/jobs/{job_id}")
        assert fetch_resp.status_code == 200
        failed_job = fetch_resp.json()
        assert failed_job["status"] == "failed"
        assert failed_job["error_message"] is not None
        assert commits[4] in failed_job["error_message"]
        assert "expected 'bad'" in failed_job["error_message"]


def test_prune_target_worktrees_at_startup(tmp_path: Path):
    """Test that prune_target_worktrees removes stale worktree metadata on target repositories."""
    import shutil

    repo = tmp_path / "prune_repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=str(repo), check=True, capture_output=True)

    (repo / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    # Create a worktree
    wt_dir = tmp_path / "temp_worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt_dir), "HEAD"], check=True, capture_output=True)
    assert (repo / ".git" / "worktrees" / wt_dir.name).exists()

    # Simulate crash: remove worktree directory directly without git worktree remove
    shutil.rmtree(wt_dir)

    targets_file = tmp_path / "targets.toml"
    targets_file.write_text(f"""
    [targets.test_prune]
    repo_path = "{repo}"
    eval_command = "echo ok"
    metric_name = "acc"
    threshold = 0.5
    """, encoding="utf-8")

    # Run prune_target_worktrees
    pruned = prune_target_worktrees(config_path=targets_file)
    assert "test_prune" in pruned
    assert wt_dir.name in pruned["test_prune"]
    assert not (repo / ".git" / "worktrees" / wt_dir.name).exists()


