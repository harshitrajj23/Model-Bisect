#!/usr/bin/env bash
set -euo pipefail

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

REPO_ROOT="$(pwd)"
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "=== Verifying Clean Clone ==="
echo "Source repo: $REPO_ROOT"
echo "Current branch: $CURRENT_BRANCH"
echo "Temporary directory: $TMP_DIR"

# Clone local repo and switch to current branch
git clone "$REPO_ROOT" "$TMP_DIR/repo"
cd "$TMP_DIR/repo"
git checkout "$CURRENT_BRANCH"

# Create virtual environment and install requirements
echo "Creating clean virtual environment..."
python3 -m venv "$TMP_DIR/repo/.venv"
source "$TMP_DIR/repo/.venv/bin/activate"

echo "Installing requirements from requirements.txt..."
pip install --quiet --no-warn-script-location -r requirements.txt

echo "Running pytest -v..."
set +e
PYTEST_OUT="$(pytest -v 2>&1)"
PYTEST_STATUS=$?
set -e

echo "$PYTEST_OUT"

COLLECTED="$(echo "$PYTEST_OUT" | grep -oE 'collected [0-9]+ items' | grep -oE '[0-9]+' | head -n 1 || echo "0")"
PASSED="$(echo "$PYTEST_OUT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -n 1 || echo "0")"

echo ""
echo "=========================================="
echo "Clean Clone Verification Results:"
echo "Tests Collected: ${COLLECTED:-0}"
echo "Tests Passed:    ${PASSED:-0}"
echo "=========================================="

if [ "$PYTEST_STATUS" -ne 0 ]; then
    echo "ERROR: pytest exited with status $PYTEST_STATUS"
    exit "$PYTEST_STATUS"
fi

echo "Clean clone verification completed successfully."
