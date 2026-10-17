#!/usr/bin/env bash
set -euo pipefail

DEMO_DIR="${1:-demo_repo}"
rm -rf "$DEMO_DIR"
mkdir -p "$DEMO_DIR"

cd "$DEMO_DIR"
git init
git config user.name "Demo Tester"
git config user.email "demo@tester.com"
git config commit.gpgSign false

COMMITS=()
for i in 0 1 2 3 4; do
  if [ "$i" -ge 3 ]; then
    metric="0.80"
  else
    metric="0.95"
  fi
  echo "print('accuracy=$metric')" > eval.py
  echo "$i" > v.txt
  git add .
  git commit -m "c$i: metric $metric"
  sha=$(git rev-parse HEAD)
  COMMITS+=("$sha")
done

echo "Demo repo created at: $DEMO_DIR"
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
