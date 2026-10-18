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
7. **Repository Mount & Worktree Hygiene**:
   - The `/repos` volume mount is **writable** because `git worktree add` creates metadata records under `.git/worktrees/<name>/` inside the target repository. A read-only mount causes worktree creation to fail.
   - Evaluation commands run with write access to the mounted repositories.
   - A crashed or unexpectedly terminated run can leave behind worktree metadata. At startup, the worker automatically runs `git worktree prune` on each configured target's repository before processing jobs and logs what it removed.

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

## Demo Repositories & Verification Scripts

Model Bisect provides two scripts for building sample repositories in `scripts/`:

1. **Monotonic Demo Repository (`scripts/make_demo_repo.sh`)**:
   Builds a 5-commit repository with metrics `0.95, 0.95, 0.95, 0.80, 0.80` (commits c0–c2 good, c3–c4 bad) at threshold `0.90`. Demonstrates standard regression discovery where `status="found"`, `first_bad_commit` points to commit `c3`, and `monotonicity_verified=true`.

2. **Alternating Non-Monotonic Repository (`scripts/make_alternating_repo.sh`)**:
   Builds a 5-commit repository with alternating metrics `0.95, 0.80, 0.95, 0.80, 0.95` (good, bad, good, bad, good) at threshold `0.90`. Demonstrates non-monotonic regression detection where `status="non_monotonic"`, `monotonicity_verified=false`, and `first_bad_commit=null`.

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
    "target": "alternating_target",
    "good_commit": "5e80c65a62be03fd102794d3de8acfdff813f72e",
    "bad_commit": "3db46a734d5201a11eebdb4e0aa1a53ff892fd73"
  }'
```
**Response (202 Accepted):**
```json
{
  "id": "7e6864c5-6be4-48ff-b98d-ba18fc3611eb",
  "target": "alternating_target",
  "good_commit": "5e80c65a62be03fd102794d3de8acfdff813f72e",
  "bad_commit": "3db46a734d5201a11eebdb4e0aa1a53ff892fd73",
  "threshold_override": null,
  "status": "queued",
  "created_at": "2026-10-18T08:16:37.218130+00:00",
  "started_at": null,
  "finished_at": null,
  "progress_count": 0,
  "result": null,
  "error_message": null
}
```

### 3. Check Job Status & Result (from `docs/job_b.json`)
```bash
curl -X GET http://localhost:8000/jobs/7e6864c5-6be4-48ff-b98d-ba18fc3611eb
```
**Response (200 OK - Done):**
```json
{
  "id": "7e6864c5-6be4-48ff-b98d-ba18fc3611eb",
  "target": "alternating_target",
  "good_commit": "5e80c65a62be03fd102794d3de8acfdff813f72e",
  "bad_commit": "3db46a734d5201a11eebdb4e0aa1a53ff892fd73",
  "threshold_override": null,
  "status": "done",
  "created_at": "2026-10-18T08:16:37.218130+00:00",
  "started_at": "2026-10-18T08:16:37.811091+00:00",
  "finished_at": "2026-10-18T08:16:37.968221+00:00",
  "progress_count": 4,
  "result": {
    "status": "non_monotonic",
    "first_bad_commit": null,
    "evaluated_commits": [
      {
        "commit": "5e80c65a62be03fd102794d3de8acfdff813f72e",
        "metric": 0.95,
        "status": "good",
        "duration": 0.039642458001253544,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "2b8a1750046bcfd21958254273ed9fad2ba5da71",
        "metric": 0.8,
        "status": "bad",
        "duration": 0.021276125000440516,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "2fcd2c876d272b02db1a20a1b62daf4aee4c1848",
        "metric": 0.95,
        "status": "good",
        "duration": 0.0201861249988724,
        "skip_reason": null,
        "eval_runs": null
      },
      {
        "commit": "3db46a734d5201a11eebdb4e0aa1a53ff892fd73",
        "metric": 0.8,
        "status": "bad",
        "duration": 0.022664499998427345,
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

## Status and Limitations

As documented in [`docs/verification.md`](docs/verification.md):

1. **Items Not Run**:
   - **GitHub Action live PR trigger**: Not run against a live pull request event; requires a live GitHub repository with pull request event dispatch and `GITHUB_TOKEN`.
   - **GPU evaluation**: Not run on GPU hardware; all evaluations executed using CPU test models.
   - **Multi-host deployment**: Not run across a multi-host cluster; service was evaluated locally via single-host Docker Compose.
3. **Worktree Hygiene & Scoped Git Security**:
   - The worktree-prune and safe.directory settings are scoped as described in the [Security Model](#security-model) section.
4. **Predecessor Check is Best-Effort**:
   - To strictly respect the compute ceiling $\lceil \log_2(N) \rceil + 2$, non-monotonic detection runs only when binary search finishes with spare budget. An early regression that recovers can be missed if the binary search utilizes the full budget quota. In this scenario, Model Bisect returns `status="found"` with `monotonicity_verified=false` to document that monotonicity was not verified.
5. **Stdout Fallback is a Convenience, Not a Contract**:
   - Stdout output is treated as unstructured process logs. The engine provides a fallback parser for lines matching `name=value`, but if duplicate differing values appear, the commit is marked `skipped` as ambiguous. Reliable evaluations should write to the path in `MODEL_BISECT_METRICS_FILE`.
6. **Octopus Merges & Complex Topologies**:
   - Commit history between `good_commit` and `bad_commit` is linearized topologically using `git rev-list --topo-order --reverse`. For octopus merges or multi-branch features, the engine searches along this linearized sequence.
7. **GPU and IPC Isolation**:
   - `git worktree` isolates file system trees across commits. However, subprocesses share the host's operating system, environment variables, GPU devices, and ports. If evaluations require complete resource sandboxing (e.g., fixed GPU memory, containerized filesystem), the user-provided eval command should invoke a container or sandbox wrapper (e.g., Docker, Apptainer).
8. **Container Paths in Targets Configuration**:
   - In the Docker setup, `targets.toml` paths (such as `repo_path = "/repos/..."`) specify paths inside the container environment, where `./repos` from the host is mounted to `/repos`.

