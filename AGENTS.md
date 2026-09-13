# AGENTS.md

> Claude Code loads this file via `CLAUDE.md` (`@AGENTS.md` import), following the
> agent-utilities convention — edit this file, never `CLAUDE.md`'s body.

## Status: pre-extraction scaffold (RF-ADR-009 W0 / W5-prep)

**This repository does not yet run graph-os.** It is an empty-but-real Python
project skeleton created ahead of Migration Wave 5 (see "Migration wave" below).
The live MCP server, REST gateway, control plane, fleet gateway, and webui
hosting all still run out of `agent-utilities` and are deployed by
`services/graph-os` — see `services/graph-os/AGENTS.md` and
`inventory/k8s-migration/GRAPHOS-LOCAL-REDEPLOY.md` in the workspace for the
current, live topology. Nothing in that deployment changes because this
repository exists.

The one real, tested surface here is the `graph-os` console script
(`graph_os.cli:main`), which reports the installed version and this
repository's target composition — see `graph_os/cli.py`.

## Role in Agent OS Architecture (target, RF-ADR-009 §2/§2.4)

RF-ADR-009 (`plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md` in the
workspace) splits `agent-utilities` into five layers with one direction of
dependency. This repository is **phase 5, "graph-os"**:

> the deployable composition: MCP server (`kg_server` split into modules),
> REST gateway, control plane, fleet gateway wiring, agent-webui hosting,
> deployment tooling (`deployment/`, doctor, release canary) — **must not
> own** business logic that belongs to epistemic-graph, agent-connector-sdk,
> or the agent-utilities agent plane.

Per §2.4: it keeps the `graph-os` console script and the existing
`services/graph-os` deployment repo (nothing operator-facing is renamed); the
in-process fleet loader (multiplexer) stays, but its catalog moves from a
static file to the epistemic-graph server registry; `services/mcp-multiplexer`
retires once verified unused; agent-webui stays one product UI served by
graph-os, with its BFF calling EG directly instead of importing
agent-utilities knowledge-graph internals.

## Module mapping

Each package under `graph_os/` is presently a docstring-only placeholder — see
"W5 source measurements" below for what it receives:

| Package | Owns | Source today |
|---|---|---|
| `graph_os.mcp_server` | the graph-os MCP tool surface (`graph_*`/`ontology_*`/`object_*`/`engine_*`) | `agent_utilities/mcp/kg_server.py` |
| `graph_os.fleet` | the in-process fleet gateway / multiplexer | `agent_utilities/mcp/multiplexer.py` |
| `graph_os.gateway` | the REST gateway + gateway host daemon + dashboard | `agent_utilities/gateway/` |
| `graph_os.control_plane` | fleet reconciliation, action-policy enforcement | `agent_utilities/control_plane/` |
| `graph_os.webui_host` | agent-webui hosting (the webui co-service) | `agent_utilities/server/webui_co_service.py` |
| `graph_os.deployment` | deployment doctor, release canary, production ops | `agent_utilities/deployment/` |

## W5 source measurements (measured 2026-09-12)

Line counts against `agent-utilities` `HEAD` at measurement time (`wc -l` over
every `.py` file in the directory; single files counted directly). These are
the sizes Migration Wave 5 (RF-ADR-009 §7) will actually move — not estimates.

| Source | Lines | Moves to |
|---|---:|---|
| `agent_utilities/mcp/kg_server.py` | 6,443 | `graph_os.mcp_server` (split into modules per §2.4) |
| `agent_utilities/mcp/multiplexer.py` | 8,136 | `graph_os.fleet` |
| `agent_utilities/gateway/` | 14,045 | `graph_os.gateway` |
| `agent_utilities/control_plane/` | 17,856 | `graph_os.control_plane` |
| `agent_utilities/server/webui_co_service.py` | 176 | `graph_os.webui_host` |
| `agent_utilities/deployment/` | 18,989 | `graph_os.deployment` |
| **Total** | **65,645** | |

These figures match the RF-ADR-009 §1 subsystem table (`mcp/` 71,100 total —
`kg_server.py` + `multiplexer.py` above account for 14,579 of it, the rest
being the AU-retained multiplexer skill/prompt-harvest and server-scaffolding
code that moves to agent-connector-sdk, not here; `gateway/` 14,045;
`control_plane/` 17,856; `deployment/` 18,989 — all four exact matches).

### Console scripts that belong to graph-os (from agent-utilities' `pyproject.toml`)

Every `[project.scripts]` entry whose target module falls under one of the six
paths above — these move to this repository's own `[project.scripts]` in W5,
replacing today's single placeholder (`graph-os = "graph_os.cli:main"`):

| Script | Target (today) | Destination package |
|---|---|---|
| `graph-os` | `agent_utilities.mcp.kg_server:mcp_server` | `graph_os.mcp_server` |
| `graph-os-daemon` | `agent_utilities.gateway.daemon:main` | `graph_os.gateway` |
| `graph-os-release-canary` | `agent_utilities.deployment.release_canary:main` | `graph_os.deployment` |
| `graph-os-certify-skills` | `agent_utilities.deployment.skill_validation:main` | `graph_os.deployment` |
| `graph-os-generate-skill-runtime-profile` | `agent_utilities.deployment.skill_validation_assets:profile_main` | `graph_os.deployment` |
| `graph-os-generate-skill-certification` | `agent_utilities.deployment.skill_validation_assets:generator_main` | `graph_os.deployment` |
| `graph-os-skill-readiness` | `agent_utilities.deployment.skill_validation_assets:readiness_main` | `graph_os.deployment` |
| `graph-os-verify-skill-certification` | `agent_utilities.deployment.skill_validation_assets:verifier_main` | `graph_os.deployment` |
| `graph-os-production-ops` | `agent_utilities.deployment.production_ops:main` | `graph_os.deployment` |
| `setup-config` | `agent_utilities.deployment.cli:main` | `graph_os.deployment` |
| `agent-utilities-doctor` | `agent_utilities.deployment.doctor:main` | `graph_os.deployment` |
| `agent-utilities-venv` | `agent_utilities.deployment.venv_sync:main` | `graph_os.deployment` |

**Named but NOT moving here**, despite the `graph-os-` prefix — a naming
inconsistency in agent-utilities today, not a graph-os target, worth flagging
to the W5 lane: `graph-os-analytics-worker`
(`agent_utilities.knowledge_graph.analytics_worker:main`) and
`graph-os-certify-connector`
(`agent_utilities.knowledge_graph.integrations.connector_certification_cli:main`)
both target `agent_utilities/knowledge_graph/`, which RF-ADR-009 §2 assigns to
**epistemic-graph**, not graph-os. Also not moving: the dozen
`assemble-*`/`generate-graphos-*`/`check-graphos-compatibility`/
`materialize-*`/`promote-*`/`verify-*`/`graphos-certification-*`/
`graphos-operational-evidence` scripts under `scripts/release/` and
`scripts/certification/` — these are agent-utilities' OWN release/certification
tooling (their name embeds "graphos" as a compatibility-matrix component
label, not as this repository); they stay in agent-utilities.

## Target dependencies (planned, W5 — not installed today)

`dependencies = []` in `pyproject.toml` today (Wire-First: a dependency is
declared only once code that imports it lands). Once Migration Wave 5 extracts
real code, this repository will need:

- **`agent-connector-sdk`** — RF-ADR-009 §3 phase 3 (MCP server factory, action
  dispatch, base config/exceptions). **Does not exist yet** — it is created in
  W2, after epistemic-graph (W1). graph-os cannot declare this dependency
  before then; see the `dependency-readiness` pre-commit hook below, which
  would fail closed on exactly that mistake.
- **`epistemic_graph`** (the Python client) — for graph/ontology/query/proof
  calls the fleet gateway and control plane need. Ships from the
  epistemic-graph repository; graph-os depends on its client surface only,
  never the engine crate.
- **The agent-utilities agent plane surface** graph-os composes at deploy time
  — `fastmcp`, the `pydantic-ai` agent runtime — declared once W5 extracts code
  that actually imports them.

## Pre-commit hooks: adopted vs. dropped

`.pre-commit-config.yaml` here adopts every **tool-based, content-agnostic**
hook from agent-utilities' combined suite (`agent-packages/agent-utilities/.pre-commit-config.yaml`,
~100 hooks) that applies to a Python package with no KG/ontology/engine/backend
content and zero declared dependencies yet:

**Adopted:** `check-added-large-files`, `check-ast`, `check-yaml`, `check-toml`,
`check-json`, `fix-byte-order-marker`, `check-merge-conflict`,
`detect-private-key`, `trailing-whitespace`, `end-of-file-fixer`, `ruff-check`,
`ruff-format`, `mypy`, `vulture`, `codespell`, `bandit`, `nbqa-ruff`, `uv-lock`,
`hadolint`, and `dependency-readiness` (the RF-ADR-009 §3 phase-direction
check — wired from day one per the ADR's "enforced, not documented" mandate,
even though it trivially passes today), plus a local `pytest` hook
(pre-push/manual, mirroring agent-utilities' own).

**Dropped**, grouped by reason — every hook agent-utilities runs that is NOT
in this repository's config:

| Reason | Hooks |
|---|---|
| AU-specific KG/ontology/concept-governance content check; no such content exists here yet | `check-agent-standards`, `check-concept-gaps`, `check-concept-governance`, `check-concept-governance-merged`, `check-ontology`, `check-skill-name-collision`, `check-security-identity-isolation`, `check-security-parser-fuzz`, `check-security-tenant-identity`, `check-no-per-element-ingest-loop`, `check-http-transport-closure`, `turtle-format`, `guardrail-citation-lineage`, `guardrail-concept-domain-vocab`, `guardrail-concept-freshness`, `guardrail-entrypoint-engine-authority`, `guardrail-epistemic-operations-protocol`, `guardrail-eval-corpus`, `guardrail-genesis-manifest`, `guardrail-kg-skill-coverage`, `guardrail-openapi-coverage`, `guardrail-parity`, `guardrail-prebundled-skills`, `guardrail-prod-profile`, `guardrail-reliability-corpus`, `guardrail-retrieval-quality`, `guardrail-prompt-schema` |
| References an agent-utilities `scripts/*.py` with no equivalent in this repo yet; port in W5 alongside the code each would check | `check-bumpversion`, `check-cli-help`, `check-current-only-contract-debt`, `check-event-loop-blocking`, `check-gitignore-convergence`, `check-guardrails-release-supply-chain`, `check-identifier-interpolation`, `check-import-cycles`, `check-import-safety`, `check-mermaid`, `check-no-stdout-writes`, `check-release-catalogs`, `check-root-hygiene`, `check-secret-history`, `check-stubs`, `check-swallowed-errors`, `check-wire-first`, `ci-gate-replica`, `ci-gate-replica-consistency`, `ci-gate-replica-skip-advisory`, `complexity-staged`, `constrained-parallelism`, `contract-checks`, `docs-build` (superseded meanwhile by `mkdocs build --strict` in `.github/workflows/pages.yml`), `docs-consistency`, `guardrail-coupling-advisory`, `guardrail-docs-contract`, `guardrail-gate-meta-tests`, `guardrail-liveness`, `guardrail-no-stub`, `guardrail-pre-commit-patch-safety`, `guardrail-removed-symbol-consumers`, `guardrail-sprawl`, `guardrail-surface-parity` (will matter once `graph_os.gateway`/`graph_os.mcp_server` both have real routes), `guardrail-tracked-privacy`, `guardrail-version-consistency` (partly covered meanwhile by `.bumpversion.cfg` + `tests/test_cli.py` asserting `__version__`), `security-sanitizer`, `supply-chain-source-contract` (also noted in `pages.yml`) |
| Targets a build/runtime shape this repo doesn't have yet | `backend-interface-parity`, `backend-parity` (no storage backends), `docker-compose-check`, `guardrail-no-pyo3`, `wheel-build` (pure-Python package, no native/Rust wheel) |
| No dependencies declared yet (`dependencies = []`) | `dependency-audit` (OSV) |
| No env vars read yet (Configuration discipline: an env var is a last resort) | `env-var-drift` |
| Requires native scanner toolchain installs (dupehound/jscpd) sized for a much larger codebase | `clone-dupehound-changed-functions`, `clone-jscpd-census`, `clone-jscpd-diff`, `guardrail-cpd-drift` |
| Requires agent-utilities' `lane_resources.yaml` multi-worktree concurrency governance; revisit once this repo has multiple concurrent contributor lanes | `lane-guard` |

## Quality bar (RF-ADR-009 §4)

Applies here like every repository in scope: full green on the combined
pre-commit/pre-push suite (this repo's own, per the accounting above — not a
subset), no suppressions/baselines/ratchets, and "green means the full release
workflow run locally first" (every job/step in `.github/workflows/release.yml`,
continuing past failures) before trusting a GitHub run. The four wiring gates
(orphan modules, contract-method reachability, durable-table registration,
pinned-reference resolution) are deferred alongside the AU-specific
`scripts/*.py` they depend on (see the dropped-hooks table); `tests/test_package_layout.py`
is this repository's own stand-in for "orphan modules" today — every package
under `graph_os/` is imported and asserted to document its ownership.

## Documentation (RF-ADR-009 §5)

Per §5, this repository does **not** treat a `/docs` folder as its documentation
surface — the surface is the **published GitHub Pages site**
(`.github/workflows/pages.yml`, built with `mkdocs build --strict` from the
`docs/` sources). `mkdocs.yml` is the minimal skeleton; content grows as real
capability lands (component registry → architecture pages, contract →
capability ledger — generated wherever derivable, hand-written prose only for
introductions/concepts, per §5 D2).

## Branching & isolation

This repository is not yet a shared multi-worktree checkout (this scaffold is
its first commit), but the workspace-wide convention applies from here on: work
in a dedicated worktree, never the canonical checkout directly.

```
git worktree add ${XDG_STATE_HOME}/repository-worktrees/graph-os/<branch> -b <branch> main
```

Never `Agent(isolation: "worktree")` / `EnterWorktree` against this repo once it
has more than one contributor lane — see the workspace root `AGENTS.md`
"⚠ Never spawn a subagent with `isolation: "worktree"` against a shared repo
checkout" for the confirmed `core.bare=true` corruption this avoids. Never
`git stash` in a shared checkout of this repo for the same reason
(`refs/stash` is repo-wide, not per-worktree).

## References

- `plans/refactor/RF-ADR-009-connector-sdk-and-graph-os.md` — the decision this
  repository implements.
- `services/graph-os/AGENTS.md` — the live deployment this repository does not
  yet replace.
- `inventory/k8s-migration/GRAPHOS-LOCAL-REDEPLOY.md` — how graph-os is built
  and deployed today (kaniko job, in-pod engine, `/au` live mount).
- `agent-packages/agent-utilities/AGENTS.md` and `.pre-commit-config.yaml` —
  the conventions this repository's scaffold mirrors.
