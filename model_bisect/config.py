from __future__ import annotations

import os
import tomllib
from pathlib import Path
from model_bisect.models import Direction, TargetConfig


class TargetError(Exception):
    """Base exception for target configuration errors."""


class TargetNotFoundError(TargetError):
    """Raised when a requested target name does not exist in configuration."""


class RepoPathNotAllowedError(TargetError):
    """Raised when a target's repo path resolves outside the allowlisted root."""


def get_default_repo_root() -> Path:
    """Return the allowlisted repository root directory."""
    raw = os.environ.get("MODEL_BISECT_REPO_ROOT")
    if raw:
        return Path(raw).resolve()
    # Default to current working directory if not explicitly overridden
    return Path.cwd().resolve()


def get_default_targets_file() -> Path:
    """Return the path to the server targets configuration file."""
    raw = os.environ.get("MODEL_BISECT_TARGETS_FILE", "targets.toml")
    return Path(raw).resolve()


def load_targets(config_path: Path | str | None = None) -> dict[str, TargetConfig]:
    """Load and parse target configurations from a TOML file."""
    path = Path(config_path).resolve() if config_path else get_default_targets_file()
    if not path.is_file():
        return {}

    with path.open("rb") as f:
        data = tomllib.load(f)

    targets_dict = data.get("targets", {})
    parsed: dict[str, TargetConfig] = {}
    for name, raw_cfg in targets_dict.items():
        # Handle valid_range if formatted as a list
        vr = raw_cfg.get("valid_range")
        if vr is not None and isinstance(vr, list) and len(vr) == 2:
            raw_cfg["valid_range"] = (float(vr[0]), float(vr[1]))

        parsed[name] = TargetConfig(
            repo_path=str(raw_cfg.get("repo_path", "")),
            eval_command=str(raw_cfg.get("eval_command", "")),
            metric_name=str(raw_cfg.get("metric_name", "")),
            threshold=float(raw_cfg.get("threshold", 0.0)),
            direction=raw_cfg.get("direction", "higher"),
            valid_range=raw_cfg.get("valid_range"),
            timeout=float(raw_cfg.get("timeout", 120.0)),
            repeat_count=int(raw_cfg.get("repeat_count", 1)),
        )
    return parsed


def get_validated_target(
    target_name: str,
    config_path: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> TargetConfig:
    """Retrieve and validate a target configuration by name.

    Ensures the target exists and its repo_path resolves strictly within the allowlisted repo_root.
    """
    targets = load_targets(config_path)
    if target_name not in targets:
        raise TargetNotFoundError(f"Target '{target_name}' not found in configuration")

    target = targets[target_name]
    root = Path(repo_root).resolve() if repo_root else get_default_repo_root()
    repo_path = Path(target.repo_path)

    resolved_path: Path | None = None
    candidates: list[Path] = []
    if repo_path.is_absolute():
        candidates.append(repo_path)
        parts = repo_path.parts
        if len(parts) > 2 and parts[1] == "repos":
            candidates.append(root / Path(*parts[1:]))
            candidates.append(root / Path(*parts[2:]))
    else:
        candidates.append(root / repo_path)
        candidates.append(repo_path)

    for cand in candidates:
        cand_resolved = cand.resolve()
        try:
            cand_resolved.relative_to(root)
            if cand_resolved.exists():
                resolved_path = cand_resolved
                break
            if resolved_path is None:
                resolved_path = cand_resolved
        except ValueError:
            continue

    if resolved_path is None:
        raise RepoPathNotAllowedError(
            f"Target repository path is not inside allowlisted root directory"
        )

    return target.model_copy(update={"repo_path": str(resolved_path)})

