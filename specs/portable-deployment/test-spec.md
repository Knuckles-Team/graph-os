# Test contract

## Required on every PR from a clean checkout

Run `uv sync --extra test`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os tests`, `uvx --from pre-commit==4.6.0 pre-commit run --all-files`, and `uv build --wheel --out-dir dist`. CI installs the wheel in a new environment and invokes `graph-os-release-canary --json` with a packaged engine wheel. Tests use local temporary paths, fake secret resolvers and deterministic network stubs. The gate must not require an operator's private environment, unprovided token or sibling source tree.

| Positive case | Expected result |
|---|---|
| Load all public sample profiles | Closed schema validates; normalized digest stable across runs. |
| Render development/Compose/Kubernetes plans | All required mounts, identity policy, secret references and digest-pinned images are visible. |
| Run preflight for core and optional WebUI | Core prerequisites are separate; WebUI toolchain checked only when selected. |
| Build and install wheel | Console entrypoints import from installed packages and canary reports aggregate pass. |
| Run disposable first boot in CI | Readiness, authorized request, denied request, idempotent effect and rollback receipts persist. |

| Negative case | Required failure |
|---|---|
| Unknown/missing profile key or missing secret reference | Parse error with source section and key; no apply. |
| Plaintext secret, unpinned production image, unwritable runtime path | Validation refusal before process launch. |
| Wrong engine contract or missing optional WebUI | Typed mismatch/unavailable, without a fallback graph or core host failure. |
| Non-loopback disabled identity or missing first-admin transition | Security refusal with redacted diagnostic. |
| Failing readiness or functional probe | Rollout remains unaccepted and rollback plan is available. |
| Missing/miswired pre-push hook | CI hook audit fails instead of reporting empty green. |

Repository scanner checks include CCCC, jscpd, dupehound and KISS alongside Python gates when source changes; actual versions and differential rules are those pinned by this repository's shared pipeline. A failing required source gate must be fixed or narrowly reclassified with a reproducible portable substitute, never silently skipped. Hosted cluster checks carry an explicit optional/runtime label and do not gate a cloud PR unless the CI job provisions that environment itself.

## GRAPHOS-DEPLOY-R016 host daemon container

| Check | Expected result |
|---|---|
| Manifest contract test | Exactly one container has `KG_DAEMON_ROLE=host`. The serving container keeps `client` and dev mode. |
| Daemon isolation | The daemon disables messaging intake and the web UI. It exposes only the daemon metrics port. |
| Engine sharing | `GRAPH_SERVICE_ENDPOINTS` names the shared Unix socket. The resolver selects connect-only mode, so no second engine starts. |
| Server-side dry run | The API server accepts the deployment. |
| Live host lock | The daemon status command reports this container as the host-lock holder. |

## GRAPHOS-DEPLOY-R017 backend deployment planner

| Check | Expected result |
|---|---|
| Plan models and registry | Frozen models; unknown backend and empty target refused. |
| Renderers | Parsed YAML and unit text match the composition; floating images refused; no secret values. |
| Plan-only backends | `apply(dry_run=False)` raises `PlanOnlyBackendError`; plan never dispatches a fleet call. |
| in_process | Dry run returns the plan; live apply needs a verified session and starts the supervisor. |
| Command | `setup-config deploy-plan` prints valid JSON per backend and refuses a malformed override. |
