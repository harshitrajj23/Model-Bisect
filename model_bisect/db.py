from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from model_bisect.models import BisectResult, JobResponse, JobStatus


def is_postgres(target: Path | str | None = None) -> bool:
    """Return True if the target or environment specifies a PostgreSQL database."""
    if target is not None:
        s = str(target).strip()
        return s.startswith("postgresql://") or s.startswith("postgres://")
    env_url = os.environ.get("DATABASE_URL") or os.environ.get("MODEL_BISECT_DATABASE_URL")
    if env_url:
        s = env_url.strip()
        return s.startswith("postgresql://") or s.startswith("postgres://")
    raw = os.environ.get("MODEL_BISECT_DB_PATH", "")
    return raw.strip().startswith("postgresql://") or raw.strip().startswith("postgres://")


def get_pg_url(db_path: Path | str | None = None) -> str:
    """Return PostgreSQL connection URL from argument or environment."""
    if db_path is not None:
        s = str(db_path).strip()
        if s.startswith("postgresql://") or s.startswith("postgres://"):
            return s
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("MODEL_BISECT_DATABASE_URL")
        or os.environ.get("MODEL_BISECT_DB_PATH", "")
    ).strip()
    if not url:
        raise ValueError("No PostgreSQL connection URL configured in environment or arguments")
    return url


def get_pg_connection(db_path: Path | str | None = None):
    """Create a new PostgreSQL connection with RealDictCursor."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    url = get_pg_url(db_path)
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)


def get_db_path() -> Path:
    """Return configured SQLite database file path."""
    raw = os.environ.get("MODEL_BISECT_DB_PATH", "model_bisect.db")
    return Path(raw).resolve()


def get_connection(db_path: Path | str | None = None):
    """Create a new database connection (PostgreSQL if configured, otherwise SQLite)."""
    if is_postgres(db_path):
        return get_pg_connection(db_path)

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


def _row_to_job_response(row: Any) -> JobResponse:
    """Convert a database row (sqlite3.Row or psycopg2 RealDictRow) to a JobResponse model."""
    res_obj: BisectResult | None = None
    result_val = row["result_json"]
    if result_val:
        if isinstance(result_val, dict):
            try:
                res_obj = BisectResult.model_validate(result_val)
            except Exception:
                res_obj = None
        else:
            try:
                res_obj = BisectResult.model_validate_json(str(result_val))
            except Exception:
                res_obj = None

    def _fmt_ts(val: Any) -> str | None:
        if val is None:
            return None
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return str(val)

    return JobResponse(
        id=str(row["id"]),
        target=row["target"],
        good_commit=row["good_commit"],
        bad_commit=row["bad_commit"],
        threshold_override=row["threshold_override"],
        status=row["status"],
        created_at=_fmt_ts(row["created_at"]) or "",
        started_at=_fmt_ts(row["started_at"]),
        finished_at=_fmt_ts(row["finished_at"]),
        progress_count=row["progress_count"],
        result=res_obj,
        error_message=row["error_message"],
    )


# ---------------------------------------------------------------------------
# SQLite Implementations
# ---------------------------------------------------------------------------


def _sqlite_init_db(db_path: Path | str | None = None) -> None:
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


def _sqlite_create_job(
    target: str,
    good_commit: str,
    bad_commit: str,
    threshold: float | None = None,
    db_path: Path | str | None = None,
) -> JobResponse:
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


def _sqlite_get_job(job_id: str, db_path: Path | str | None = None) -> JobResponse | None:
    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return None
        return _row_to_job_response(row)
    finally:
        conn.close()


def _sqlite_list_jobs(
    status: JobStatus | None = None,
    limit: int = 20,
    db_path: Path | str | None = None,
) -> list[JobResponse]:
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


def _sqlite_claim_next_job(db_path: Path | str | None = None) -> JobResponse | None:
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


def _sqlite_increment_progress(job_id: str, db_path: Path | str | None = None) -> None:
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


def _sqlite_finish_job(
    job_id: str,
    result: BisectResult,
    db_path: Path | str | None = None,
) -> None:
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


def _sqlite_fail_job(
    job_id: str,
    error_message: str,
    db_path: Path | str | None = None,
) -> None:
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


def _sqlite_fail_stale_running_jobs(db_path: Path | str | None = None) -> int:
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


def _sqlite_check_db_health(db_path: Path | str | None = None) -> bool:
    try:
        conn = get_connection(db_path)
        cur = conn.execute("SELECT 1")
        val = cur.fetchone()[0]
        conn.close()
        return val == 1
    except Exception:
        return False


# ---------------------------------------------------------------------------
# PostgreSQL / Supabase Implementations
# ---------------------------------------------------------------------------


def _pg_init_db(db_path: Path | str | None = None) -> None:
    conn = get_pg_connection(db_path)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    good_commit TEXT NOT NULL,
                    bad_commit TEXT NOT NULL,
                    threshold_override DOUBLE PRECISION,
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed')),
                    created_at TIMESTAMPTZ NOT NULL,
                    started_at TIMESTAMPTZ,
                    finished_at TIMESTAMPTZ,
                    progress_count INTEGER NOT NULL DEFAULT 0,
                    result_json JSONB,
                    error_message TEXT,
                    culprit_commit TEXT,
                    culprit_subject TEXT,
                    metric_name TEXT,
                    total_eval_count INTEGER,
                    monotonicity_verified BOOLEAN,
                    duration_seconds DOUBLE PRECISION
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);

                CREATE TABLE IF NOT EXISTS job_evaluations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    commit_hash TEXT NOT NULL,
                    metric_value DOUBLE PRECISION,
                    status TEXT NOT NULL,
                    duration DOUBLE PRECISION,
                    skip_reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_evaluations_job_id ON job_evaluations(job_id);
                """
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_create_job(
    target: str,
    good_commit: str,
    bad_commit: str,
    threshold: float | None = None,
    db_path: Path | str | None = None,
) -> JobResponse:
    conn = get_pg_connection(db_path)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    m_name = None
    try:
        from model_bisect.config import load_targets
        t_cfg = load_targets().get(target)
        if t_cfg:
            m_name = t_cfg.metric_name
    except Exception:
        pass

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    id, target, good_commit, bad_commit, threshold_override,
                    status, created_at, started_at, finished_at, progress_count,
                    result_json, error_message, metric_name
                ) VALUES (%s, %s, %s, %s, %s, 'queued', %s, NULL, NULL, 0, NULL, NULL, %s)
                RETURNING *;
                """,
                (job_id, target, good_commit, bad_commit, threshold, now, m_name),
            )
            row = cur.fetchone()
            conn.commit()
            return _row_to_job_response(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_get_job(job_id: str, db_path: Path | str | None = None) -> JobResponse | None:
    conn = get_pg_connection(db_path)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_job_response(row)
    finally:
        conn.close()


def _pg_list_jobs(
    status: JobStatus | None = None,
    limit: int = 20,
    db_path: Path | str | None = None,
) -> list[JobResponse]:
    conn = get_pg_connection(db_path)
    try:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM jobs WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
            return [_row_to_job_response(r) for r in rows]
    finally:
        conn.close()


def _pg_claim_next_job(db_path: Path | str | None = None) -> JobResponse | None:
    conn = get_pg_connection(db_path)
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = %s
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *;
                """,
                (now,),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return None
            return _row_to_job_response(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_increment_progress(job_id: str, db_path: Path | str | None = None) -> None:
    conn = get_pg_connection(db_path)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET progress_count = progress_count + 1 WHERE id = %s",
                (job_id,),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_finish_job(
    job_id: str,
    result: BisectResult,
    db_path: Path | str | None = None,
    culprit_subject: str | None = None,
) -> None:
    from psycopg2.extras import Json

    conn = get_pg_connection(db_path)
    now = datetime.now(timezone.utc)
    result_dict = result.model_dump()
    culprit_commit = result.first_bad_commit
    total_eval_count = result.total_eval_count
    monotonicity_verified = result.monotonicity_verified

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT started_at FROM jobs WHERE id = %s", (job_id,))
            st_row = cur.fetchone()
            dur_sec = None
            if st_row and st_row.get("started_at"):
                dur_sec = (now - st_row["started_at"]).total_seconds()

            cur.execute(
                """
                UPDATE jobs
                SET status = 'done',
                    result_json = %s,
                    finished_at = %s,
                    culprit_commit = %s,
                    culprit_subject = %s,
                    total_eval_count = %s,
                    monotonicity_verified = %s,
                    duration_seconds = %s
                WHERE id = %s AND status = 'running'
                """,
                (
                    Json(result_dict),
                    now,
                    culprit_commit,
                    culprit_subject,
                    total_eval_count,
                    monotonicity_verified,
                    dur_sec,
                    job_id,
                ),
            )

            for ev in result.evaluated_commits:
                cur.execute(
                    """
                    INSERT INTO job_evaluations (job_id, commit_hash, metric_value, status, duration, skip_reason)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (job_id, ev.commit, ev.metric, ev.status, ev.duration, ev.skip_reason),
                )

            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_fail_job(
    job_id: str,
    error_message: str,
    db_path: Path | str | None = None,
) -> None:
    conn = get_pg_connection(db_path)
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'failed', error_message = %s, finished_at = %s
                WHERE id = %s
                """,
                (error_message, now, job_id),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_fail_stale_running_jobs(db_path: Path | str | None = None) -> int:
    conn = get_pg_connection(db_path)
    now = datetime.now(timezone.utc)
    msg = "Worker terminated unexpectedly while job was running"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'failed', error_message = %s, finished_at = %s
                WHERE status = 'running'
                """,
                (msg, now),
            )
            count = cur.rowcount
            conn.commit()
            return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_check_db_health(db_path: Path | str | None = None) -> bool:
    try:
        conn = get_pg_connection(db_path)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            val = cur.fetchone()
            conn.close()
            return val is not None and list(val.values())[0] == 1
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public Facade (Dispatches to PostgreSQL or SQLite based on configuration)
# ---------------------------------------------------------------------------


def init_db(db_path: Path | str | None = None) -> None:
    """Create jobs schema if not already present."""
    if is_postgres(db_path):
        _pg_init_db(db_path)
    else:
        _sqlite_init_db(db_path)


def create_job(
    target: str,
    good_commit: str,
    bad_commit: str,
    threshold: float | None = None,
    db_path: Path | str | None = None,
) -> JobResponse:
    """Insert a new job in queued status."""
    if is_postgres(db_path):
        return _pg_create_job(target, good_commit, bad_commit, threshold, db_path)
    return _sqlite_create_job(target, good_commit, bad_commit, threshold, db_path)


def get_job(job_id: str, db_path: Path | str | None = None) -> JobResponse | None:
    """Retrieve job details by ID."""
    if is_postgres(db_path):
        return _pg_get_job(job_id, db_path)
    return _sqlite_get_job(job_id, db_path)


def list_jobs(
    status: JobStatus | None = None,
    limit: int = 20,
    db_path: Path | str | None = None,
) -> list[JobResponse]:
    """List most recent jobs, newest first."""
    if is_postgres(db_path):
        return _pg_list_jobs(status, limit, db_path)
    return _sqlite_list_jobs(status, limit, db_path)


def claim_next_job(db_path: Path | str | None = None) -> JobResponse | None:
    """Atomically claim the oldest queued job and transition it to running."""
    if is_postgres(db_path):
        return _pg_claim_next_job(db_path)
    return _sqlite_claim_next_job(db_path)


def increment_progress(job_id: str, db_path: Path | str | None = None) -> None:
    """Increment progress_count by 1 for a job."""
    if is_postgres(db_path):
        _pg_increment_progress(job_id, db_path)
    else:
        _sqlite_increment_progress(job_id, db_path)


def finish_job(
    job_id: str,
    result: BisectResult,
    db_path: Path | str | None = None,
    culprit_subject: str | None = None,
) -> None:
    """Mark a running job as done with its serialized BisectResult."""
    if is_postgres(db_path):
        _pg_finish_job(job_id, result, db_path, culprit_subject=culprit_subject)
    else:
        _sqlite_finish_job(job_id, result, db_path)


def fail_job(
    job_id: str,
    error_message: str,
    db_path: Path | str | None = None,
) -> None:
    """Mark a job as failed with an error message."""
    if is_postgres(db_path):
        _pg_fail_job(job_id, error_message, db_path)
    else:
        _sqlite_fail_job(job_id, error_message, db_path)


def fail_stale_running_jobs(db_path: Path | str | None = None) -> int:
    """Mark any jobs left in 'running' by a crashed/dead worker as failed."""
    if is_postgres(db_path):
        return _pg_fail_stale_running_jobs(db_path)
    return _sqlite_fail_stale_running_jobs(db_path)


def check_db_health(db_path: Path | str | None = None) -> bool:
    """Verify database connectivity."""
    if is_postgres(db_path):
        return _pg_check_db_health(db_path)
    return _sqlite_check_db_health(db_path)
