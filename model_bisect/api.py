from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from model_bisect.config import (
    RepoPathNotAllowedError,
    TargetNotFoundError,
    get_validated_target,
)
from model_bisect.db import (
    check_db_health,
    create_job,
    get_job,
    init_db,
    list_jobs,
)
from model_bisect.models import JobCreate, JobResponse, JobStatus

logger = logging.getLogger("model_bisect.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize database on application startup."""
    init_db()
    yield


app = FastAPI(
    title="Model Bisect API",
    description="FastAPI service for regression bisecting of ML models across git histories.",
    version="1.0.0",
    lifespan=lifespan,
)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
if not UI_DIR.exists():
    UI_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Format request validation errors cleanly without exposing internals."""
    errors = []
    for err in exc.errors():
        field = " -> ".join(str(loc) for loc in err.get("loc", []))
        msg = err.get("msg", "Invalid value")
        errors.append(f"{field}: {msg}")
    return JSONResponse(
        status_code=422,
        content={"detail": "; ".join(errors)},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Mask unexpected internal exceptions to prevent information disclosure."""
    logger.exception(f"Unhandled exception while processing {request.method} {request.url.path}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new model bisect job",
)
def submit_job(payload: JobCreate) -> JobResponse:
    """Validate target and submit a new regression bisect job."""
    try:
        get_validated_target(payload.target)
    except TargetNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except RepoPathNotAllowedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception(f"Unexpected target check error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target validation failed",
        )

    job = create_job(
        target=payload.target,
        good_commit=payload.good_commit,
        bad_commit=payload.bad_commit,
        threshold=payload.threshold,
    )
    return job


@app.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Get details and status for a specific job",
)
def fetch_job(job_id: str) -> JobResponse:
    """Retrieve details, progress, and result of a job by ID."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found",
        )
    return job


@app.get(
    "/jobs",
    response_model=list[JobResponse],
    summary="List most recent jobs",
)
def fetch_jobs(
    status: JobStatus | None = Query(default=None, description="Optional status filter"),
) -> list[JobResponse]:
    """Retrieve the 20 most recent jobs, newest first."""
    return list_jobs(status=status, limit=20)


@app.get(
    "/health",
    summary="Health check",
)
def health_check() -> dict[str, str]:
    """Verify service health and database connectivity."""
    if not check_db_health():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection unhealthy",
        )
    return {"status": "ok", "database": "connected"}
