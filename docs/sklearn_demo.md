# Scikit-Learn Model Bisect Demo Walkthrough

This document records the end-to-end reproduction and verification of Model Bisect finding a real performance regression in a scikit-learn model repository, executed via both the CLI and REST API.

All outputs in this document are cited directly from saved files:
- CLI Output: `docs/sklearn_demo_cli.json`
- API Output: `docs/sklearn_demo_job.json`
- Accuracy Table: `docs/sklearn_demo_accuracy.txt`
- Job Status Check: `docs/sklearn_demo_job_check.txt`
- Collect Counts: `docs/collect_counts.txt`

---

## Quick Run

Run the entire demo with a single command:

```bash
./scripts/run_sklearn_demo.sh
```

Expected last line of output:
```
Culprit: 6f2ce80 c6: reduce n_estimators to 2 for faster training (regression) (evaluated 6 commits)
```

---

## Part 1: Build the Demo Repository

The script `scripts/make_sklearn_repo.sh` generates a git repository with 12 commits (`c0` to `c11`) at `repos/sklearn_demo`.

### Repository Structure
- `train.py`: Trains a `RandomForestClassifier` on the `load_breast_cancer` dataset with `random_state=42`, stratified split, and writes accuracy metrics as JSON to `MODEL_BISECT_METRICS_FILE`.
- `.model-bisect.toml`:
  ```toml
  eval_command = "python train.py"
  metric_name = "accuracy"
  threshold = 0.95
  direction = "higher"
  valid_range = [0.0, 1.0]
  timeout = 60.0
  repeat_count = 1
  ```

### Evaluated Accuracies from `docs/sklearn_demo_accuracy.txt`
```
Commit   Hash                                       Accuracy   Subject
c0       040b2fd8aa77f6948705f2c371db922f6c96488f   0.9580     c0: initial model with n_estimators=200
c2       4a26b105da2d44c5aad1765f45f00c85a5916974   0.9580     c2: clarify train-test split strategy
c5       d413a168b3131e6119fe3343391c5ca6efe43677   0.9580     c5: verify hyperparameter comments before release
c6       85f2bce386aa34eb93232b9cabf0b19b5037f98a   0.8881     c6: reduce n_estimators to 2 for faster training (regression)
c8       fa031c02c316cef87d5fec77bd1e74b988c01223   0.8881     c8: document quick training mode
c11      058be493831f50b9656a73b3001cecd87c60c524   0.8881     c11: final cosmetic polish and notes
```

---

## Part 2: CLI Bisect Execution

Run Model Bisect from the CLI against `repos/sklearn_demo`:

```bash
python -m model_bisect \
  --repo repos/sklearn_demo \
  --good 040b2fd8aa77f6948705f2c371db922f6c96488f \
  --bad 058be493831f50b9656a73b3001cecd87c60c524 \
  --config repos/sklearn_demo/.model-bisect.toml \
  --out docs/sklearn_demo_cli.json
```

### Result from `docs/sklearn_demo_cli.json`
```json
{
  "status": "found",
  "first_bad_commit": "85f2bce386aa34eb93232b9cabf0b19b5037f98a",
  "evaluated_commits": [
    {
      "commit": "040b2fd8aa77f6948705f2c371db922f6c96488f",
      "metric": 0.958041958041958,
      "status": "good",
      "duration": 1.3484257919990341,
      "skip_reason": null,
      "eval_runs": null
    },
    {
      "commit": "0e4c46f4084e04cc04880a149f9b5971cd9e8153",
      "metric": 0.958041958041958,
      "status": "good",
      "duration": 1.017742875003023,
      "skip_reason": null,
      "eval_runs": null
    },
    {
      "commit": "d413a168b3131e6119fe3343391c5ca6efe43677",
      "metric": 0.958041958041958,
      "status": "good",
      "duration": 1.0609431250049965,
      "skip_reason": null,
      "eval_runs": null
    },
    {
      "commit": "85f2bce386aa34eb93232b9cabf0b19b5037f98a",
      "metric": 0.8881118881118881,
      "status": "bad",
      "duration": 1.0282615420001093,
      "skip_reason": null,
      "eval_runs": null
    },
    {
      "commit": "fa031c02c316cef87d5fec77bd1e74b988c01223",
      "metric": 0.8881118881118881,
      "status": "bad",
      "duration": 0.8918409999969299,
      "skip_reason": null,
      "eval_runs": null
    },
    {
      "commit": "058be493831f50b9656a73b3001cecd87c60c524",
      "metric": 0.8881118881118881,
      "status": "bad",
      "duration": 0.9705092090007383,
      "skip_reason": null,
      "eval_runs": null
    }
  ],
  "total_eval_count": 6,
  "error_message": null,
  "monotonicity_verified": true
}
```

---

## Part 3: REST API Bisect Execution

### Job Status Check from `docs/sklearn_demo_job_check.txt`
```
    "status": "done",
        "status": "found",
        "first_bad_commit": "85f2bce386aa34eb93232b9cabf0b19b5037f98a",
                "status": "good",
                "status": "good",
                "status": "good",
                "status": "bad",
                "status": "bad",
                "status": "bad",
```

### Result from `docs/sklearn_demo_job.json`
```json
{
  "id": "32e81cef-3060-442f-80ca-edb38eb1dc92",
  "target": "sklearn_demo_target",
  "good_commit": "040b2fd8aa77f6948705f2c371db922f6c96488f",
  "bad_commit": "058be493831f50b9656a73b3001cecd87c60c524",
  "threshold_override": null,
  "status": "done",
  "created_at": "2026-10-18T10:30:22.516565+00:00",
  "started_at": "2026-10-18T10:30:23.469666+00:00",
  "finished_at": "2026-10-18T10:30:28.239672+00:00",
  "progress_count": 6,
  "result": {
    "status": "found",
    "first_bad_commit": "85f2bce386aa34eb93232b9cabf0b19b5037f98a",
    "evaluated_commits": [
      {
        "commit": "040b2fd8aa77f6948705f2c371db922f6c96488f",
        "metric": 0.958041958041958,
        "status": "good",
        "duration": 0.9615547920002427,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "0e4c46f4084e04cc04880a149f9b5971cd9e8153",
        "metric": 0.958041958041958,
        "status": "good",
        "duration": 0.810740501001419,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "d413a168b3131e6119fe3343391c5ca6efe43677",
        "metric": 0.958041958041958,
        "status": "good",
        "duration": 0.8620139170016046,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "85f2bce386aa34eb93232b9cabf0b19b5037f98a",
        "metric": 0.8881118881118881,
        "status": "bad",
        "duration": 0.6609285829981673,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "fa031c02c316cef87d5fec77bd1e74b988c01223",
        "metric": 0.8881118881118881,
        "status": "bad",
        "duration": 0.6929567499973928,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "058be493831f50b9656a73b3001cecd87c60c524",
        "metric": 0.8881118881118881,
        "status": "bad",
        "duration": 0.679369499997847,
        "skip_reason": null,
        "eval_runs": null
      }
    ],
    "total_eval_count": 6,
    "error_message": null,
    "monotonicity_verified": true
  },
  "error_message": null
}
```

---

## Comparison: CLI vs API Results

| Field | CLI (`docs/sklearn_demo_cli.json`) | API (`docs/sklearn_demo_job.json`) |
|---|---|---|
| Status | `found` | `done` (result: `found`) |
| Culprit (`first_bad_commit`) | `85f2bce386aa34eb93232b9cabf0b19b5037f98a` | `85f2bce386aa34eb93232b9cabf0b19b5037f98a` |
| Monotonicity Verified | `true` | `true` |
| Total Evaluated Commits | `6` | `6` |
