from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from model_bisect.models import BisectResult, JobResponse, JobStatus


def get_db_path() -> Path:
    """Return configured SQLite database file path."""
    raw = os.environ.get("MODEL_BISECT_DB_PATH", "model_bisect.db")
    return Path(raw).resolve()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create a new SQLite connection configured with WAL mode and row factory."""
    path = Path(db_path).resolve() if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=30.0,
        isolation_level=None,  # We manage transactions explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    """Create jobs schema if not already present."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                good_commit TEXT NOT NULL,
                bad_commit TEXT NOT NULL,
                threshold_override REAL,
                status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed')),
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                progress_count INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error_message TEXT
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _row_to_job_response(row: sqlite3.Row) -> JobResponse:
    """Convert a database row to a JobResponse model."""
    res_obj: BisectResult | None = None
    if row["result_json"]:
        try:
            res_obj = BisectResult.model_validate_json(row["result_json"])
        except Exception:
            res_obj = None

    return JobResponse(
        id=row["id"],
        target=row["target"],
        good_commit=row["good_commit"],
        bad_commit=row["bad_commit"],
        threshold_override=row["threshold_override"],
        status=row["status"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        progress_count=row["progress_count"],
        result=res_obj,
        error_message=row["error_message"],
    )


def create_job(
    target: str,
    good_commit: str,
    bad_commit: str,
    threshold: float | None = None,
    db_path: Path | str | None = None,
) -> JobResponse:
    """Insert a new job in queued status."""
    conn = get_connection(db_path)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO jobs (
                id, target, good_commit, bad_commit, threshold_override,
                status, created_at, started_at, finished_at, progress_count,
                result_json, error_message
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, NULL, 0, NULL, NULL)
            """,
            (job_id, target, good_commit, bad_commit, threshold, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    res = get_job(job_id, db_path)
    if not res:
        raise RuntimeError("Failed to retrieve newly created job")
    return res


def get_job(job_id: str, db_path: Path | str | None = None) -> JobResponse | None:
    """Retrieve job details by ID."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return None
        return _row_to_job_response(row)
    finally:
        conn.close()


def list_jobs(
    status: JobStatus | None = None,
    limit: int = 20,
    db_path: Path | str | None = None,
) -> list[JobResponse]:
    """List most recent jobs, newest first."""
    conn = get_connection(db_path)
    try:
        if status:
            cur = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = cur.fetchall()
        return [_row_to_job_response(r) for r in rows]
    finally:
        conn.close()


def claim_next_job(db_path: Path | str | None = None) -> JobResponse | None:
    """Atomically claim the oldest queued job and transition it to running.

    Guarantees that only one worker claims the job even in concurrent worker races.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            conn.execute("COMMIT")
            return None

        job_id = row["id"]
        now = datetime.now(timezone.utc).isoformat()
        update_cur = conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
            (now, job_id),
        )
        if update_cur.rowcount == 1:
            conn.execute("COMMIT")
            return get_job(job_id, db_path)

        conn.execute("COMMIT")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def increment_progress(job_id: str, db_path: Path | str | None = None) -> None:
    """Increment progress_count by 1 for a job."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE jobs SET progress_count = progress_count + 1 WHERE id = ?",
            (job_id,),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def finish_job(
    job_id: str,
    result: BisectResult,
    db_path: Path | str | None = None,
) -> None:
    """Mark a running job as done with its serialized BisectResult."""
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    result_json = result.model_dump_json()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE jobs
            SET status = 'done', result_json = ?, finished_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (result_json, now, job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def fail_job(
    job_id: str,
    error_message: str,
    db_path: Path | str | None = None,
) -> None:
    """Mark a job as failed with an error message."""
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', error_message = ?, finished_at = ?
            WHERE id = ?
            """,
            (error_message, now, job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def fail_stale_running_jobs(db_path: Path | str | None = None) -> int:
    """Mark any jobs left in 'running' by a crashed/dead worker as failed."""
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    msg = "Worker terminated unexpectedly while job was running"
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', error_message = ?, finished_at = ?
            WHERE status = 'running'
            """,
            (msg, now),
        )
        count = cur.rowcount
        conn.execute("COMMIT")
        return count
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def check_db_health(db_path: Path | str | None = None) -> bool:
    """Verify SQLite database connectivity."""
    try:
        conn = get_connection(db_path)
        cur = conn.execute("SELECT 1")
        val = cur.fetchone()[0]
        conn.close()
        return val == 1
    except Exception:
        return False
