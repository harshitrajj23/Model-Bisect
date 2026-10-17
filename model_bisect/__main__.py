from __future__ import annotations

import argparse
import sys
from pathlib import Path

from model_bisect.engine import bisect


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="model_bisect",
        description="Find the git commit that caused an ML model's eval metric to regress.",
    )
    parser.add_argument("--repo", type=str, default=".", help="Path to git repository")
    parser.add_argument("--good", required=True, type=str, help="Known good commit hash or ref")
    parser.add_argument("--bad", required=True, type=str, help="Known bad commit hash or ref")
    parser.add_argument("--cmd", required=True, type=str, help="Shell command to evaluate model")
    parser.add_argument("--metric", required=True, type=str, help="Metric name to parse from stdout")
    parser.add_argument("--threshold", required=True, type=float, help="Metric threshold value")
    parser.add_argument(
        "--direction",
        choices=["higher", "lower"],
        default="higher",
        help="Whether higher or lower is better (default: higher)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat eval command N times per commit and take median (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds for each eval run (default: 120s)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Path to output JSON result file",
    )

    args = parser.parse_args()

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
        out_path=args.out,
    )

    json_str = result.model_dump_json(indent=2)
    print(json_str)

    if result.status == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
