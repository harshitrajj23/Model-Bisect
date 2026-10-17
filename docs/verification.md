# Model Bisect Authoritative Verification Report

This document is the single authoritative verification report for Model Bisect prior to public release. It documents only the commands that were actually executed, their verified outputs, and explicitly records unverified components.

---

## 1. Clean Clone & Full Test Suite Verification

### Command
```bash
./scripts/verify_clean_clone.sh
```
Full console output is saved in [`docs/verification_output.txt`](verification_output.txt).

### Verification Environment
- **Source Repository**: Clean clone of current `main` branch into isolated temp directory (`mktemp -d`).
- **Python Runtime**: Python 3.12.15 (`pytest-9.1.1`, `pluggy-1.7.0`).
- **Dependencies**: Clean installation from `requirements.txt`.

### Test Results
- **Total Tests Collected**: 57
- **Total Tests Passed**: 57 (100% PASS)
- **Exit Code**: 0
- **Duration**: ~71s

### Test Suite Breakdown
1. `tests/test_adversarial.py` (34 tests):
   - Git edge cases: non-ancestor commits, identical good/bad commit, single commit, 2 commits, merge commits, short hashes, branch names, tags, detached HEAD, dirty working tree worktree safety.
   - Subprocess lifecycle: non-zero exit codes, hanging processes terminated via process group `SIGKILL` on timeout, quote parsing.
   - Metric contract: single-line stdout fallback vs JSON metric contract, duplicate values, ambiguous values, domain validation (`valid_range`), nan/inf rejection.
   - Search boundaries: inclusive threshold equality, directionality (higher/lower), all pass, all fail, regressions at first or last commit, predecessor verification compute budget limits ($\lceil \log_2(N) \rceil + 2$).
   - Repetition: repeat median aggregation, crash handling during repeated evals.
   - CLI serialization: nested output directory creation, invalid arguments, Pydantic roundtrip, byte-for-byte deterministic JSON serialization.
2. `tests/test_api.py` (8 tests):
   - Health check endpoint (`GET /health`).
   - Job submission and retrieval (`POST /jobs`, `GET /jobs/{id}`).
   - Unknown job retrieval (HTTP 404).
   - Strict commit reference validation and regex sanitization (HTTP 422).
   - Unknown target rejection (HTTP 400).
   - Target repo path traversal outside allowlisted `MODEL_BISECT_REPO_ROOT`, including relative `..` traversal (HTTP 400).
   - Job listing and status filtering (`GET /jobs?status=...`).
3. `tests/test_engine.py` (8 tests):
   - Monotonic 20-commit regression search (culprit at commit 12).
   - Non-monotonic detection.
   - Skipped commit outward candidate search.
   - Repeat count median aggregation.
   - CLI execution and result writing.
   - `on_progress` callback event emission.
   - Skipped commit before culprit allowed (`test_skipped_commit_before_culprit_allowed`).
   - Invariant validation assertions (`test_validate_found_result_raises_on_invariant_violations`).
4. `tests/test_worker.py` (7 tests):
   - Stale running job recovery on worker startup.
   - Atomic job claiming under concurrent threads.
   - End-to-end planted regression execution and progress tracking.
   - Engine exception handling marking job as failed.
   - End-to-end API submission and worker execution.
   - End-to-end alternating non-monotonic history resolution (`test_end_to_end_alternating_repo_non_monotonic`).
   - Startup git worktree pruning on target repositories (`test_prune_target_worktrees_at_startup`).

---

## 2. Alternating Non-Monotonic History Verification (Docker Stack)

### Commands
```bash
# 1. Build alternating 5-commit repository (0.95, 0.80, 0.95, 0.80, 0.95)
./scripts/make_alternating_repo.sh repos/alternating_repo

# 2. Submit job via Docker API
curl -s -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "target": "alternating_target",
    "good_commit": "43d6a6d92c6051f6c43a45a2b024a595cb8047e5",
    "bad_commit": "d04739c4c0fd58a2b48639a843e045b8a4c512bb"
  }'

# 3. Retrieve completed job
curl -s http://127.0.0.1:8000/jobs/f0eecad2-5cc7-4532-bb6f-3c97143220aa
```

### Raw JSON Response
```json
{
  "id": "f0eecad2-5cc7-4532-bb6f-3c97143220aa",
  "target": "alternating_target",
  "good_commit": "43d6a6d92c6051f6c43a45a2b024a595cb8047e5",
  "bad_commit": "d04739c4c0fd58a2b48639a843e045b8a4c512bb",
  "threshold_override": null,
  "status": "done",
  "created_at": "2026-10-17T19:09:07.342554+00:00",
  "started_at": "2026-10-17T19:09:08.059874+00:00",
  "finished_at": "2026-10-17T19:09:08.200767+00:00",
  "progress_count": 4,
  "result": {
    "status": "non_monotonic",
    "first_bad_commit": null,
    "evaluated_commits": [
      {
        "commit": "43d6a6d92c6051f6c43a45a2b024a595cb8047e5",
        "metric": 0.95,
        "status": "good",
        "duration": 0.0303132919999598,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "bb80ba78be9d1584ab3e95c51130e822e6e9ef06",
        "metric": 0.95,
        "status": "good",
        "duration": 0.017825708000032137,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "07a066ae7d925e347bfe0fbe6961b52f9f005e8f",
        "metric": 0.8,
        "status": "bad",
        "duration": 0.018108332999986487,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "d04739c4c0fd58a2b48639a843e045b8a4c512bb",
        "metric": 0.95,
        "status": "good",
        "duration": 0.01959645800002363,
        "skip_reason": null,
        "eval_runs": null
      }
    ],
    "total_eval_count": 4,
    "error_message": null,
    "monotonicity_verified": false
  },
  "error_message": null
}
```
**Outcome**: Verified `status: "non_monotonic"`, `monotonicity_verified: false`, and `first_bad_commit: null`.

---

## 3. Docker Git Security & Scoped safe.directory Verification

### Commands
```bash
# Verify safe.directory works for listed demo_repo
docker compose exec worker git -C /repos/demo_repo status

# Verify safe.directory rejects unlisted repo path with dubious ownership
docker compose exec -u root worker bash -c "mkdir -p /tmp/unlisted_root_repo && git init /tmp/unlisted_root_repo"
docker compose exec worker git -C /tmp/unlisted_root_repo status
```

### Outputs
- `git -C /repos/demo_repo status`:
  ```text
  On branch master
  nothing to commit, working tree clean
  ```
  *(Exit code 0)*
- `git -C /tmp/unlisted_root_repo status`:
  ```text
  fatal: detected dubious ownership in repository at '/tmp/unlisted_root_repo'
  To add an exception for this directory, call:

  	git config --global --add safe.directory /tmp/unlisted_root_repo
  ```
  *(Exit code 128)*

---

## 4. Worker Startup Worktree Pruning Verification

### Command
```bash
docker compose logs worker
```

### Output
```text
worker-1  | 2026-10-17 19:22:54,057 [INFO] model_bisect.worker: Target 'demo_target' (/repos/demo_repo) worktree prune: 0 stale worktrees removed
worker-1  | 2026-10-17 19:22:54,060 [INFO] model_bisect.worker: Target 'alternating_target' (/repos/alternating_repo) worktree prune: 0 stale worktrees removed
worker-1  | 2026-10-17 19:22:54,061 [INFO] model_bisect.worker: Model Bisect worker initialized and ready for jobs.
```
**Outcome**: Verified that worker executes `git worktree prune -v` across all targets at startup and logs results.

---

## 5. Items Not Run / Unverified

The following components were not executed in this local environment and are explicitly marked:
1. **GitHub Action Workflow (`.github/workflows/model_bisect.yml`)**:
   - **Status**: **Unverified / Not Run**
   - **Reason**: Requires execution on GitHub Action runners triggered by an actual Pull Request with a valid `GITHUB_TOKEN`.
2. **Kubernetes / Multi-Node Worker Cluster**:
   - **Status**: **Not Run**
   - **Reason**: Out of scope for v1 single-host Docker stack.
3. **GPU Hardware Acceleration**:
   - **Status**: **Not Run**
   - **Reason**: Local test environment ran on CPU.
