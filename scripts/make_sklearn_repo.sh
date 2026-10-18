#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-repos/sklearn_demo}"
rm -rf "$REPO_DIR"
mkdir -p "$REPO_DIR"

cd "$REPO_DIR"
git init -b main
git config user.name "Model Bisect Developer"
git config user.email "dev@modelbisect.local"
git config commit.gpgSign false

cat << 'EOF' > .model-bisect.toml
eval_command = "python train.py"
metric_name = "accuracy"
threshold = 0.95
direction = "higher"
valid_range = [0.0, 1.0]
timeout = 60.0
repeat_count = 1
EOF

cat << 'EOF' > README.md
# Scikit-Learn Breast Cancer Classification Model

This repository trains a RandomForestClassifier on the breast_cancer dataset.
EOF

generate_train_py() {
  local n_estimators="$1"
  local comment="${2:-}"
  cat << EOF > train.py
import json
import os
import sys
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

N_ESTIMATORS = ${n_estimators}
# ${comment}

def main():
    data = load_breast_cancer()
    X_train, X_test, y_train, y_test = train_test_split(
        data.data, data.target, random_state=42, stratify=data.target
    )
    clf = RandomForestClassifier(n_estimators=N_ESTIMATORS, random_state=42)
    clf.fit(X_train, y_train)
    acc = float(clf.score(X_test, y_test))

    metrics_file = os.environ.get("MODEL_BISECT_METRICS_FILE")
    if metrics_file:
        os.makedirs(os.path.dirname(os.path.abspath(metrics_file)), exist_ok=True)
        with open(metrics_file, "w") as f:
            json.dump({"accuracy": acc}, f)

    print(f"accuracy={acc:.4f}")

if __name__ == "__main__":
    main()
EOF
}

COMMITS=()

# Commit 0
generate_train_py 200 "Commit 0: baseline estimator count"
git add .
git commit -m "c0: initial model with n_estimators=200"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 1
generate_train_py 200 "Commit 1: add data loading comments"
git add train.py
git commit -m "c1: add data loading comments"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 2
generate_train_py 200 "Commit 2: clarify train-test split strategy"
git add train.py
git commit -m "c2: clarify train-test split strategy"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 3
echo "Commit 3: update model documentation" >> README.md
git add README.md
git commit -m "c3: update model documentation"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 4
generate_train_py 200 "Commit 4: format docstrings and annotations"
git add train.py
git commit -m "c4: format docstrings and annotations"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 5
generate_train_py 200 "Commit 5: verify hyperparameter comments before release"
git add train.py
git commit -m "c5: verify hyperparameter comments before release"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 6 (Planted regression: n_estimators changed to 2)
generate_train_py 2 "Commit 6: reduce n_estimators to 2 for faster training (regression)"
git add train.py
git commit -m "c6: reduce n_estimators to 2 for faster training (regression)"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 7
generate_train_py 2 "Commit 7: update notes after estimator adjustment"
git add train.py
git commit -m "c7: update notes after estimator adjustment"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 8
echo "Commit 8: document quick training mode" >> README.md
git add README.md
git commit -m "c8: document quick training mode"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 9
generate_train_py 2 "Commit 9: add metric output logging notes"
git add train.py
git commit -m "c9: add metric output logging notes"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 10
generate_train_py 2 "Commit 10: refine code comments"
git add train.py
git commit -m "c10: refine code comments"
COMMITS+=("$(git rev-parse HEAD)")

# Commit 11
echo "Commit 11: final cosmetic polish and notes" >> README.md
git add README.md
git commit -m "c11: final cosmetic polish and notes"
COMMITS+=("$(git rev-parse HEAD)")

echo "Scikit-learn demo repo created at: $REPO_DIR"
echo "Total commits: ${#COMMITS[@]}"
for idx in "${!COMMITS[@]}"; do
  echo "c$idx: ${COMMITS[$idx]}"
done
