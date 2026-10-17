from __future__ import annotations

import os
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from model_bisect.api import app
from model_bisect.db import init_db


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated DB, repo root, and targets config for API testing."""
    db_file = tmp_path / "test_api.db"
    repo_root = tmp_path / "repos"
    repo_root.mkdir(parents=True, exist_ok=True)
    targets_file = tmp_path / "targets.toml"

    # Create dummy repo within allowlist
    valid_repo = repo_root / "model_alpha"
    valid_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(valid_repo), check=True, capture_output=True)

    # Create dummy repo outside allowlist
    outside_repo = tmp_path / "outside_root" / "secret_repo"
    outside_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(outside_repo), check=True, capture_output=True)

    targets_content = f"""
    [targets.valid_target]
    repo_path = "{valid_repo}"
    eval_command = "python eval.py"
    metric_name = "accuracy"
    threshold = 0.90
    direction = "higher"
    valid_range = [0.0, 1.0]
    timeout = 30.0
    repeat_count = 1

    [targets.traversal_target]
    repo_path = "{outside_repo}"
    eval_command = "python eval.py"
    metric_name = "accuracy"
    threshold = 0.90
    direction = "higher"
    """
    targets_file.write_text(targets_content, encoding="utf-8")

    monkeypatch.setenv("MODEL_BISECT_DB_PATH", str(db_file))
    monkeypatch.setenv("MODEL_BISECT_TARGETS_FILE", str(targets_file))
    monkeypatch.setenv("MODEL_BISECT_REPO_ROOT", str(repo_root))

    init_db(db_file)

    with TestClient(app) as client:
        yield client, valid_repo


def test_health_check(api_env):
    client, _ = api_env
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "connected"}


def test_create_and_get_job(api_env):
    client, _ = api_env
    payload = {
        "target": "valid_target",
        "good_commit": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "bad_commit": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        "threshold": 0.88,
    }
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["target"] == "valid_target"
    assert data["status"] == "queued"
    assert data["good_commit"] == payload["good_commit"]
    assert data["bad_commit"] == payload["bad_commit"]
    assert data["threshold_override"] == 0.88
    job_id = data["id"]

    # Retrieve job
    get_resp = client.get(f"/jobs/{job_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == job_id


def test_get_unknown_job_404(api_env):
    client, _ = api_env
    resp = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_invalid_commit_string_422(api_env):
    client, _ = api_env
    # Semicolons / shell injection characters
    bad_payload = {
        "target": "valid_target",
        "good_commit": "v1.0.0; rm -rf /",
        "bad_commit": "HEAD",
    }
    resp = client.post("/jobs", json=bad_payload)
    assert resp.status_code == 422
    assert "Invalid commit reference" in resp.json()["detail"]

    # Leading hyphen (could be interpreted as git option flag)
    bad_payload2 = {
        "target": "valid_target",
        "good_commit": "--upload-pack=evil",
        "bad_commit": "HEAD",
    }
    resp2 = client.post("/jobs", json=bad_payload2)
    assert resp2.status_code == 422


def test_unknown_target_400(api_env):
    client, _ = api_env
    payload = {
        "target": "non_existent_target",
        "good_commit": "HEAD~5",
        "bad_commit": "HEAD",
    }
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 400
    assert "not found in configuration" in resp.json()["detail"]


def test_repo_path_traversal_rejected_400(api_env):
    client, _ = api_env
    payload = {
        "target": "traversal_target",
        "good_commit": "HEAD~1",
        "bad_commit": "HEAD",
    }
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 400
    assert "allowlisted root" in resp.json()["detail"]


def test_list_jobs_and_filter(api_env):
    client, _ = api_env
    # Create multiple jobs
    for i in range(3):
        client.post(
            "/jobs",
            json={
                "target": "valid_target",
                "good_commit": f"commit_{i}",
                "bad_commit": f"commit_{i+1}",
            },
        )

    resp = client.get("/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 3

    # Filter by status
    resp_queued = client.get("/jobs?status=queued")
    assert resp_queued.status_code == 200
    assert len(resp_queued.json()) == 3

    resp_done = client.get("/jobs?status=done")
    assert resp_done.status_code == 200
    assert len(resp_done.json()) == 0
