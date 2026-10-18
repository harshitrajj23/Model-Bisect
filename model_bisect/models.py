from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator

CommitStatus = Literal["good", "bad", "skipped"]
BisectStatus = Literal["found", "not_found", "non_monotonic", "error"]
Direction = Literal["higher", "lower"]
JobStatus = Literal["queued", "running", "done", "failed"]

COMMIT_PATTERN = re.compile(r"^(?!-)(?!.*\.\.)[a-zA-Z0-9_./~^@-]+$")


class CommitEvaluation(BaseModel):
    """Evaluation result for a single git commit."""

    commit: str = Field(description="Git commit hash")
    metric: float | None = Field(default=None, description="Parsed metric value, or None if skipped")
    status: CommitStatus = Field(description="Classification: good, bad, or skipped")
    duration: float = Field(description="Total evaluation duration in seconds")
    skip_reason: str | None = Field(default=None, description="Reason if commit was skipped")
    eval_runs: list[float] | None = Field(default=None, description="Individual metric values if repeat_count > 1")


class BisectResult(BaseModel):
    """Result of a model bisect search."""

    status: BisectStatus = Field(description="Outcome status: found, not_found, non_monotonic, or error")
    first_bad_commit: str | None = Field(default=None, description="Hash of the first bad commit if found")
    evaluated_commits: list[CommitEvaluation] = Field(
        default_factory=list, description="Chronological log of evaluated commits"
    )
    total_eval_count: int = Field(default=0, description="Total number of evaluations performed")
    error_message: str | None = Field(default=None, description="Error message if status is error")
    monotonicity_verified: bool = Field(
        default=False, description="True only when the predecessor check ran and passed"
    )


class TargetConfig(BaseModel):
    """Server-side configuration for an evaluation target."""

    repo_path: str = Field(description="Path to git repository, must be within allowlisted root")
    eval_command: str = Field(description="Shell command to evaluate candidate model")
    metric_name: str = Field(description="Metric key to extract from JSON contract or stdout fallback")
    threshold: float = Field(description="Default regression classification threshold")
    direction: Direction = Field(default="higher", description="Higher or lower is better")
    valid_range: tuple[float, float] | None = Field(
        default=None, description="Optional [min, max] allowed numerical range"
    )
    timeout: float = Field(default=120.0, description="Execution timeout per evaluation in seconds")
    repeat_count: int = Field(default=1, description="Number of evaluation repetitions (median is used)")


class JobCreate(BaseModel):
    """Job creation request payload."""

    target: str = Field(description="Configured target identifier", min_length=1, max_length=64)
    good_commit: str = Field(description="Known good commit reference", min_length=1, max_length=128)
    bad_commit: str = Field(description="Known bad/regressed commit reference", min_length=1, max_length=128)
    threshold: float | None = Field(default=None, description="Optional threshold override")

    @field_validator("good_commit", "bad_commit")
    @classmethod
    def validate_commit_ref(cls, v: str) -> str:
        s = v.strip()
        if not s or not COMMIT_PATTERN.match(s):
            raise ValueError(
                "Invalid commit reference. Must be a valid commit SHA, branch, or tag name "
                "(alphanumerics, '_', '.', '/', '~', '^', '@', '-', no leading '-' or '..')"
            )
        return s


class JobResponse(BaseModel):
    """Response model representing a bisect job.

    The response uses threshold_override because it reports the value actually applied
    to the job, and that is the documented meaning.
    """

    id: str = Field(description="Unique job UUID")
    target: str = Field(description="Target identifier")
    good_commit: str = Field(description="Good commit reference")
    bad_commit: str = Field(description="Bad commit reference")
    threshold_override: float | None = Field(default=None, description="Threshold override if specified")
    status: JobStatus = Field(description="Job status: queued, running, done, or failed")
    created_at: str = Field(description="Creation timestamp (ISO 8601 UTC)")
    started_at: str | None = Field(default=None, description="Execution start timestamp")
    finished_at: str | None = Field(default=None, description="Completion or failure timestamp")
    progress_count: int = Field(default=0, description="Number of completed evaluations")
    result: BisectResult | None = Field(default=None, description="Parsed bisect result when done")
    error_message: str | None = Field(default=None, description="Failure reason if failed")
