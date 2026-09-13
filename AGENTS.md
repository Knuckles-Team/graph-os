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
hook from agent-utilities'/epistemic-graph's combined suites that applies to a
Python package with no KG/ontology/engine/backend content and zero declared
runtime dependencies yet — including the full native scanner suite (cccc,
KISS, dupehound, jscpd) and the security/privacy/hygiene gates, all copied
from epistemic-graph (see "Scanner hooks: copied files and adaptations" below
for exact source commits).

**Adopted:** `check-added-large-files`, `check-ast`, `check-yaml`, `check-toml`,
`check-json`, `fix-byte-order-marker`, `check-merge-conflict`,
`detect-private-key`, `trailing-whitespace`, `end-of-file-fixer`, `ruff-check`,
`ruff-format`, `mypy`, `vulture`, `codespell`, `bandit`, `nbqa-ruff`, `uv-lock`,
`hadolint`, `dependency-readiness` (the RF-ADR-009 §3 phase-direction check —
wired from day one per the ADR's "enforced, not documented" mandate, even
though it trivially passes today), `complexity-staged` (cccc, diff-scoped),
`kiss-changed-python` + `kiss-census` (KISS, diff-scoped / whole-tree — the
census hook doubles as this repository's Python orphan-module wiring gate,
see below), `dupehound-changed-functions`, `jscpd-differential` + `jscpd-census`,
`security-sanitizer`, `guardrail-tracked-privacy`, `check-root-hygiene`,
`dependency-audit` (OSV — trivially passes today with `dependencies = []`,
wired from day one for the same reason as `dependency-readiness`),
`check-secret-history` (manual-stage locally; release-critical in CI, see
`release.yml`'s `gates` job), plus a local `pytest` hook (pre-push/manual).

**Dropped**, grouped by reason — every hook agent-utilities/epistemic-graph run
that is NOT in this repository's config:

| Reason | Hooks |
|---|---|
| AU-specific KG/ontology/concept-governance content check; no such content exists here yet | `check-agent-standards`, `check-concept-gaps`, `check-concept-governance`, `check-concept-governance-merged`, `check-ontology`, `check-skill-name-collision`, `check-security-identity-isolation`, `check-security-parser-fuzz`, `check-security-tenant-identity`, `check-no-per-element-ingest-loop`, `check-http-transport-closure`, `turtle-format`, `guardrail-citation-lineage`, `guardrail-concept-domain-vocab`, `guardrail-concept-freshness`, `guardrail-entrypoint-engine-authority`, `guardrail-epistemic-operations-protocol`, `guardrail-eval-corpus`, `guardrail-genesis-manifest`, `guardrail-kg-skill-coverage`, `guardrail-openapi-coverage`, `guardrail-parity`, `guardrail-prebundled-skills`, `guardrail-prod-profile`, `guardrail-reliability-corpus`, `guardrail-retrieval-quality`, `guardrail-prompt-schema`, `canonical-property-schema`, `epistemic-operations-protocol`, `documentation-contract`, `scale-documentation` |
| References an agent-utilities/epistemic-graph `scripts/*.py` with no equivalent in this repo yet; port in W5 alongside the code each would check | `check-cli-help`, `check-current-only-contract-debt`, `check-event-loop-blocking`, `check-gitignore-convergence`, `check-guardrails-release-supply-chain`, `check-identifier-interpolation`, `check-import-cycles`, `check-import-safety`, `check-mermaid`, `check-no-stdout-writes`, `check-release-catalogs`, `check-stubs`, `check-swallowed-errors`, `check-wire-first`, `ci-gate-replica`, `ci-gate-replica-consistency`, `ci-gate-replica-skip-advisory`, `constrained-parallelism`, `contract-checks`, `docs-build` (superseded meanwhile by `mkdocs build --strict` in `.github/workflows/pages.yml`), `docs-consistency`, `guardrail-coupling-advisory`, `guardrail-docs-contract`, `guardrail-gate-meta-tests`, `guardrail-liveness`, `guardrail-no-stub`, `guardrail-pre-commit-patch-safety`, `guardrail-removed-symbol-consumers`, `guardrail-sprawl`, `guardrail-surface-parity` (will matter once `graph_os.gateway`/`graph_os.mcp_server` both have real routes), `guardrail-version-consistency` (partly covered meanwhile by `.bumpversion.cfg` + `tests/test_cli.py` asserting `__version__`), `supply-chain-source-contract` (also noted in `pages.yml`), `env-var-drift` (also: no env vars read yet — Configuration discipline, an env var is a last resort), `mint-lease-call-sites`, `persisted-mutation-contract`, `pinned-reference-resolution` |
| Targets a build/runtime shape this repo doesn't have yet (Rust/native/multi-language architecture tooling) | `backend-interface-parity`, `backend-parity` (no storage backends), `docker-compose-check`, `guardrail-no-pyo3`, `wheel-build`, `wheel-smoke` (pure-Python package, no native/Rust wheel), `cargo-clippy`, `cargo-clippy-all-features`, `cargo-deny-advisories`, `check-crates-io-only`, `rust-arch-lint`, `rustfmt-scope`, `import-linter-architecture`, `dependency-cruiser-architecture`, `current-only-architecture`, `no-version-suffixes`, `p2-analytics-reasoning-architecture`, `p2-modality-architecture`, `lazy-lifecycle-architecture`, `universal-read-rls-architecture`, `exact-fault-restart-harness-architecture`, `exact-release-campaigns-architecture`, `engine-contract-check`, `durable-table-registration`, `contract-method-reachability` |
| Requires epistemic-graph's `orphan-modules` mechanism specifically ("every tracked Rust file is compiled by some cargo target") — no Rust here; superseded by `kiss-census`'s `orphan_module` rule for Python (see below) | `orphan-modules` |
| Requires agent-utilities' `lane_resources.yaml` multi-worktree concurrency governance; revisit once this repo has multiple concurrent contributor lanes | `lane-guard` |

## Scanner hooks: copied files and adaptations

Every native scanner and security/privacy/hygiene gate script under
`scripts/` is copied from **epistemic-graph** (not agent-utilities — EG
already carries the generalized, repository-shape-agnostic version of each,
per its own header comments). Each file's header names its exact source
commit. Summary:

| File | Source commit | Adaptation |
|---|---|---|
| `scripts/scanner_contract.py` | `ceec541457209235ce3d0e0526506bf2428dc845` | `[tool.graph_os.scanners]` namespace (not `[tool.epistemic_graph.scanners]`); drops `dependency_cruiser_version`/`import_linter_version`/`arch_lint_version`/`cargo_deny_version` (no Rust/JS architecture tooling here); adds `read_json_report()` (new shared helper, not in the EG original — see below) |
| `scripts/list_scanner_sources.py` | `a14e697c6c07c5058e0d1fe6667d69c4fdf4f95b` | KISS source selector targets `graph_os/*.py`, not `src\|crates/*.rs` |
| `scripts/check_complexity_staged.py` | `593e7e427194d22aade38d7cc1ae2a98ddc677c8` | Drops the Rust exhaustive-dispatch cyclomatic exemption outright (never applicable — zero `.rs` files; porting `rust_exhaustive_match.py` + `rust_lexer.py`, ~900 lines, would be permanently dead code) |
| `scripts/report_complexity_terms.py` | `593e7e427194d22aade38d7cc1ae2a98ddc677c8` | Same exemption removal; simplified to a plain over-cap count (nothing to split into "accepted by rule" vs. backlog without the exemption); `_document` now calls the new shared `scanner_contract.read_json_report()` |
| `scripts/validate_cccc_census.py` | `a14e697c6c07c5058e0d1fe6667d69c4fdf4f95b` | `_read_report` now calls `scanner_contract.read_json_report()` (see below) |
| `scripts/check_dupehound.py` | `339e26cbbbcaccac101b998b60e6d77e9ebe4722` | None functionally; `main()` split into `_run_dupehound`/`_print_resolved_notes`/`_report_register_verdict` to bring it under this repo's own complexity caps (see "Why several copied files needed splitting" below) |
| `scripts/dupehound_ledger.py` | `ceec541457209235ce3d0e0526506bf2428dc845` | `normalized_function_text`/`partition` split into smaller helpers (same complexity-cap reason); **dormant Rust regex** (`\bfn\s+NAME\b`) left unchanged since `dupehound-distinct.toml` is empty — flagged in that file's own header for whoever adds the first Python entry |
| `scripts/check_duplication.py` | `a14e697c6c07c5058e0d1fe6667d69c4fdf4f95b` | None — already fully language-agnostic |
| `scripts/security_sanitizer.py` | `2dc6908b8c85ece0474e1a11f62d1e681eb2ea25` | None functionally (emoji stripped from two print statements per workspace convention) |
| `scripts/audit_dependencies.py` | `fb557bb9758d460d627b982f5009f860b80dba11` | `User-Agent` string renamed to `graph-os-audit/1`; `parse_lock`/`_validate_uv_artifacts`/`load_acceptances`/`audit`/`_advisory_detail`/`main` split into smaller helpers (complexity-cap reason) |
| `scripts/check_tracked_privacy.py` | `b5292e01f1f860dc0480e6babdd6e1d7c41e09e0` | None to the ported logic itself (EG's own `_is_runtime_source_path` already generalized past agent-utilities' repo-specific allowlist); added `_relative_posix()` and a `_ScanContext` dataclass purely to deduplicate the two scan-pass functions' identical preamble/signature (jscpd genuinely fired on both, on first introduction here — see the two `fix:` commits) |
| `scripts/check_root_hygiene.py` | `43eee2c0f398d2da8fa9796909136a1a6bc03427` | `ALLOWED_DOTFILES` is this repository's own tracked set (5 entries vs. EG's larger one) — inherently repo-specific, per the file's own module docstring |
| `scripts/_git_subprocess_env.py` | `ceec541457209235ce3d0e0526506bf2428dc845` | None |
| `scripts/security/check_secret_history.py` | `8d50f8fd6b56ce5db81371d81d9953926ea54ae6` | None — fully repository-agnostic, including its own `--self-check` |
| `scripts/kiss_diff_scope.py` | `refactor/eg-f56-registry-kiss` branch, commit `55835df3` (not yet merged to EG `main` — this lane's instruction was to use its diff-scoping behaviour) | `function_spans()` reimplemented with Python's `ast` module (exact `lineno`/`end_lineno` spans) instead of porting `rust_lexer.py`'s masked-source-regex approach, which has no Python-syntax equivalent |
| `scripts/check_kiss_staged.sh` | same branch/commit | Rewritten for Python: `--lang python`; path selection is `graph_os/**/*.py`, not `src\|crates/**/*.rs`; the Rust "complete compiler-declared module closure" validation (`rust_module_tree.py`) is dropped — Python's per-file KISS rules need no sibling-file closure the way Rust's `mod` declarations do |

**Not copied at all:** `scripts/rust_lexer.py`, `scripts/rust_exhaustive_match.py`,
`scripts/rust_module_tree.py` — Rust-only, and (per the complexity-staged /
kiss_diff_scope adaptations above) nothing in this repository imports them.

**Why several copied files needed splitting.** `check_complexity_staged.py`
compares the STAGED INDEX against `HEAD`; a file with no `HEAD` blob (i.e.
its first-ever commit into this repository) has every function judged as
"NEW" against the caps, with no "pre-existing debt" carve-out. On
epistemic-graph these functions are old, already-passing-there code; on
their first commit into graph-os, several were newly over cyclomatic-10/
cognitive-15 by this repository's own rules. The no-ratchet policy forbids
suppressing that, so `audit_dependencies.py`, `check_dupehound.py`, and
`dupehound_ledger.py` were mechanically split into smaller named helpers —
every split preserves exact validation order, error messages, and control
flow (spot-checked with direct unit calls after each split); nothing was
rewritten. Two commits (`fix: eliminate the remaining jscpd finding...`)
did the same for a same-file jscpd duplication finding in
`check_tracked_privacy.py`, for the identical underlying reason (a
first-introduction "new" finding, not a false positive).

## Orphan-module wiring gate (Python)

No Python equivalent of epistemic-graph's Rust `orphan-modules` hook
("every tracked Rust file is compiled by some cargo target") existed
anywhere in the workspace (confirmed: agent-utilities' `check_wiring.py` is
explicitly documented as "a finder of suspects, not a gate"). Rather than
write a bespoke script, this repository uses **KISS 0.4.10's own built-in
`orphan_module` rule** (`.kiss/kiss.toml` `[global] orphan_module_enabled =
true`), run via the `kiss-census` pre-commit hook (pre-push/manual) and the
`scanner-quality` CI job — both as **one whole-directory invocation**
(`kiss check --config .kiss/kiss.toml --lang python graph_os`), not a
per-file loop: KISS's orphan detection needs the whole package's import
graph to prove a module has zero production/test-only fan-in, and a
single-file invocation has no sibling context to prove that against
(verified empirically — see below). `kiss-census` treats `orphan_module`
findings as a hard failure (`exit 1`), unlike every other KISS finding it
reports (advisory only).

**Plant-and-fire proof** (2026-09-12, then reverted): a scratch module
`graph_os/_kiss_plant.py`, imported by nothing, was added and staged.
`kiss check --config .kiss/kiss.toml --lang python graph_os/_kiss_plant.py`
(single file) reported no orphan finding; the SAME file, checked as part of
`kiss check --config .kiss/kiss.toml --lang python graph_os` (whole
directory), reported `VIOLATION:orphan_module:...graph_os/_kiss_plant.py:1:
... Module ... is an isolated production module (no production or
test-only import edges)`. The `kiss-census` hook (updated to the
whole-directory form specifically because of this finding) correctly
failed with exit 1; `check_kiss_staged.sh`'s diff-scoped `kiss-changed-python`
hook also fired on the same planted file for two ordinary KISS rules
(`branches_per_function`, `returns_per_function`) it was deliberately
written to trip. Both are reverted; `git log` shows no trace of the plant.

## Quality bar (RF-ADR-009 §4)

Applies here like every repository in scope: full green on the combined
pre-commit/pre-push suite (this repo's own, per the accounting above — not a
subset), no suppressions/baselines/ratchets, and "green means the full release
workflow run locally first" (every job/step in `.github/workflows/release.yml`,
continuing past failures, including the `scanner-quality` job) before trusting
a GitHub run. Three of the four EG wiring gates (contract-method reachability,
durable-table registration, pinned-reference resolution) are deferred
alongside the AU/EG `scripts/*.py` they depend on (see the dropped-hooks
table); the fourth (orphan modules) is covered by KISS's own `orphan_module`
rule, above. `tests/test_package_layout.py` additionally proves every package
under `graph_os/` is imported and documents its ownership.

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
