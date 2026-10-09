# GraphOS — repository guide

GraphOS is the deployable composition layer for the Knuckles agent platform:
MCP/REST composition, MCP fleet supervision, control-plane policy, optional
Agent WebUI hosting, unary A2A, governed browser control, and deployment
operations under `graph_os/`. Public operator documentation lives at
<https://knuckles-team.github.io/graph-os/>.

Current capability limits are documented in `docs/status.md`. A capability
waiting on another repository's public contract must fail closed and remain
marked unavailable. Do not add a compatibility alias, static fallback, or
fabricated receipt to make it appear complete.

## Start here

| Need | Read |
|---|---|
| Architecture boundaries, module map, build-host/environment setup, required quality gates, contract regeneration, identity setup, and branching & isolation for working *in this repository* | [`graph_os/skills/graph-os-repository-development/SKILL.md`](graph_os/skills/graph-os-repository-development/SKILL.md) |
| Choosing which Graph OS ecosystem repository owns a spec, and the public cross-repository contribution flow | [`graph_os/skills/graph-os-development/SKILL.md`](graph_os/skills/graph-os-development/SKILL.md) |
| End-user / operator usage of a running GraphOS | [`graph_os/skills/using-graph-os/SKILL.md`](graph_os/skills/using-graph-os/SKILL.md) |
| Standing up GraphOS (bare metal, Compose, Swarm, Kubernetes) | [`graph_os/skills/graphos-genesis/SKILL.md`](graph_os/skills/graphos-genesis/SKILL.md) |

## Non-negotiables

- GraphOS authenticates, composes, routes, supervises, and projects. Durable
  graph state and RDF/OWL/SHACL semantics belong to `epistemic-graph`; agent
  decisions and workflows belong to `agent-utilities`; source-specific
  transport and effects belong to `agent-connector-sdk` and connector
  services; browser presentation belongs to `agent-webui`. Dependencies point
  toward those authorities through public contracts — never copy their
  implementations here.
- MCP and REST routes share the same application service and authorization
  decision.
- Security is fail closed: validated identity, tenant isolation, and the
  configured TLS/authorization posture are required before dispatch; unknown
  effects never claim rollback.
- Never commit credentials, bearer tokens, private endpoints, operator
  inventories, generated live configuration, or plaintext secrets.
- Never push to `main`. Work on a topic branch in a dedicated worktree (see
  the repository development skill's "Branching & isolation" section) and
  open a pull request; hosted CI (`release.yml`) is the merge gate.

---
*Navigation index — kept lean on purpose. Detailed procedure lives in the
linked skills above; regenerate this file's content there, not here.*
