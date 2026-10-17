# Model Bisect Verification Report

This document records the exact commands executed, their raw outputs (or paths to files holding them), and their verified results. Any unexecuted component is explicitly marked as not run.

---

## 1. Alternating Repository & Endpoint Verification

### Command
```bash
./scripts/make_alternating_repo.sh repos/alternating_repo
```

### Git Log
```text
f00279d (HEAD -> master) c4: metric 0.95
f11b53b c3: metric 0.80
25e4198 c2: metric 0.95
b6bb52a c1: metric 0.80
d2ef4a1 c0: metric 0.95
```

### Per-Commit Metric Evaluation via Git Worktree
Executed with `python3 "$wt_dir/eval.py"` inside a temporary detached worktree for each commit:
- `d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695` (c0): `accuracy=0.95` (good)
- `b6bb52aa0fca2f8a53c0cccb05bf282770894711` (c1): `accuracy=0.80` (bad)
- `25e4198f14fc99a9ffe9cab3745cc4163b8bb79a` (c2): `accuracy=0.95` (good)
- `f11b53b1b56e90e3010644dcd1cfb6b38ca6f71e` (c3): `accuracy=0.80` (bad)
- `f00279d540616b859043444a65bb6b6fac704a42` (c4): `accuracy=0.95` (good)

### Target Configuration (`targets.toml`)
```toml
[targets.demo_target]
repo_path = "/repos/demo_repo"
eval_command = "python eval.py"
metric_name = "accuracy"
threshold = 0.90
direction = "higher"

[targets.alternating_target]
repo_path = "/repos/alternating_repo"
eval_command = "python eval.py"
metric_name = "accuracy"
threshold = 0.90
direction = "higher"
```
Threshold `0.90` with direction `higher` classifies metrics $\ge 0.90$ as `good` (0.95) and $< 0.90$ as `bad` (0.80).

### API Job Submissions

#### Run A: Endpoints `good=c0`, `bad=c4` (First and Last Commits)
- **Request**:
  ```bash
  curl -s -X POST http://127.0.0.1:8000/jobs \
    -H "Content-Type: application/json" \
    -d '{
      "target": "alternating_target",
      "good_commit": "d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695",
      "bad_commit": "f00279d540616b859043444a65bb6b6fac704a42"
    }'
  ```
- **Response**:
  ```json
  {"id":"0acc0f31-3f1e-4415-919f-6badc1318338","target":"alternating_target","good_commit":"d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695","bad_commit":"f00279d540616b859043444a65bb6b6fac704a42","threshold_override":null,"status":"queued","created_at":"2026-10-17T19:36:55.355193+00:00","started_at":null,"finished_at":null,"progress_count":0,"result":null,"error_message":null}
  ```
- **Completed Job**:
  ```json
  {
    "id": "0acc0f31-3f1e-4415-919f-6badc1318338",
    "target": "alternating_target",
    "good_commit": "d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695",
    "bad_commit": "f00279d540616b859043444a65bb6b6fac704a42",
    "threshold_override": null,
    "status": "done",
    "created_at": "2026-10-17T19:36:55.355193+00:00",
    "started_at": "2026-10-17T19:36:55.617952+00:00",
    "finished_at": "2026-10-17T19:36:55.766759+00:00",
    "progress_count": 4,
    "result": {
      "status": "non_monotonic",
      "first_bad_commit": null,
      "evaluated_commits": [
        {"commit": "d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695", "metric": 0.95, "status": "good", "duration": 0.033775750000131666, "skip_reason": null, "eval_runs": null},
        {"commit": "25e4198f14fc99a9ffe9cab3745cc4163b8bb79a", "metric": 0.95, "status": "good", "duration": 0.01762183399978312, "skip_reason": null, "eval_runs": null},
        {"commit": "f11b53b1b56e90e3010644dcd1cfb6b38ca6f71e", "metric": 0.8, "status": "bad", "duration": 0.017174332999729813, "skip_reason": null, "eval_runs": null},
        {"commit": "f00279d540616b859043444a65bb6b6fac704a42", "metric": 0.95, "status": "good", "duration": 0.020504042000084155, "skip_reason": null, "eval_runs": null}
      ],
      "total_eval_count": 4,
      "error_message": null,
      "monotonicity_verified": false
    },
    "error_message": null
  }
  ```
  *Note on Mismatch*: Because the 5-commit history ends at `c4` with metric `0.95`, the endpoint `bad_commit=c4` evaluates to `status="good"`. Model Bisect detects the intermediate regression (`f11b53b` evaluated as bad) and returns `status="non_monotonic"`.

#### Run B: Endpoints `good=c0`, `bad=c3` (Bad Commit Endpoint)
- **Request**:
  ```bash
  curl -s -X POST http://127.0.0.1:8000/jobs \
    -H "Content-Type: application/json" \
    -d '{
      "target": "alternating_target",
      "good_commit": "d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695",
      "bad_commit": "f11b53b1b56e90e3010644dcd1cfb6b38ca6f71e"
    }'
  ```
- **Response**:
  ```json
  {"id":"f06ef8af-78c7-4dd3-a54c-d5471c46e8a5","target":"alternating_target","good_commit":"d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695","bad_commit":"f11b53b1b56e90e3010644dcd1cfb6b38ca6f71e","threshold_override":null,"status":"queued","created_at":"2026-10-17T19:37:32.329914+00:00","started_at":null,"finished_at":null,"progress_count":0,"result":null,"error_message":null}
  ```
- **Completed Job**:
  ```json
  {
    "id": "f06ef8af-78c7-4dd3-a54c-d5471c46e8a5",
    "target": "alternating_target",
    "good_commit": "d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695",
    "bad_commit": "f11b53b1b56e90e3010644dcd1cfb6b38ca6f71e",
    "threshold_override": null,
    "status": "done",
    "created_at": "2026-10-17T19:37:32.329914+00:00",
    "started_at": "2026-10-17T19:37:33.032408+00:00",
    "finished_at": "2026-10-17T19:37:33.167794+00:00",
    "progress_count": 4,
    "result": {
      "status": "non_monotonic",
      "first_bad_commit": null,
      "evaluated_commits": [
        {"commit": "d2ef4a104420cc87fa7b5d3edbc06ac46dbe5695", "metric": 0.95, "status": "good", "duration": 0.029410374999770283, "skip_reason": null, "eval_runs": null},
        {"commit": "b6bb52aa0fca2f8a53c0cccb05bf282770894711", "metric": 0.8, "status": "bad", "duration": 0.01731104200007394, "skip_reason": null, "eval_runs": null},
        {"commit": "25e4198f14fc99a9ffe9cab3745cc4163b8bb79a", "metric": 0.95, "status": "good", "duration": 0.01719462500022928, "skip_reason": null, "eval_runs": null},
        {"commit": "f11b53b1b56e90e3010644dcd1cfb6b38ca6f71e", "metric": 0.8, "status": "bad", "duration": 0.01890400000002046, "skip_reason": null, "eval_runs": null}
      ],
      "total_eval_count": 4,
      "error_message": null,
      "monotonicity_verified": false
    },
    "error_message": null
  }
  ```
  *Outcome*: With `bad_commit=c3`, `bad_commit` is classified as `bad` (metric `0.80`), and non-monotonicity is detected.

---

## 2. Test Suite Reconciliation & Clean Clone Verification

### Command
```bash
./scripts/verify_clean_clone.sh
```
Raw output saved in [`docs/verification_output.txt`](verification_output.txt).

### Output Summary
```text
==========================================
Clean Clone Verification Results:
Tests Collected: 57
Tests Passed:    57
Exit Code:       0
==========================================
Clean clone verification completed successfully.
```

### Pytest Collection by File
Command: `pytest <file> --collect-only -q`
- `tests/test_adversarial.py`: 34 tests
- `tests/test_api.py`: 8 tests
- `tests/test_engine.py`: 8 tests
- `tests/test_worker.py`: 7 tests
- **Total**: 57 tests collected

### Grep Confirmation for Named Tests
```text
$ pytest --collect-only -q | grep "test_end_to_end_alternating_repo_non_monotonic"
tests/test_worker.py::test_end_to_end_alternating_repo_non_monotonic

$ pytest --collect-only -q | grep "test_skipped_commit_before_culprit_allowed"
tests/test_engine.py::test_skipped_commit_before_culprit_allowed

$ pytest --collect-only -q | grep "test_validate_found_result_raises_on_invariant_violations"
tests/test_engine.py::test_validate_found_result_raises_on_invariant_violations

$ pytest --collect-only -q | grep "test_prune_target_worktrees_at_startup"
tests/test_worker.py::test_prune_target_worktrees_at_startup
```

---

## 3. Docker Git Security & Worktree Pruning Verification

### Scoped safe.directory Check
```bash
docker compose exec worker git -C /repos/demo_repo status
```
Output:
```text
On branch master
nothing to commit, working tree clean
```
*(Exit code 0)*

```bash
docker compose exec -u root worker bash -c "mkdir -p /tmp/unlisted_root_repo && git init /tmp/unlisted_root_repo"
docker compose exec worker git -C /tmp/unlisted_root_repo status
```
Output:
```text
fatal: detected dubious ownership in repository at '/tmp/unlisted_root_repo'
```
*(Exit code 128)*

### Worker Startup Prune Check
```bash
docker compose logs worker
```
Output:
```text
worker-1  | 2026-10-17 19:22:54,057 [INFO] model_bisect.worker: Target 'demo_target' (/repos/demo_repo) worktree prune: 0 stale worktrees removed
worker-1  | 2026-10-17 19:22:54,060 [INFO] model_bisect.worker: Target 'alternating_target' (/repos/alternating_repo) worktree prune: 0 stale worktrees removed
worker-1  | 2026-10-17 19:22:54,061 [INFO] model_bisect.worker: Model Bisect worker initialized and ready for jobs.
```

---

## 4. Commit History

### Command
```bash
git log --oneline -10
```
Output:
```text
ee66b6c Part 4: Add clean clone verification output and authoritative verification report
2f6781d Part 4: Ensure complete .gitignore coverage for venv, cache, db, and result files
8e7e5fd Part 3: Tighten Docker git safe.directory, add startup worktree pruning, and update security docs
2b591a7 Part 2: State invariant policy in bisect docstring, factor out validate_found_result, and test invariant
0f710e7 Part 1: Reproduce non-monotonic bug with alternating repo and verify API & worker behavior
d6b3640 Refine invariant check to allow skipped commits before first bad commit
54b92cf Fix evaluated_commits ordering, non-monotonic alternation check, invariant assertions, input validation tests, and Docker verification
5aba544 Implement Day 2: FastAPI service, SQLite storage, background worker, Dockerfile, compose, GitHub Action, and comprehensive tests
99b1935 Enhance verify_clean_clone.sh to select Python >= 3.10 and upgrade pip
a8660d7 Apply fixes: JSON metric contract, valid range, predecessor check limits, clean clone script, and docs
```
Note on commit history: `docs/verification_output.txt` was generated from the codebase after commits `0f710e7`, `2b591a7`, `8e7e5fd`, and `2f6781d`, and committed alongside `docs/verification.md` in commit `ee66b6c`.

---

## 5. Items Not Run

The following items were not executed:
1. **GitHub Action Workflow (`.github/workflows/model_bisect.yml`)**: **Not run**. Requires a real Pull Request execution with a valid `GITHUB_TOKEN`.
2. **GPU Hardware Evaluation**: **Not run**. All evaluations executed on CPU.
3. **Multi-Host / Kubernetes Deployments**: **Not run**. Evaluated strictly on a single macOS host via Docker Compose.
