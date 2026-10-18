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
docker compose up --build -d
docker compose ps > docs/compose_ps.txt
```
- For the container process table verifying `model_bisect-api-1` and `model_bisect-worker-1` are up and healthy, see [docs/compose_ps.txt](compose_ps.txt).

---

## 3. End-to-End API Job Verification

### Job A: Input Validation Check (Mismatched Endpoint)
- **Goal**: Confirm endpoint validation rejects `bad_commit` that evaluates as `good` without entering binary search.
- **Request**: Target `alternating_target`, `good_commit=c0` (`5e80c65`), `bad_commit=c4` (`577cf2c`).
- **Result**: Job transitioned to status `failed` with error message `Bad commit 577cf2cc9c0942b0ff85f12a402b3ca259cd13a2 evaluated as 'good' (metric=0.95), expected 'bad'`.
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

---

## 6. Items Not Run

The following components were not executed in this verification pass:
1. **GitHub Action Live PR Trigger**: Not run against a live GitHub pull request event; requires a live GitHub repository with pull request event dispatch and `GITHUB_TOKEN`.
2. **GPU Evaluation**: Not run on GPU hardware; all evaluations executed using CPU test models.
3. **Multi-Host Deployment**: Not run across a multi-host cluster; service was evaluated locally via single-host Docker Compose.
