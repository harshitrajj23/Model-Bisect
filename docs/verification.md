# Model Bisect Verification Report

This document is the authoritative verification report for Model Bisect, covering test coverage, verification results, reproduction commands, and known system limitations.

---

## 1. Scope of Verification

The verification suite tests the robustness, safety, and correctness of Model Bisect across edge cases and stress scenarios:

1. **Git Edge Cases & Workspace Safety**:
   - Non-ancestor commit pairs rejected cleanly (`ValueError` with clear error status).
   - Identical `good` and `bad` commits rejected immediately.
   - Minimal repositories (1 commit and 2 commits).
   - Merged branches, short hashes, branch names, and tags.
   - Detached HEAD states and dirty working trees: verified that worktrees are cleaned up in `try...finally` and the user's working copy remains untouched.

2. **Evaluation Command Handling & Subprocess Lifecycle**:
   - Non-zero exit codes handled cleanly (commit marked as `skipped` with stderr reason).
   - Hanging/stuck processes terminated cleanly via process group `SIGKILL` after timeout, with all child processes reaped.
   - Quoting and special shell arguments handled correctly.

3. **Metric Extraction & Validation Contract**:
   - Primary source: Flat JSON object written to `MODEL_BISECT_METRICS_FILE` containing finite float metrics.
   - Stdout fallback: Single-line `name=value` accepted. Ambiguous conflicting values cause the commit to be skipped.
   - Configurable domain validation (`--valid-range MIN MAX`) ensuring values outside specified boundaries are rejected.
   - Absence of metric or invalid JSON triggers a graceful commit skip without engine crashes.

4. **Search Correctness & Evaluation Budget**:
   - Threshold equality (inclusive boundary checks).
   - Directionality (`higher` vs. `lower` is better).
   - All commits passing or all failing.
   - Boundary regressions (at first candidate commit or last candidate commit).
   - Evaluation budget guarantee: total evaluations strictly bounded by $\lceil \log_2(N) \rceil + 2$ on monotonic histories.

5. **Repeated Runs & Noise Mitigation**:
   - Repeat count with median aggregation over valid runs.
   - Graceful skip when any repetition fails or produces invalid metrics.

6. **CLI & Serialization**:
   - CLI execution and JSON output writing to arbitrary or nested directory paths.
   - Strict Pydantic roundtrip validation.
   - Deterministic, byte-identical JSON outputs across identical runs.

---

## 2. Verification Results

- **Total Test Cases**: 38 tests collected across 2 test suites:
  - `tests/test_adversarial.py`: 33 tests
  - `tests/test_engine.py`: 5 tests
- **Status**: 100% PASS (38 passed, 0 failed, 0 skipped)
- **Execution Time**: ~67s

---

## 3. Reproduction Commands

To reproduce the verification results in a clean, isolated environment:

### Clean Clone Verification
Model Bisect includes an automated verification script that creates an isolated clone in a temporary directory, sets up a fresh virtual environment, installs dependencies, and runs all test suites:

```bash
./scripts/verify_clean_clone.sh
```

### Manual Test Execution
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

---

## 4. Known Limitations

1. **Predecessor Check is Best-Effort**:
   To strictly respect the compute ceiling $\lceil \log_2(N) \rceil + 2$, non-monotonic detection runs only when binary search finishes with spare budget. An early regression that later recovers can be missed if the binary search utilizes the full budget quota. In this scenario, Model Bisect returns status `"found"` with `monotonicity_verified=false` to document that monotonicity was not verified.

2. **Stdout Fallback is a Convenience, Not a Contract**:
   Stdout output is treated as unstructured process logs. The engine provides a fallback parser for lines matching `name=value`, but if duplicate differing values appear, the commit is marked `skipped` as ambiguous. Reliable evaluations should write to the path in `MODEL_BISECT_METRICS_FILE`.

3. **Octopus Merges & Complex Branch Topologies**:
   Commit history between `good_commit` and `bad_commit` is linearized topologically using `git rev-list --topo-order --reverse`. For octopus merges or multi-branch features, the engine searches along the linearized chronological sequence.

4. **GPU and IPC Isolation**:
   `git worktree` isolates file system trees across commits. However, subprocesses share the host's operating system, environment variables, GPU devices, and ports. If evaluations require complete resource sandboxing (e.g., fixed GPU memory, containerized filesystem), the user-provided eval command should invoke a container or sandbox wrapper (e.g., Docker, Apptainer).
