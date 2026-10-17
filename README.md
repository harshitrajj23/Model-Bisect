# Model Bisect

Model Bisect is a high-performance tool that identifies the exact Git commit that caused an ML model's evaluation metric to regress. Like `git bisect`, it performs a binary search over commit history, but classifies commits by evaluating quantitative metrics against a defined threshold.

---

## Architecture Overview

Model Bisect consists of:
1. **Core Engine**: Worktree-isolated git binary search with deterministic compute bounds ($\lceil \log_2(N) \rceil + 2$).
2. **FastAPI Service**: RESTful API accepting bisect jobs referencing pre-configured server targets.
3. **Background Worker**: Polling worker claiming queued jobs atomically using SQLite transactions and reporting real-time progress.
4. **GitHub Action**: Self-contained PR workflow bisecting regressions on model changes via `.model-bisect.toml`.

---

## Security Model

Security is a primary design constraint for the Model Bisect service:

1. **No Remote Shell Execution**: The evaluation command is **never** accepted from HTTP request payloads. All evaluation commands and repository locations are statically defined on the server in a configuration file (`targets.toml`).
2. **Strict Request Schema**: `POST /jobs` accepts only:
   - `target`: String identifier of a pre-configured target.
   - `good_commit`: Git reference for the baseline commit.
   - `bad_commit`: Git reference for the regressed commit.
   - `threshold`: Optional numeric threshold override.
3. **Allowlisted Repository Root**: Every configured target's `repo_path` must resolve strictly inside an allowlisted root directory (`MODEL_BISECT_REPO_ROOT`, e.g., `/repos`). Any path traversal or path outside this root is rejected immediately with HTTP 400.
4. **Strict Commit Reference Validation**: `good_commit` and `bad_commit` parameters are validated against a strict regex pattern (`^(?!-)(?!.*\.\.)[a-zA-Z0-9_./~^@-]+$`). Leading hyphens (option flags) and shell metacharacters (`$`, `;`, `&`, `|`, quotes, backticks) are rejected with HTTP 422. References are passed as arguments to `git`, never interpolated into shell strings.
5. **No Information Disclosure**: Internal stack traces, server paths, and exception details are masked from API error responses.
6. **Network Access Notice**: Anyone who can reach the API can trigger evaluations defined in `targets.toml`. Authentication is out of scope for v1. **Do not expose this API publicly without an authenticating reverse proxy or VPN.**

---

## Starting Locally

### Prerequisites
- Python 3.12+ (or Python >= 3.10)
- Git 2.30+

### Option A: Local Processes (Without Docker)

1. **Clone repository and set up environment:**
   ```bash
   git clone https://github.com/example/Model_Bisect.git
   cd Model_Bisect
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure targets:**
   Create `targets.toml` in the project root:
   ```toml
   [targets.nlp_model]
   repo_path = "/path/to/my_model_repo"
   eval_command = "python eval.py"
   metric_name = "accuracy"
   threshold = 0.90
   direction = "higher"
   valid_range = [0.0, 1.0]
   timeout = 60.0
   repeat_count = 1
   ```

3. **Start the API server:**
   ```bash
   export MODEL_BISECT_TARGETS_FILE="$(pwd)/targets.toml"
   export MODEL_BISECT_REPO_ROOT="/path/to"
   export MODEL_BISECT_DB_PATH="$(pwd)/model_bisect.db"
   uvicorn model_bisect.api:app --host 127.0.0.1 --port 8000 --reload
   ```

4. **Start the background worker in a separate terminal:**
   ```bash
   source .venv/bin/activate
   export MODEL_BISECT_TARGETS_FILE="$(pwd)/targets.toml"
   export MODEL_BISECT_REPO_ROOT="/path/to"
   export MODEL_BISECT_DB_PATH="$(pwd)/model_bisect.db"
   python -m model_bisect.worker
   ```

---

### Option B: With Docker Compose

Start the API and background worker with a single command:
```bash
docker compose up --build
```

- The API port is bound to `127.0.0.1:8000` (loopback only) for security.
- The SQLite database is persisted in the `db-data` docker volume.
- Server configuration is mounted from `./targets.toml` (read-only).
- Evaluated repositories are mounted from `./repos` as **writable** because `git worktree add` creates metadata under `.git/worktrees/<name>/` inside the source repository. A read-only mount would cause worktree creation to fail.

---

## API Endpoints & Example cURL Commands

### 1. Health Check
```bash
curl -X GET http://localhost:8000/health
```
**Response (200 OK):**
```json
{
  "status": "ok",
  "database": "connected"
}
```

### 2. Submit a Job
```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "target": "nlp_model",
    "good_commit": "v1.0.0",
    "bad_commit": "HEAD",
    "threshold": 0.92
  }'
```
**Response (202 Accepted):**
```json
{
  "id": "e2d4e76a-3607-4e78-9eb4-8fd89b7fa9dc",
  "target": "nlp_model",
  "good_commit": "v1.0.0",
  "bad_commit": "HEAD",
  "threshold_override": 0.92,
  "status": "queued",
  "created_at": "2026-10-18T00:15:00.000000+00:00",
  "started_at": null,
  "finished_at": null,
  "progress_count": 0,
  "result": null,
  "error_message": null
}
```

### 3. Check Job Status & Result
```bash
curl -X GET http://localhost:8000/jobs/e2d4e76a-3607-4e78-9eb4-8fd89b7fa9dc
```
**Response (200 OK - Done):**
```json
{
  "id": "e2d4e76a-3607-4e78-9eb4-8fd89b7fa9dc",
  "target": "nlp_model",
  "good_commit": "v1.0.0",
  "bad_commit": "HEAD",
  "threshold_override": 0.92,
  "status": "done",
  "created_at": "2026-10-18T00:15:00.000000+00:00",
  "started_at": "2026-10-18T00:15:01.000000+00:00",
  "finished_at": "2026-10-18T00:15:22.000000+00:00",
  "progress_count": 5,
  "result": {
    "status": "found",
    "first_bad_commit": "7a3f81e4b85c1d683a9082ef7890123456789abc",
    "evaluated_commits": [...],
    "total_eval_count": 5,
    "error_message": null,
    "monotonicity_verified": true
  },
  "error_message": null
}
```

### 4. List Recent Jobs
```bash
# List all recent jobs
curl -X GET http://localhost:8000/jobs

# Filter by status
curl -X GET "http://localhost:8000/jobs?status=running"
```

---

## GitHub Action Setup

Model Bisect provides a self-contained GitHub Action for automated CI regression testing on pull requests:

1. **Commit a configuration file (`.model-bisect.toml`) to your repository:**
   ```toml
   eval_command = "python eval.py"
   metric_name = "accuracy"
   threshold = 0.90
   direction = "higher"
   valid_range = [0.0, 1.0]
   timeout = 120.0
   repeat_count = 1
   fail_on_not_found = false
   ```

2. **Add the workflow file (`.github/workflows/model_bisect.yml`):**
   - Triggers when files under `models/` or `model/` change.
   - Evaluates the commits between `pull_request.base.sha` and `pull_request.head.sha`.
   - Generates `result.json`.
   - Posts a summary comment on the pull request with the culprit commit, evaluation count, and monotonicity status using `GITHUB_TOKEN`.

---

## Known Limitations

As documented in [`docs/verification.md`](docs/verification.md):

1. **Predecessor Check is Best-Effort**:
   To strictly respect the compute ceiling $\lceil \log_2(N) \rceil + 2$, non-monotonic detection runs only when binary search finishes with spare budget. An early regression that recovers can be missed if the binary search utilizes the full budget quota. In this scenario, Model Bisect returns `status="found"` with `monotonicity_verified=false` to document that monotonicity was not verified.

2. **Stdout Fallback is a Convenience, Not a Contract**:
   Stdout output is treated as unstructured process logs. The engine provides a fallback parser for lines matching `name=value`, but if duplicate differing values appear, the commit is marked `skipped` as ambiguous. Reliable evaluations should write to the path in `MODEL_BISECT_METRICS_FILE`.

3. **Octopus Merges & Complex Topologies**:
   Commit history between `good_commit` and `bad_commit` is linearized topologically using `git rev-list --topo-order --reverse`. For octopus merges or multi-branch features, the engine searches along this linearized sequence.

4. **GPU and IPC Isolation**:
   `git worktree` isolates file system trees across commits. However, subprocesses share the host's operating system, environment variables, GPU devices, and ports. If evaluations require complete resource sandboxing (e.g., fixed GPU memory, containerized filesystem), the user-provided eval command should invoke a container or sandbox wrapper (e.g., Docker, Apptainer).
