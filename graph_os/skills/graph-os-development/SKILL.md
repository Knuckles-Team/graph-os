---
name: graph-os-development
domain: development
skill_type: skill
description: >-
  Develop a feature in any Graph OS ecosystem repository from public GitHub
  checkouts. Use when choosing the owner of any Graph OS ecosystem spec,
  preparing an isolated source and test environment, or proving its deliverable.
license: MIT
tags: [graph-os, development, public-contribution, specs]
metadata:
  version: '1.0.1'
---

# Graph OS ecosystem development

Start at the public [organization hub](https://github.com/Knuckles-Team/.github):
its [build-first queue](https://knuckles-team.github.io/.github/build-first.html)
lists work still to build, the [spec status report](https://knuckles-team.github.io/.github/spec-status.html)
links owner-native contracts, and the [contribution guide](https://github.com/Knuckles-Team/.github/blob/main/CONTRIBUTING.md)
walks a new contributor or coding agent from checkout to pull request.

Use the **owning repository's tracked `specs/<id>/`** as the complete contribution
contract. Its `spec.md`, architecture/design `plan.md`, `test-spec.md`, and
`tasks.md` must contain enough context, interfaces, decisions, fixtures, and
acceptance criteria for a contributor with only public GitHub access. Include
local contracts or public links to a related repository's `specs/<id>/` when a
change crosses boundaries. Do not require a private ledger, unpublished draft,
operator inventory, internal URL, or live deployment to interpret a spec.

## Ownership and reuse

| Repository | Primary ownership |
|---|---|
| `epistemic-graph` | Durable graph engine, query/compute, protocol, generated contracts, Python client |
| `agent-connector-sdk` | Connector transport, certification, typed source and sink ports |
| `agent-utilities` | Agent orchestration, KG application services, skills and workflow runtime |
| `graph-os` | Deployable gateway, MCP/REST composition, control plane and operations |
| `agent-webui` | Browser interface and its Python service adapter |
| `repository-manager` | Repository lifecycle, worktrees, gate orchestration and release readiness |
| `pipelines` | Shared CI, quality and Pages workflows, artifact publication order |
| `agent-terminal-ui` | Terminal client interaction and local presentation |
| `emerald-exchange` | Finance strategy application, curation and governed trading workflows |
| `tunnel-manager` | Tunnel inventory, configuration and control API |
| `.github` | Public ecosystem map, cross-repository status and program governance |

Other connector, frontend and service repositories may own additional
requirements. Use the owner named by the public spec and verify it against
actual code and interfaces. A related consumer spec does not transfer authority
from the component that implements the behavior.

Read each affected repository's `AGENTS.md`, `CONTRIBUTING.md`, own spec, public
interfaces, and tests. Extend the existing owner and execution path. A new
component needs a real caller and a test of that edge. Keep a capability in one
owner and use its exported contract in other repositories; avoid copied policy,
parallel registries, and compatibility scaffolding without a named consumer.
If the needed behavior has no owner spec, write the self-contained spec there
before implementation. A merged source change, test pass, and acceptance result
are distinct states; record evidence for each before calling a deliverable done.

## Public source environment

For a fresh checkout, use [the public bootstrap recipe](references/bootstrap.md).
It reproduces the source paths declared by the repositories and the public
Graph OS release workflow; it does not assume a preexisting monorepo, private
package index, credentials, or running services. `repository-manager` is needed
only when working on its code or release-readiness logic. Work in a personal Git
branch/worktree and stage only reviewed paths. Never use a shared `git stash` or
an agent harness worktree action in a multi-worktree repository.

Install [universal-skills](https://github.com/Knuckles-Team/universal-skills)
and use `spec-generator`, `task-planner`, `sdd-implementer`, `spec-verifier`,
and `sdd-full-lifecycle` as needed. Read all six owner files: `spec.md`,
`plan.md`, `test-spec.md`, `tasks.md`, `requirements.md`, and `status.json`
(see `specs/README.md`'s own "Structure" section). A complete public
spec supplies its own design, tests, fixtures, and acceptance criteria; never
ask a contributor to retrieve private drafts or local operator inventory.

Check the target repository's own `AGENTS.md` for current commands and tool
versions. Run focused tests first, then its ordinary pre-commit/PR checks.
For Graph OS, its release workflow is the executable source for the pinned
sibling revisions and source-verified test environment. Release-tag
dependency-readiness and live/credentialed checks have different prerequisites;
report them separately and do not claim they passed during an ordinary PR.
Do not weaken deterministic correctness, security, or code-quality failures.

The implementation and test evidence must cover the relevant spec requirements,
including failure behavior, interface compatibility, and existing wiring. Run
the owning repository's configured CCCC, `jscpd`, Dupehound, and KISS checks
where applicable; cite their actual commands and output, including any absent
tool or unsupported local stage. Do not invent a pass or a threshold.
