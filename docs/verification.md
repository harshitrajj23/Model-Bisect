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
e6d78db (HEAD -> master) c4: metric 0.95
9d12ce9 c3: metric 0.80
4ff906e c2: metric 0.95
962800e c1: metric 0.80
7f50dd7 c0: metric 0.95
```

### Per-Commit Metric Evaluation via Git Worktree
Executed with `python3 eval.py` inside temporary detached git worktrees:
- `7f50dd73b2aaeb7a929c60b1b54ce1126a548697` (c0): `accuracy=0.95` (good)
- `962800e41f828982eaece7e4bfd3360796b4f150` (c1): `accuracy=0.80` (bad)
- `4ff906e02d439bf8b0b9901b24c0c638dce32ce6` (c2): `accuracy=0.95` (good)
- `9d12ce914148a37447e21eb35ce11a63d2f4895a` (c3): `accuracy=0.80` (bad)
- `e6d78db7bf72db11ba24ae11939e02d871e14b75` (c4): `accuracy=0.95` (good)

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

### Docker Stack Build & Process Status
```bash
docker compose up --build -d
docker compose ps
```
Output:
```text
NAME                    IMAGE                 COMMAND                  SERVICE   CREATED          STATUS          PORTS
model_bisect-api-1      model_bisect-api      "uvicorn model_bisec…"   api       13 seconds ago   Up 12 seconds   127.0.0.1:8000->8000/tcp
model_bisect-worker-1   model_bisect-worker   "python -m model_bis…"   worker    13 seconds ago   Up 11 seconds   8000/tcp
```

### API Job Submissions

#### Job A: Input-Validation Check (`good=c0`, `bad=c4`)
- **Description**: Input-validation check. Submitting `c4` (which evaluates to 0.95, meeting the $\ge 0.90$ threshold) as `bad_commit` fails endpoint validation before binary search runs. The engine returns `status="error"`, and the background worker records job status as `failed` with the endpoint mismatch message.
- **Request**:
  ```bash
  curl -s -i -X POST http://127.0.0.1:8000/jobs \
    -H "Content-Type: application/json" \
    -d '{
      "target": "alternating_target",
      "good_commit": "7f50dd73b2aaeb7a929c60b1b54ce1126a548697",
      "bad_commit": "e6d78db7bf72db11ba24ae11939e02d871e14b75"
    }'
  ```
- **Response (HTTP 202 Accepted)**:
  ```text
  HTTP/1.1 202 Accepted
  date: Sun, 18 Oct 2026 07:05:41 GMT
  server: uvicorn
  content-length: 371
  content-type: application/json

  {"id":"9bd1b451-e265-4ea4-81c0-12df9733215c","target":"alternating_target","good_commit":"7f50dd73b2aaeb7a929c60b1b54ce1126a548697","bad_commit":"e6d78db7bf72db11ba24ae11939e02d871e14b75","threshold_override":null,"status":"queued","created_at":"2026-10-18T07:05:41.726164+00:00","started_at":null,"finished_at":null,"progress_count":0,"result":null,"error_message":null}
  ```
- **Fetch Job Detail**:
  ```bash
  curl -s -i http://127.0.0.1:8000/jobs/9bd1b451-e265-4ea4-81c0-12df9733215c
  ```
- **Response (HTTP 200 OK)**:
  ```text
  HTTP/1.1 200 OK
  date: Sun, 18 Oct 2026 07:05:51 GMT
  server: uvicorn
  content-length: 530
  content-type: application/json

  {"id":"9bd1b451-e265-4ea4-81c0-12df9733215c","target":"alternating_target","good_commit":"7f50dd73b2aaeb7a929c60b1b54ce1126a548697","bad_commit":"e6d78db7bf72db11ba24ae11939e02d871e14b75","threshold_override":null,"status":"failed","created_at":"2026-10-18T07:05:41.726164+00:00","started_at":"2026-10-18T07:05:42.360453+00:00","finished_at":"2026-10-18T07:05:42.450126+00:00","progress_count":2,"result":null,"error_message":"Bad commit e6d78db7bf72db11ba24ae11939e02d871e14b75 evaluated as 'good' (metric=0.95), expected 'bad'"}
  ```
- **Result**: Job status `failed`. Error message explicitly reports bad commit `e6d78db7bf72db11ba24ae11939e02d871e14b75` evaluated as `'good'` (metric=0.95) when expected `'bad'`.

---

#### Job B: Alternating History with Valid Endpoints (`good=c0`, `bad=c3`)
- **Description**: Valid endpoints where `good=c0` evaluates as `good` (0.95) and `bad=c3` evaluates as `bad` (0.80). Evaluates intermediate history and detects non-monotonic regression.
- **Request**:
  ```bash
  curl -s -i -X POST http://127.0.0.1:8000/jobs \
    -H "Content-Type: application/json" \
    -d '{
      "target": "alternating_target",
      "good_commit": "7f50dd73b2aaeb7a929c60b1b54ce1126a548697",
      "bad_commit": "9d12ce914148a37447e21eb35ce11a63d2f4895a"
    }'
  ```
- **Response (HTTP 202 Accepted)**:
  ```text
  HTTP/1.1 202 Accepted
  date: Sun, 18 Oct 2026 07:06:02 GMT
  server: uvicorn
  content-length: 371
  content-type: application/json

  {"id":"32754cd6-8cc8-4e7b-a032-aee47a5c0676","target":"alternating_target","good_commit":"7f50dd73b2aaeb7a929c60b1b54ce1126a548697","bad_commit":"9d12ce914148a37447e21eb35ce11a63d2f4895a","threshold_override":null,"status":"queued","created_at":"2026-10-18T07:06:02.669501+00:00","started_at":null,"finished_at":null,"progress_count":0,"result":null,"error_message":null}
  ```
- **Fetch Job Detail**:
  ```bash
  curl -s -i http://127.0.0.1:8000/jobs/32754cd6-8cc8-4e7b-a032-aee47a5c0676
  ```
- **Response (HTTP 200 OK)**:
  ```text
  HTTP/1.1 200 OK
  date: Sun, 18 Oct 2026 07:06:15 GMT
  server: uvicorn
  content-length: 1172
  content-type: application/json

  {"id":"32754cd6-8cc8-4e7b-a032-aee47a5c0676","target":"alternating_target","good_commit":"7f50dd73b2aaeb7a929c60b1b54ce1126a548697","bad_commit":"9d12ce914148a37447e21eb35ce11a63d2f4895a","threshold_override":null,"status":"done","created_at":"2026-10-18T07:06:02.669501+00:00","started_at":"2026-10-18T07:06:03.587902+00:00","finished_at":"2026-10-18T07:06:03.721995+00:00","progress_count":4,"result":{"status":"non_monotonic","first_bad_commit":null,"evaluated_commits":[{"commit":"7f50dd73b2aaeb7a929c60b1b54ce1126a548697","metric":0.95,"status":"good","duration":0.028009042000121553,"skip_reason":null,"eval_runs":null},{"commit":"962800e41f828982eaece7e4bfd3360796b4f150","metric":0.8,"status":"bad","duration":0.017727874999764026,"skip_reason":null,"eval_runs":null},{"commit":"4ff906e02d439bf8b0b9901b24c0c638dce32ce6","metric":0.95,"status":"good","duration":0.01698699999906239,"skip_reason":null,"eval_runs":null},{"commit":"9d12ce914148a37447e21eb35ce11a63d2f4895a","metric":0.8,"status":"bad","duration":0.020070707998456783,"skip_reason":null,"eval_runs":null}],"total_eval_count":4,"error_message":null,"monotonicity_verified":false},"error_message":null}
  ```
- **Result**: Job status `done`. Engine result `status="non_monotonic"`, `first_bad_commit=null`, `monotonicity_verified=false`.

---

## 2. Docker Git Security & Worktree Pruning Verification

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
docker compose logs worker | grep "worktree prune"
```
Output:
```text
model_bisect-worker-1  | 2026-10-18 07:05:08,266 [INFO] model_bisect.worker: Target 'demo_target' (/repos/demo_repo) worktree prune: 0 stale worktrees removed
model_bisect-worker-1  | 2026-10-18 07:05:08,272 [INFO] model_bisect.worker: Target 'alternating_target' (/repos/alternating_repo) worktree prune: 0 stale worktrees removed
```

---

## 3. Test Suite Reconciliation & Clean Clone Verification

### Command
```bash
./scripts/verify_clean_clone.sh
```
Raw output saved in [`docs/verification_output.txt`](verification_output.txt).

### Output Summary
```text
==========================================
Clean Clone Verification Results:
Tests Collected: 61
Tests Passed:    61
Exit Code:       0
==========================================
Clean clone verification completed successfully.
```

### Pytest Collection by File
Command: `pytest <file> --collect-only -q`
- `tests/test_adversarial.py`: 34 tests
- `tests/test_api.py`: 8 tests
- `tests/test_engine.py`: 11 tests
- `tests/test_worker.py`: 8 tests
- **Total**: 61 tests collected

---

## 4. Items Not Run

The following items were not executed:
1. **GitHub Action Workflow (`.github/workflows/model_bisect.yml`)**: **Not run**. Requires a real Pull Request execution with a valid `GITHUB_TOKEN`.
2. **GPU Hardware Evaluation**: **Not run**. All evaluations executed on CPU.
3. **Multi-Host / Kubernetes Deployments**: **Not run**. Evaluated strictly on a single macOS host via Docker Compose.
