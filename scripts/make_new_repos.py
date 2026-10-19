#!/usr/bin/env python3
"""Build three distinct ML benchmark repositories for Model Bisect."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS_DIR = ROOT / "repos"


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def commit_file(repo: Path, filename: str, content: str, message: str) -> None:
    file_path = repo / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        f.write(content)
    run_git(repo, "add", filename)
    run_git(repo, "commit", "-m", message)


def build_digits_repo() -> None:
    repo = REPOS_DIR / "digits_classifier"
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True, exist_ok=True)

    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "ModelBisect Demo")
    run_git(repo, "config", "user.email", "demo@modelbisect.local")

    def code(norm: bool, max_iter: int) -> str:
        return f'''import json
import os
from sklearn.datasets import load_digits
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

NORMALIZE = {norm}
MAX_ITER = {max_iter}

def eval():
    digits = load_digits()
    X = digits.data / 16.0 if NORMALIZE else digits.data
    y = digits.target
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=42)
    
    clf = LogisticRegression(max_iter=MAX_ITER, random_state=42)
    clf.fit(X_train, y_train)
    acc = float(clf.score(X_test, y_test))

    out_file = os.environ.get("MODEL_BISECT_METRICS_FILE")
    if out_file:
        os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
        with open(out_file, "w") as f:
            json.dump({{"accuracy": acc}}, f)
    print(f"accuracy={{acc:.4f}}")

if __name__ == "__main__":
    eval()
'''

    commit_file(repo, "train.py", code(True, 1000), "c0: Initial digits model with normalized inputs (acc ~0.96)")
    commit_file(repo, "README.md", "# Digits Classifier\nHandwritten digits recognition benchmark.", "c1: Add readme documentation")
    commit_file(repo, "train.py", code(True, 1000) + "# Comment: verified test split\n", "c2: Verify test partition split")
    commit_file(repo, "train.py", code(True, 1000) + "# Comment: updated solver params\n", "c3: Refactor comments")
    commit_file(repo, "train.py", code(True, 1000) + "# Comment: prepare release candidate\n", "c4: Prepare release candidate")
    # Planted regression at c5: disable normalization and restrict iterations (acc drops to ~0.69)
    commit_file(repo, "train.py", code(False, 5), "c5: Speed up training by removing normalization and iterations (regression)")
    commit_file(repo, "train.py", code(False, 5) + "# Doc polish\n", "c6: Update documentation notes")
    commit_file(repo, "train.py", code(False, 5) + "# Release polish\n", "c7: Minor cosmetic cleanups")
    commit_file(repo, "train.py", code(False, 5) + "# Final commit\n", "c8: Final polish for v1.1")


def build_latency_repo() -> None:
    repo = REPOS_DIR / "latency_benchmark"
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True, exist_ok=True)

    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "ModelBisect Demo")
    run_git(repo, "config", "user.email", "demo@modelbisect.local")

    def code(slow_loop: bool) -> str:
        return f'''import json
import os
import time
import numpy as np

SLOW_MODE = {slow_loop}

def run_benchmark():
    np.random.seed(42)
    data = np.random.randn(2000, 128)
    weights = np.random.randn(128, 64)

    start = time.perf_counter()
    if SLOW_MODE:
        # Unvectorized slow row iteration simulating unoptimized inference
        time.sleep(0.065)
        res = [np.dot(row, weights) for row in data]
    else:
        # Fast vectorized matrix dot product
        time.sleep(0.008)
        res = np.dot(data, weights)
    
    elapsed_ms = float((time.perf_counter() - start) * 1000.0)

    out_file = os.environ.get("MODEL_BISECT_METRICS_FILE")
    if out_file:
        os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
        with open(out_file, "w") as f:
            json.dump({{"latency_ms": elapsed_ms}}, f)
    print(f"latency_ms={{elapsed_ms:.2f}}")

if __name__ == "__main__":
    run_benchmark()
'''

    commit_file(repo, "benchmark.py", code(False), "c0: Fast vectorized inference pipeline (~10ms)")
    commit_file(repo, "README.md", "# Latency Benchmark\nMeasures throughput & inference latency.", "c1: Add benchmark docs")
    commit_file(repo, "benchmark.py", code(False) + "# Comment: warmup pass\n", "c2: Add warmup pass notes")
    commit_file(repo, "benchmark.py", code(False) + "# Comment: batch tuning\n", "c3: Tuning batch sizing comments")
    # Planted regression at c4: unvectorized loop jumps latency to ~75ms
    commit_file(repo, "benchmark.py", code(True), "c4: Refactor inference loop to process per-sample (latency regression)")
    commit_file(repo, "benchmark.py", code(True) + "# Comment: per-sample checks\n", "c5: Add per-sample debug logging")
    commit_file(repo, "benchmark.py", code(True) + "# Comment: final cleanup\n", "c6: Final pipeline polish")


def build_housing_repo() -> None:
    repo = REPOS_DIR / "housing_regressor"
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True, exist_ok=True)

    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "ModelBisect Demo")
    run_git(repo, "config", "user.email", "demo@modelbisect.local")

    def code(bad_alpha: bool) -> str:
        alpha = "10000.0" if bad_alpha else "0.1"
        return f'''import json
import os
from sklearn.datasets import load_diabetes
from sklearn.linear_model import Ridge
from sklearn.model_selection import train_test_split

ALPHA = {alpha}

def eval():
    data = load_diabetes()
    X_train, X_test, y_train, y_test = train_test_split(data.data, data.target, random_state=42)
    reg = Ridge(alpha=ALPHA, random_state=42)
    reg.fit(X_train, y_train)
    r2 = float(reg.score(X_test, y_test))

    out_file = os.environ.get("MODEL_BISECT_METRICS_FILE")
    if out_file:
        os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
        with open(out_file, "w") as f:
            json.dump({{"r2_score": r2}}, f)
    print(f"r2_score={{r2:.4f}}")

if __name__ == "__main__":
    eval()
'''

    commit_file(repo, "train.py", code(False), "c0: Ridge regression model with tuned alpha=0.1 (r2 ~0.55)")
    commit_file(repo, "README.md", "# Tabular Regressor\nPredicts target response using regularized regression.", "c1: Add readme")
    commit_file(repo, "train.py", code(False) + "# Comment: feature notes\n", "c2: Add feature preprocessing notes")
    # Planted regression at c3: extreme over-regularization alpha=10000.0 destroys model (r2 drops to ~0.01)
    commit_file(repo, "train.py", code(True), "c3: Over-regularize alpha to 10000 to prevent variance (regression)")
    commit_file(repo, "train.py", code(True) + "# Comment: doc updates\n", "c4: Document regularization change")
    commit_file(repo, "train.py", code(True) + "# Final touches\n", "c5: Final model polish")


if __name__ == "__main__":
    print("Building digits_classifier...")
    build_digits_repo()
    print("Building latency_benchmark...")
    build_latency_repo()
    print("Building housing_regressor...")
    build_housing_repo()
    print("Done!")
