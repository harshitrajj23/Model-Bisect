#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Locate suitable Python binary (prefer python if available, fallback to python3)
PYTHON_BIN="${PYTHON:-$(command -v python || command -v python3)}"

# Output directory: user-provided argument or fresh mktemp directory
OUTPUT_DIR="${1:-$(mktemp -d "${TMPDIR:-/tmp}/sklearn_demo_XXXXXX")}"

# Build the demo repository
echo "Building demo repository at: $OUTPUT_DIR"
"$SCRIPT_DIR/make_sklearn_repo.sh" "$OUTPUT_DIR"

# Find good and bad commits by subject line
GOOD_COMMIT="$(git -C "$OUTPUT_DIR" log --all --grep="initial model" --format="%H" -n 1)"
BAD_COMMIT="$(git -C "$OUTPUT_DIR" log --all --grep="final cosmetic" --format="%H" -n 1)"

if [[ -z "$GOOD_COMMIT" || -z "$BAD_COMMIT" ]]; then
  echo "Error: Could not locate good or bad commit by subject line." >&2
  exit 1
fi

echo "Good commit (c0): $GOOD_COMMIT"
echo "Bad commit (c11): $BAD_COMMIT"

# Run Model Bisect via CLI
RESULT_FILE="$OUTPUT_DIR/bisect_result.json"
echo "Running Model Bisect..."
PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m model_bisect \
  --repo "$OUTPUT_DIR" \
  --good "$GOOD_COMMIT" \
  --bad "$BAD_COMMIT" \
  --config "$OUTPUT_DIR/.model-bisect.toml" \
  --out "$RESULT_FILE"

# Parse JSON result and verify status and culprit
"$PYTHON_BIN" -c '
import json
import subprocess
import sys

result_file = sys.argv[1]
repo_dir = sys.argv[2]

with open(result_file, "r") as f:
    data = json.load(f)

status = data.get("status")
if status != "found":
    print(f"Error: bisect status is not \"found\" (got \"{status}\")", file=sys.stderr)
    sys.exit(1)

culprit_hash = data.get("first_bad_commit")
if not culprit_hash:
    print("Error: first_bad_commit is missing", file=sys.stderr)
    sys.exit(1)

eval_count = data.get("total_eval_count", len(data.get("evaluated_commits", [])))
short_hash = subprocess.check_output(
    ["git", "-C", repo_dir, "rev-parse", "--short", culprit_hash], text=True
).strip()
subject = subprocess.check_output(
    ["git", "-C", repo_dir, "log", "-1", "--format=%s", culprit_hash], text=True
).strip()

if "regression" not in subject.lower():
    print(f"Error: culprit subject \"{subject}\" does not contain \"regression\"", file=sys.stderr)
    sys.exit(1)

print(f"Culprit: {short_hash} {subject} (evaluated {eval_count} commits)")
' "$RESULT_FILE" "$OUTPUT_DIR"
