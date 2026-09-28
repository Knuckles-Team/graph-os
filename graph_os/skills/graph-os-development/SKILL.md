---
name: graph-os-development
domain: development
skill_type: skill
description: >-
  Develop a feature in any Graph OS ecosystem repository from public GitHub
  checkouts. Use for epistemic-graph, agent-connector-sdk, agent-utilities,
  graph-os, agent-webui, or repository-manager when choosing the owner, preparing
  an isolated source and test environment, or proving a repository-owned spec.
license: MIT
tags: [graph-os, development, public-contribution, specs]
metadata:
  version: '1.0.0'
---

# Graph OS ecosystem development

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
