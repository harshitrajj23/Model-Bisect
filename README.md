# Model Bisect

Model Bisect is a high-performance Python tool that identifies the exact Git commit that caused an ML model's evaluation metric to regress. Like `git bisect`, it performs a binary search over commit history, but classifies commits by evaluating quantitative metrics against a defined threshold.

## Features

- **Safe Worktree Execution**: Runs all evaluations in isolated temporary Git worktrees (`git worktree`), ensuring the user's working tree and HEAD state remain untouched.
- **Metric Contract (JSON First)**: Passes an output path via the `MODEL_BISECT_METRICS_FILE` environment variable. The evaluation command writes a JSON object (e.g., `{"accuracy": 0.9123}`) containing the measured metrics.
- **Robust Fallback**: Supports a minimal stdout fallback (`name=value`) when stdout logs are preferred. If multiple conflicting values appear, the commit is marked ambiguous and skipped.
- **Configurable Range Validation**: Optional `--valid-range MIN MAX` parameter to ensure parsed values fall within domain-specific boundaries (e.g., `0.0 1.0` for accuracy), skipping invalid outliers.
- **Repeated Evaluations**: Supports running evaluations multiple times per commit (`--repeat N`), using the median value to guard against stochastic eval noise.
- **Budget-Bounded Monotonicity Check**: Guarantees deterministic evaluation compute bounded by $\lceil \log_2(N) \rceil + 2$. When spare budget is available, the engine performs a predecessor check on $low - 1$ to verify history monotonicity; if the binary search utilizes its full budget, non-monotonicity is marked as unverified (`monotonicity_verified=false`) rather than silently assuming full verification.

---

## Installation

```bash
git clone https://github.com/example/Model_Bisect.git
cd Model_Bisect
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Example Evaluation Script

Eval scripts can be written in any language (Python, Bash, etc.). When invoked by Model Bisect, the environment variable `MODEL_BISECT_METRICS_FILE` specifies the file where metrics should be written as JSON.

### `eval.py`
```python
import json
import os
import sys

# Compute or load model evaluation metrics
accuracy = 0.9450
latency_ms = 42.1

# Primary Metric Contract: Write to MODEL_BISECT_METRICS_FILE
metrics_file = os.environ.get("MODEL_BISECT_METRICS_FILE")
if metrics_file:
    with open(metrics_file, "w") as f:
        json.dump({"accuracy": accuracy, "latency": latency_ms}, f)
else:
    # Fallback convenience for manual CLI execution:
    print(f"accuracy={accuracy}")
```

---

## Usage

### CLI

```bash
model-bisect \
  --repo-path /path/to/repo \
  --good-commit HEAD~10 \
  --bad-commit HEAD \
  --eval-cmd "python eval.py" \
  --metric-name accuracy \
  --threshold 0.90 \
  --direction higher \
  --valid-range 0.0 1.0 \
  --repeat 1 \
  --out results.json
```

### Python API

```python
from model_bisect import bisect

result = bisect(
    repo_path="/path/to/repo",
    good_commit="v1.0.0",
    bad_commit="v1.1.0",
    eval_command="python eval.py",
    metric_name="accuracy",
    threshold=0.90,
    direction="higher",
    valid_range=(0.0, 1.0),
    repeat_count=3,
    out_path="results.json",
)

print(f"Status: {result.status}")
print(f"First bad commit: {result.first_bad_commit}")
print(f"Monotonicity verified: {result.monotonicity_verified}")
print(f"Total evaluations: {result.total_eval_count}")
```

---

## Evaluation Budget

Model Bisect guarantees that the total number of evaluations will never exceed:

$$\text{Budget} = \lceil \log_2(N) \rceil + 2$$

Where $N$ is the number of commits from `good_commit` to `bad_commit` inclusive.

For detailed benchmarks and theoretical trade-offs, see [`docs/eval_budget.md`](docs/eval_budget.md).
