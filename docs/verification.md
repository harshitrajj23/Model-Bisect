# Model Bisect Verification Report

This document records the exact commands executed, their results, and links to the artifact files in `docs/` containing the raw output. In accordance with verification integrity requirements, no raw JSON payloads are pasted directly into this document; all claims cite and link to their supporting raw output files.

---

## 1. Alternating Repository & Endpoint Verification

### Generation Command
```bash
./scripts/make_alternating_repo.sh repos/alternating_repo
```

### Git Log
- For the raw `git log --oneline` output of the rebuilt alternating repository (commits `c0` through `c4`), see [docs/alt_repo_log.txt](alt_repo_log.txt).

### Per-Commit Metric Evaluation via Git Worktree
- For the raw evaluation outputs of each commit (`python3 eval.py` inside isolated detached git worktrees), see [docs/alt_repo_metrics.txt](alt_repo_metrics.txt).
- Verified metric classifications under threshold `0.90` (direction `higher`):
  - `c0` (`5e80c65`): `accuracy=0.95` (`good`)
  - `c1` (`2b8a175`): `accuracy=0.80` (`bad`)
  - `c2` (`2fcd2c8`): `accuracy=0.95` (`good`)
  - `c3` (`3db46a7`): `accuracy=0.80` (`bad`)
  - `c4` (`577cf2c`): `accuracy=0.95` (`good`)

---

## 2. Docker Stack Verification

### Build and Launch Command
```bash
docker compose down
docker compose up --build -d
docker compose ps > docs/compose_ps.txt
```
- For the container process table verifying `model_bisect-api-1` and `model_bisect-worker-1` are up and healthy, see [docs/compose_ps.txt](compose_ps.txt).

### API Health Check
```bash
curl -s http://127.0.0.1:8000/health > docs/health.json
```
- For the API health response confirming database connectivity, see [docs/health.json](health.json).

---

## 3. End-to-End API Job Verification

### Job A: Input Validation Check (Mismatched Endpoint)
- **Goal**: Confirm endpoint validation rejects `bad_commit` that evaluates as `good` without entering binary search.
- **Request**: Target `alternating_target`, `good_commit=c0` (`5e80c65`), `bad_commit=c4` (`577cf2c`).
- **Result**: Job transitioned to status `failed` with error message `Bad commit 577cf2cc9c0942b0ff85f12a402b3ca259cd13a2 evaluated as 'good' (metric=0.95), expected 'bad'`.
- **Evaluation Sequence & Progress Count Line References** (cites [docs/source_refs.txt](source_refs.txt)):
  - Endpoint evaluations: `commits[0]` is evaluated at `model_bisect/engine.py:491`, and `commits[-1]` is evaluated at `model_bisect/engine.py:492`. Note that `model_bisect/engine.py:478` (`single_eval = evaluate(commits[0])`) appears in the grep output but belongs to the single-commit branch (`len(commits) == 1`), which is not taken for Job A (`commits` has 5 items).
  - Progress callback: When `on_progress` is configured (`model_bisect/worker.py:115`), `model_bisect/engine.py:455` checks `if on_progress is not None:` and invokes `on_progress(eval_res)` at `model_bisect/engine.py:457`. Note that grep output does not show the call order inside `evaluate()`. The callback `on_progress_callback` (`model_bisect/worker.py:94`) calls `increment_progress` at `model_bisect/worker.py:96`. The earlier reference to `db.py:209` was wrong because line 209 did not appear in `docs/source_refs.txt`; only `model_bisect/db.py:203` (`def increment_progress`) was captured in the grep output, and the SQL UPDATE statement itself was never printed.
  - Error exit: The endpoint check triggers the error message matching `expected 'bad'` at `model_bisect/engine.py:555`. In `model_bisect/worker.py`, line 117 (`if result.status == "error":`) leads to `fail_job` at line 118.
  - The final `progress_count` of 2 in [docs/job_a.json](job_a.json) equals the two endpoint evaluations performed before failure.
- For the full raw response, see [docs/job_a.json](job_a.json).

### Job B: Alternating Monotonicity Check
- **Goal**: Confirm binary search on alternating history `c0..c3` evaluates both endpoints and interior commits, detecting non-monotonicity.
- **Request**: Target `alternating_target`, `good_commit=c0` (`5e80c65`), `bad_commit=c3` (`3db46a7`).
- **Result**: Job transitioned to status `done` with `result.status="non_monotonic"`, `first_bad_commit=null`, and `monotonicity_verified=false`.
- **Commit Position Ordering**: Evaluated commits in `result.evaluated_commits` follow strict ascending commit list index order: `c0` (pos 0), `c1` (pos 1), `c2` (pos 2), and `c3` (pos 3).
- For the full raw response, see [docs/job_b.json](job_b.json).

---

## 4. Invariant Policy Change (d6b3640)

The post-bisect check changed from "every earlier commit must be good" to "no earlier commit may be bad". Skipped commits are permitted before the culprit; this is a policy decision, not a bug fix. When status is `found`, the culprit is evaluated as `bad`, and no commit evaluated earlier in search order may be `bad`. Commits marked `good` or `skipped` before the culprit are allowed.

---

## 5. Clean Clone Verification & Test Suite

### Clean Clone Script
```bash
./scripts/verify_clean_clone.sh > docs/verification_output.txt 2>&1
```
- A fresh temporary clone was created, requirements installed into a fresh virtual environment, and the entire test suite executed.
- Verified test results: 62 tests collected, 62 passed, exit code 0.
- For the complete raw terminal log, see [docs/verification_output.txt](verification_output.txt).
- For the summary line from line 90 of `verification_output.txt`, see [docs/test_summary_line.txt](test_summary_line.txt).
- For the clean clone exit block, see [docs/tail_verification.txt](tail_verification.txt).

### Pytest Collection
```bash
pytest --collect-only -q > docs/collect_output.txt
```
- Total tests collected: 62
- Per-file test counts:
  - `tests/test_adversarial.py`: 34 tests
  - `tests/test_engine.py`: 12 tests
  - `tests/test_api.py`: 8 tests
  - `tests/test_worker.py`: 8 tests
- For the full list of collected tests, see [docs/collect_output.txt](collect_output.txt).
- For the summary line from `collect_output.txt`, see [docs/tail_collect.txt](tail_collect.txt).

---

## 6. GitHub Action Verification & Status

### GitHub Action Status
Wiring verified on PR #1 (test/action-smoke) using a synthetic eval. Run 39114453801 concluded success with status `found`. Two earlier runs on the same PR failed because the base endpoint had no eval script; the PR branch was fixed and the base was not changed. Real model evaluation in CI: not run.

- For the full action run log, see [docs/action_run_log.txt](action_run_log.txt).
- For the live PR view and comments, see [docs/action_pr_live.txt](action_pr_live.txt).
- For the run verification summary confirming no failed steps, see [docs/action_run_status.txt](action_run_status.txt).
- *Note on PR comments*: [docs/action_pr_comment.txt](action_pr_comment.txt) contains the two error comments from earlier runs followed by the successful one, so readers are not misled.
- For the real scikit-learn model Action run log and PR comments on PR #2 (Run 39152434251, which concluded failure with status error because the base commit lacked model/train.py), see [docs/action_sklearn_run_log.txt](action_sklearn_run_log.txt) and [docs/action_sklearn_pr.txt](action_sklearn_pr.txt).
- For the real scikit-learn model Action rerun log on PR #4 (Run 39156596574), see [docs/action_sklearn_run_log_v2.txt](action_sklearn_run_log_v2.txt).

### Security note
The eval command is read from `.model-bisect.toml` on the PR head, so a PR author can change what runs on the CI runner. Acceptable for a private repo with trusted collaborators. Before any public release, read the config from the base branch.

---

## 7. Items Not Run

The following components were not executed in this verification pass:
1. **Real model evaluation in CI**: Not run; workflow wiring was verified using a synthetic evaluation script.
2. **GPU Evaluation**: Not run on GPU hardware; all evaluations executed using CPU test models.
3. **Multi-Host Deployment**: Not run across a multi-host cluster; service was evaluated locally via single-host Docker Compose.

