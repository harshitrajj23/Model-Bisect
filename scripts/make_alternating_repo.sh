#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-alternating_repo}"
rm -rf "$REPO_DIR"
mkdir -p "$REPO_DIR"

cd "$REPO_DIR"
git init
git config user.name "Alternating Tester"
git config user.email "alternating@tester.com"
git config commit.gpgSign false

METRICS=("0.95" "0.80" "0.95" "0.80" "0.95")
COMMITS=()

for i in 0 1 2 3 4; do
  metric="${METRICS[$i]}"
  echo "print('accuracy=$metric')" > eval.py
  echo "$i" > v.txt
  git add .
  git commit -m "c$i: metric $metric"
  sha=$(git rev-parse HEAD)
  COMMITS+=("$sha")
done

echo "Alternating repo created at: $REPO_DIR"
echo "Commits created (c0 to c4):"
for idx in "${!COMMITS[@]}"; do
  echo "c$idx: ${COMMITS[$idx]}"
done

echo ""
echo "=== git log --graph --oneline --all ==="
git log --graph --oneline --all

echo ""
echo "=== git rev-list --topo-order --reverse ${COMMITS[0]}..${COMMITS[4]} ==="
git rev-list --topo-order --reverse "${COMMITS[0]}..${COMMITS[4]}"
