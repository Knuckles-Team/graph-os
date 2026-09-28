# Test contract

From a clean clone run `uv sync --extra test`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os`, and the normal pre-commit hooks. Pure controller and fake-port tests are required on every PR; they need no live cluster or credential. A disposable served fault-injection job can provision its own engine and child service, and labels its result separately.

| Fixture | Expected result |
|---|---|
| Healthy windows after a breach | Bounded additive recovery, never above the fresh engine/policy ceiling. |
| Retryable service failures exceed budget | Deterministic multiplicative decrease; duplicate window ID leaves one receipt and one state transition. |
| Caller errors, policy refusals, too few samples | No child-health penalty; reason visible in status. |
| Observe mode | Decision receipt exists but fleet admission remains at configured base limit. |
| Enforce mode | Admission rejects/queues above the effective limit with typed retry guidance. |
| Two tenants or children | Independent windows, limits and visibility; no cross-tenant status leak. |
| Missing telemetry, stale engine premise, child disconnect | Explicit degraded state and conservative refusal/probe; no fabricated successful health. |
| Non-admin mode change, stale policy, unavailable audit reservation | No mode change or effect; same refusal on MCP and HTTP. |

Property tests should generate outcome sequences and assert `floor <= effective <= engine_headroom` and that automatic transitions never widen beyond an external ceiling. Run CCCC, KISS, jscpd and dupehound gates from the repository's pinned pipeline when source changes; fix new findings without suppression or copied controller logic. Record exact command, commit, CI URL, fixture seed and redacted served receipt in `tasks.md`.
