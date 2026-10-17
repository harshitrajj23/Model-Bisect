# Evaluation Budget Analysis

This document details the evaluation budget benchmarks for Model Bisect across various commit history lengths and regression positions.

## Theoretical vs. Empirical Budget

For a commit history of size $N$ (from `good_commit` at index $0$ to `bad_commit` at index $N-1$), Model Bisect evaluates the two endpoints, followed by binary search over intermediate commits. The theoretical maximum evaluation count for monotonic histories is:

$$\text{Budget} = \lceil \log_2(N) \rceil + 2$$

To guard against non-monotonic histories (e.g. an earlier regression that recovered before another regression occurred), the engine performs a predecessor check on $low - 1$ when candidate $high$ is identified.

### Trade-off Analysis: Predecessor Verification vs. Eval Budget
An unconditional predecessor check on every history would add $+1$ evaluation in worst-case branches, pushing the eval count to $\lceil \log_2(N) \rceil + 3$. Model Bisect bounds predecessor verification within the $\lceil \log_2(N) \rceil + 2$ budget (`len(eval_history) < max_budget`). When binary search finishes with spare budget (such as when the regression occurs early in the history), the predecessor is evaluated to confirm monotonicity; when the binary search has already utilized its full budget quota, the ceiling is respected. This guarantees deterministic budget compliance on all monotonic histories without requiring users to allocate additional eval compute.

## Benchmark Results on Monotonic Histories

The following table measures the exact evaluation counts on clean monotonic histories across representative repository sizes ($N \in \{2, 3, 7, 8, 16, 20, 64, 100\}$) with the regression introduced at the first possible commit (`first`, index 1), the middle commit (`middle`, index $N//2$), and the last possible commit (`last`, index $N-1$):

| $N$ | Regression Position | Regression Index | Eval Count (With Pred Check) | Eval Count (Without Pred Check) | Budget $\lceil \log_2(N) \rceil + 2$ | Status |
|:---:|:-------------------:|:----------------:|:----------------------------:|:-------------------------------:|:------------------------------------:|:------:|
| 2   | first               | 1                | 2                            | 2                               | 3                                    | PASS   |
| 3   | first               | 1                | 3                            | 3                               | 4                                    | PASS   |
| 3   | last                | 2                | 3                            | 3                               | 4                                    | PASS   |
| 7   | first               | 1                | 5                            | 4                               | 5                                    | PASS   |
| 7   | middle              | 3                | 5                            | 5                               | 5                                    | PASS   |
| 7   | last                | 6                | 5                            | 5                               | 5                                    | PASS   |
| 8   | first               | 1                | 5                            | 4                               | 5                                    | PASS   |
| 8   | middle              | 4                | 5                            | 5                               | 5                                    | PASS   |
| 8   | last                | 7                | 5                            | 5                               | 5                                    | PASS   |
| 16  | first               | 1                | 6                            | 5                               | 6                                    | PASS   |
| 16  | middle              | 8                | 6                            | 6                               | 6                                    | PASS   |
| 16  | last                | 15               | 6                            | 6                               | 6                                    | PASS   |
| 20  | first               | 1                | 6                            | 6                               | 7                                    | PASS   |
| 20  | middle              | 10               | 7                            | 6                               | 7                                    | PASS   |
| 20  | last                | 19               | 7                            | 7                               | 7                                    | PASS   |
| 64  | first               | 1                | 8                            | 7                               | 8                                    | PASS   |
| 64  | middle              | 32               | 8                            | 8                               | 8                                    | PASS   |
| 64  | last                | 63               | 8                            | 8                               | 8                                    | PASS   |
| 100 | first               | 1                | 9                            | 8                               | 9                                    | PASS   |
| 100 | middle              | 50               | 9                            | 8                               | 9                                    | PASS   |
| 100 | last                | 99               | 9                            | 9                               | 9                                    | PASS   |

All tested configurations strictly satisfy the evaluation budget: `Eval Count <= Budget`.
