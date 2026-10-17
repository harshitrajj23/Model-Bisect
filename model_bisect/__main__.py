from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from model_bisect.engine import bisect


def main() -> None:
    # First pass: parse --config if present so defaults can be loaded
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=str, default=None)
    pre_args, _ = pre_parser.parse_known_args()

    config_data: dict[str, object] = {}
    if pre_args.config:
        cfg_path = Path(pre_args.config).resolve()
        if cfg_path.is_file():
            with cfg_path.open("rb") as f:
                config_data = tomllib.load(f)

    parser = argparse.ArgumentParser(
        prog="model_bisect",
        description="Find the git commit that caused an ML model's eval metric to regress.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to repository TOML configuration file (e.g. .model-bisect.toml)",
    )
    parser.add_argument("--repo", type=str, default=".", help="Path to git repository")
    parser.add_argument("--good", required=True, type=str, help="Known good commit hash or ref")
    parser.add_argument("--bad", required=True, type=str, help="Known bad commit hash or ref")

    cmd_default = config_data.get("eval_command") or config_data.get("cmd")
    parser.add_argument(
        "--cmd",
        required=cmd_default is None,
        default=cmd_default,
        type=str,
        help="Shell command to evaluate model",
    )

    metric_default = config_data.get("metric_name") or config_data.get("metric")
    parser.add_argument(
        "--metric",
        required=metric_default is None,
        default=metric_default,
        type=str,
        help="Metric name to parse from JSON contract or stdout",
    )

    threshold_default = config_data.get("threshold")
    parser.add_argument(
        "--threshold",
        required=threshold_default is None,
        default=float(threshold_default) if threshold_default is not None else None,
        type=float,
        help="Metric threshold value",
    )

    dir_default = config_data.get("direction", "higher")
    parser.add_argument(
        "--direction",
        choices=["higher", "lower"],
        default=dir_default,
        help="Whether higher or lower is better (default: higher)",
    )

    repeat_default = config_data.get("repeat_count") or config_data.get("repeat", 1)
    parser.add_argument(
        "--repeat",
        type=int,
        default=int(repeat_default),
        help="Repeat eval command N times per commit and take median (default: 1)",
    )

    timeout_default = config_data.get("timeout", 120.0)
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(timeout_default),
        help="Timeout in seconds for each eval run (default: 120s)",
    )

    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Path to output JSON result file",
    )

    vr_raw = config_data.get("valid_range")
    vr_default = None
    if vr_raw and isinstance(vr_raw, list) and len(vr_raw) == 2:
        vr_default = [float(vr_raw[0]), float(vr_raw[1])]

    parser.add_argument(
        "--valid-range",
        nargs=2,
        type=float,
        metavar=("MIN", "MAX"),
        default=vr_default,
        help="Optional valid range [MIN, MAX] for the metric. Values outside cause commit to be skipped.",
    )

    args = parser.parse_args()

    valid_range = tuple(args.valid_range) if args.valid_range is not None else None

    result = bisect(
        repo_path=Path(args.repo),
        good_commit=args.good,
        bad_commit=args.bad,
        eval_command=args.cmd,
        metric_name=args.metric,
        threshold=args.threshold,
        direction=args.direction,
        repeat_count=args.repeat,
        timeout=args.timeout,
        valid_range=valid_range,
        out_path=args.out,
    )

    json_str = result.model_dump_json(indent=2)
    print(json_str)

    if result.status == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
