# graph-os specifications

These specs follow the ecosystem [spec standard](https://github.com/Knuckles-Team/pipelines/blob/main/reference/spec-standard.md): one owning spec per capability, extend an existing spec before creating one, audits and reviews land in the owning spec, one ID and title form, one file set. The `spec-standard` gate enforces it.

This tracked `specs/` directory is the public build contract for **GRAPHOS** owned work. Every
spec must contain the behavior, architecture, interfaces, tests, quality gates, and acceptance
criteria needed to implement it from this repository. External program notes may inform a draft,
but no private document or local workspace path is required to build or verify a public spec.
Delivery status is recorded here with exact merged revision and test evidence.

## Structure

Create `specs/<stable-id>/` with `spec.md` (user outcome, requirements, acceptance), `plan.md`
(architecture, reuse, interfaces, live wiring, decisions), `test-spec.md` (positive, negative,
integration, quality and release proof), `tasks.md` (ordered implementation and verification),
`requirements.md` (the definition of every requirement ID the spec owns), and `status.json`
(machine-readable delivery, acceptance, and public receipts). `status.json`'s `requirements` array
carries one entry per requirement ID defined in `requirements.md`, each with its own
`delivery_state` and evidence; a requirement counts as delivered only with a merged-head commit on
the default branch.
Start from [`_template/`](_template/). Keep status and evidence explicit; a planned or tested item
is not a landed item. Put durable evidence links in the spec directory, never local scratch output.
This follows GitHub Spec Kit's specify/plan/tasks flow with an explicit test contract. The tracked [constitution](../.specify/memory/constitution.md) records this repository's governing principles.

Each spec must name its stable ID and sole owner. Cross-repository work links the other owners'
public specs by stable ID and GitHub URL; each repository documents the complete contracts and
acceptance it owns. Designs must inventory existing code and reuse the legal
owner and live wiring before proposing new components. Include CCCC, jscpd, dupehound, KISS,
language-native and repo release gates where applicable, with exact pass evidence and a
no-duplicate-authority check.

## Status and evidence

The tracked `status.json` is the source for the public HTML report. It uses delivery states
`UNKNOWN`, `SPECIFIED`, `BUILDING`, `BUILT`, `LANDED`, `CLOSED`, `DEFERRED`, and `REJECTED`,
and acceptance states `NOT_AUDITED`, `PENDING`, `ACCEPTED`, and `FAILED`. Use
`SPECIFIED/NOT_AUDITED` for a complete design awaiting build; use `UNKNOWN/NOT_AUDITED`
when existing code has not received an exact-revision audit. Prose labels such as `PARTIAL`
or `IN REVIEW` may describe current work, but they do not prove delivery.

`LANDED` requires a public merged-head receipt for the exact owning-repository revision.
`ACCEPTED` additionally requires the checked-in test and consumer or release receipts.
Record public issue, PR, check, and commit links in the owner spec and evidence array.
An obligation can be landed while acceptance remains open.

## Specification index

| Directory | Spec ID | Owned requirement IDs |
|---|---|---|
| [`a2a-task-projection/spec.md`](a2a-task-projection/spec.md) | `GRAPHOS-A2A-001` | `A2A-01`–`A2A-07` (defined in [requirements.md](a2a-task-projection/requirements.md) as `GRAPHOS-A2A-R001`–`R008`) |
| [`a2a-capability-and-history-projection/spec.md`](a2a-capability-and-history-projection/spec.md) | `GRAPHOS-A2A-003` | `A2A-H01`–`A2A-H07` (defined in [requirements.md](a2a-capability-and-history-projection/requirements.md) as `GRAPHOS-A2A-R001`, `R001.1`–`R007`); depends on `GRAPHOS-A2A-002` |
| [`graphos-a2a-002/spec.md`](graphos-a2a-002/spec.md) | `GRAPHOS-A2A-002` | `GRAPHOS-A2A-002-R001`–`R006` (incl. `GRAPHOS-A2A-002-R001.1`) |
| [`adaptive-capacity/spec.md`](adaptive-capacity/spec.md) | `GRAPHOS-CAPACITY-001` | `GRAPHOS-CAPACITY-R001`–`R002` |
| [`conversational-acp-gateway/spec.md`](conversational-acp-gateway/spec.md) | `GRAPHOS-ACP-001` | `GRAPHOS-ACP-R001` |
| [`data-and-market-projections/spec.md`](data-and-market-projections/spec.md) | `GRAPHOS-DATA-MARKET-001` | `GRAPHOS-DATA-MARKET-R001`–`R006` |
| [`dependency-ordered-release/spec.md`](dependency-ordered-release/spec.md) | `GRAPHOS-RELEASE-001` | `RL-01`–`RL-07` (defined in [requirements.md](dependency-ordered-release/requirements.md) as `GRAPHOS-RELEASE-R001`–`R003`) |
| [`fleet-catalog-and-tools/spec.md`](fleet-catalog-and-tools/spec.md) | `GRAPHOS-FLEET-001` | `GRAPHOS-FLEET-R001`–`R028`, `PA-12` |
| [`host-composition-boundary/spec.md`](host-composition-boundary/spec.md) | `GRAPHOS-HOST-001` | `GRAPHOS-HOST-R001`–`R019` |
| [`hosted-api-operations/spec.md`](hosted-api-operations/spec.md) | `GRAPHOS-OPS-001` | `HO-01`–`HO-11` (defined in [requirements.md](hosted-api-operations/requirements.md) as `GRAPHOS-OPS-R001`–`R038`) |
| [`mcp-resource-publication-reconciliation/spec.md`](mcp-resource-publication-reconciliation/spec.md) | `GRAPHOS-MCP-RESOURCES-001` | `GRAPHOS-MCP-RESOURCES-R001`–`R004` |
| [`identity-access/spec.md`](identity-access/spec.md) | `GRAPHOS-IDENTITY-001` | `IA-01`–`IA-16` (defined in [requirements.md](identity-access/requirements.md) as `GRAPHOS-IDENTITY-R001`–`R024`) |
| [`messaging-channel-supervision/spec.md`](messaging-channel-supervision/spec.md) | `GRAPHOS-MESSAGING-001` | `MSG-01`–`MSG-06` (defined in [requirements.md](messaging-channel-supervision/requirements.md) as `GRAPHOS-MESSAGING-R001`–`R006`) |
| [`portable-deployment/spec.md`](portable-deployment/spec.md) | `GRAPHOS-DEPLOY-001` | `GRAPHOS-DEPLOY-R001`–`R015` |
| [`public-ingress-identity/spec.md`](public-ingress-identity/spec.md) | `GRAPHOS-INGRESS-001` | `IN-01`–`IN-06` (defined in [requirements.md](public-ingress-identity/requirements.md) as `GRAPHOS-INGRESS-R001`) |

Each directory's `requirements.md` is the authoritative definition of its requirement IDs; each
`status.json` records their current delivery state and evidence.

## Graph OS owner map

| Prefix | Repository specs | Responsibility |
|---|---|---|
| `EG` | [epistemic-graph](https://github.com/Knuckles-Team/epistemic-graph/tree/main/specs) | Rust database, graph compute, ontology, governed ingestion, durable records and clients |
| `SDK` | [agent-connector-sdk](https://github.com/Knuckles-Team/agent-connector-sdk/tree/main/specs) | connector control, transport, manifests, certification, governed effects |
| `AU` | [agent-utilities](https://github.com/Knuckles-Team/agent-utilities/tree/main/specs) | agent orchestration and control workflows |
| `GRAPHOS` | [graph-os](https://github.com/Knuckles-Team/graph-os/tree/main/specs) | serving composition, fleet, gateway, A2A, deployment operations |
| `WEBUI` | [agent-webui](https://github.com/Knuckles-Team/agent-webui/tree/main/specs) | browser presentation and interaction |
| `RM` | [repository-manager](https://github.com/Knuckles-Team/repository-manager/tree/main/specs) | repository discovery, worktrees, source control and execution |

## Contributions

Read this repository's `AGENTS.md` and any contribution guide. Propose `spec.md` first, resolve
architecture and test details in `plan.md` and `test-spec.md`, then implement `tasks.md` in a
dedicated branch or worktree. Link the PR to spec IDs and update evidence and status only when the
corresponding gates actually pass. Use the [universal-skills spec-generator](https://github.com/Knuckles-Team/universal-skills/tree/main/universal_skills/development/spec-generator),
[spec-verifier](https://github.com/Knuckles-Team/universal-skills/tree/main/universal_skills/development/spec-verifier),
and [task-planner](https://github.com/Knuckles-Team/universal-skills/tree/main/universal_skills/development/task-planner)
and the [graph-os-development](https://github.com/Knuckles-Team/graph-os/blob/main/graph_os/skills/graph-os-development/SKILL.md)
bootstrap skill, together with the [SDD full lifecycle](https://github.com/Knuckles-Team/universal-skills/tree/main/universal_skills/development-workflows/sdd-full-lifecycle) workflow.

- [`GRAPHOS-A2A`](a2a-capability-and-history-projection/spec.md) — A2A streaming, push notifications and transition-history projection
- [`GRAPHOS-A2A`](a2a-task-projection/spec.md) — A2A task and approval projection
- [`GRAPHOS-ACCEPTANCE`](acceptance-evidence-freeze/spec.md) — evidence acceptance freeze
- [`GRAPHOS-CAPACITY`](adaptive-capacity/spec.md) — Adaptive capacity control
- [`GRAPHOS-ACP`](conversational-acp-gateway/spec.md) — Conversational ACP gateway session projection and policy
- [`GRAPHOS-DATA-MARKET`](data-and-market-projections/spec.md) — Schema context, source admission, and finance projection
- [`GRAPHOS-RELEASE`](dependency-ordered-release/spec.md) — Dependency-ordered, digest-pinned GraphOS rollout
- [`GRAPHOS-FLEET`](fleet-catalog-and-tools/spec.md) — Fleet catalog, intent tools, and policy safe reload
- [`GRAPHOS-A2A-002`](graphos-a2a-002/spec.md) — A2A context-budget tool-subset admission
- [`GRAPHOS-HOST`](host-composition-boundary/spec.md) — GraphOS host composition and ownership boundary
- [`GRAPHOS-OPS`](hosted-api-operations/spec.md) — Hosted API and intent operations
- [`GRAPHOS-IDENTITY`](identity-access/spec.md) — ACCESS — identity, authorization, and access operations
- [`GRAPHOS-MCP-RESOURCES`](mcp-resource-publication-reconciliation/spec.md) — MCP resource/template publication reconciliation
- [`GRAPHOS-MESSAGING`](messaging-channel-supervision/spec.md) — Messaging channel supervision lifecycle
- [`GRAPHOS-DEPLOY`](portable-deployment/spec.md) — Portable GraphOS development and deployment
- [`GRAPHOS-INGRESS`](public-ingress-identity/spec.md) — Portable public ingress and identity cutover
