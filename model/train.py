import json
import os
import sys
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

N_ESTIMATORS = 200
# Baseline model with n_estimators=200

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
