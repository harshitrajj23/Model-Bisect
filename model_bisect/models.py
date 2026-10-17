from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

CommitStatus = Literal["good", "bad", "skipped"]
BisectStatus = Literal["found", "not_found", "non_monotonic", "error"]
Direction = Literal["higher", "lower"]


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
