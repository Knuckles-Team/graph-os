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

## Rapid delivery: the 5-minute contract

Specs are already designed. The work is implementation, delivered in very small, continuously landed slices.

**Time and size**
- One agent delivers one PR in 5 minutes or less. No agent or sub-agent runs longer than 5 minutes; an orchestrator dispatches the next slice to a fresh agent.
- Self-timer (hard): run "date +%s > /tmp/agent-start-$$" as your FIRST command, and before every later step check elapsed with "echo $(( $(date +%s) - $(cat /tmp/agent-start-*) ))". At 300 s, stop starting new work. At 480 s, commit what passes, push it, open the PR, report and STOP. Never exceed 600 s. Never wait on CI or background jobs; use `git commit --no-verify` and `git push --no-verify` after running pre-commit on the changed files yourself.
- CI turns around in 5 minutes or less. PR CI runs the fast subset: lint, type check, the spec check, and only the tests and crates the diff touches. Pushes to main run the full suite.
- CI timer (hard): every PR-path job carries "timeout-minutes: ${{ github.event_name == 'pull_request' && 10 || 60 }}", so a PR gate is killed at 10 minutes and a slow gate shows up as a failure instead of a silent queue.
- Never spawn sub-agents from a delivery agent.

**Investigation budget**
- At most 2 minutes and about 10 tool calls of reading before the first edit, and at most about 25 tool calls per PR.
- If the change is not clear by then, ship the `.1` slice (typed model plus refusal test) or skip the row with a one-line note. Do not write deferral essays.
- Trust the orchestrator's evidence and ID list; do not re-verify it.

**Sizing (deterministic)**
- Split a requirement when its size score is above 6, it names more than 2 code roots, or it is cross-repo. Children are `<ID>.<n>`, producer first.
- Net-new work is sliced, never skipped: `.1` typed model plus validation and refusal tests, `.2` the one entry point that uses it, `.3`+ each further behavior.
- Cross-repo moves split into one child per repo: the destination copies the behavior first, then the source switches importers and deletes.

**Specs update themselves (land VERIFIED, not just LANDED)**
- Every commit that delivers a requirement ends with the trailer "Spec: <ID>[, <ID>...]". A commit that touches code without delivering one says "Spec: none (ci|deps|refactor|fix-forward)".
- Every delivered ID gets at least one test bound to it in the same PR. Python: `@pytest.mark.spec("<ID>")`. Rust/TypeScript: a `// spec: <ID>` line directly above the test.
- Never hand-edit `status.json`; it is generated (`spec-status`). Never write placeholder SHAs. If the work is only part of a requirement, decompose it into `<ID>.<n>` children first and deliver whole children.
- Write the marker with the literal ID, never a variable. Put `Spec:` and `Co-Authored-By:` on consecutive lines in the last paragraph of the commit message, or `Spec:` is not read as a trailer.
- Generated status depends on the commit: run "pipelines-hook spec-status --write" again after committing and commit the result.
- The full rules are in "plans/refactor/reconciliation-20261006/SPEC-STATUS-LIFECYCLE.md".

**Delivered means the row's acceptance is met**
- Never delete or stub out behaviour whose replacement is not wired. Never make a caller "fail closed" in place of repointing it. Never substitute a scope, weaken an assertion or add an allowlist entry to pass a gate.
- If the replacement lives in another repository, do not deliver: record the blocker in the row, name the missing capability, and stop. The orchestrator adds the row in the owning repository.
- Never re-add a path that another requirement retired; report the conflict.
- Valid outcomes are DELIVERED (with a bound test), SPLIT, BLOCKED and GAP (a "landed" row that is not really delivered). A half-delivery is not an outcome.

**Where progress shows**
- `https://knuckles-team.github.io/<repo>/spec-delivery/` is rebuilt from `specs/*/status.json` on each push to main. SPECIFIED is written, LANDED is on main, VERIFIED is on main with a bound test, RETIRED is withdrawn.
- Orchestrating a session (resuming, running waves, landing trains): "plans/refactor/reconciliation-20261006/SPEC-DELIVERY-PLAYBOOK.md" and the prompt templates in `lane-prompts/` beside it.

**Before every push**
- Work in a real `git worktree add` from `origin/main`. Never edit a shared checkout, never `git stash`, never `git add -A`, never force-push.
- Run the repo's own pre-commit on the changed files: `uvx --from pre-commit==4.6.0 pre-commit run --files $(git diff --name-only origin/main...HEAD)`. Fix every failure. No `noqa`, `type: ignore`, skip, xfail or whitelist entries to pass a gate.
- Run the targeted tests only, never a full suite. Run the type checker on every changed file, and keep each function at cyclomatic complexity 10 or less.

**Landing**
- Every PR lands within minutes of going green; no PR sits idle. Mechanical conflicts (generated files, Markdown, `status.json`) are resolved automatically by the merge-train tool. Real conflicts are additive in most cases and are resolved, not deferred.
- Many open PRs land together as a merge train (one integration branch, one CI run), built with "plans/tools/l9/eg-bulk-merge.sh".
- Full CI on main catches what the fast subset missed; regressions are fixed forward immediately.
- Landing in this repo: `gh pr merge --auto --merge` when the PR opens. The required checks are the fast PR subset. If the PR goes DIRTY, merge `origin/main` into it and push.

**Report**: at most 8 lines: PR URL, requirement IDs, test result, and any `ID:<main sha>` proof for rows already on main.

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
