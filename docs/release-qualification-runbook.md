# Release-qualification runbook

Companion procedure to the organization's exit-criteria matrix
(<https://knuckles-team.github.io/.github/build-first.html>). Each stage
below names the exact automated check or script a reviewer runs directly;
none depend on private infrastructure. Run the stages in order — a stage
is qualified only once every check it names passes on the revision being
released.

A failing stage stops qualification of this release candidate at that
stage. Record the exact revision, command, and result for each stage you
run; do not mark a later stage qualified from an earlier, different
revision.

## 1. Graph engine (`epistemic-graph`)

```bash
gh run list --workflow=release.yml -R Knuckles-Team/epistemic-graph --branch main --limit 1
```

The listed run must be the one built from the commit this release pins.
Re-run it directly with `gh run rerun <run-id> -R Knuckles-Team/epistemic-graph`
if it needs re-proving.

## 2. Connector SDK (`agent-connector-sdk`)

```bash
gh run list --workflow=release.yml -R Knuckles-Team/agent-connector-sdk --branch main --limit 1
```

Same proof shape as stage 1, against the revision this release's
`agent-connector-sdk` dependency pins.

## 3. Agent runtime (`agent-utilities`)

```bash
gh run list --workflow=release.yml -R Knuckles-Team/agent-utilities --branch main --limit 1
```

Same proof shape as stage 1, against the revision this release's
`agent-utilities` dependency pins.

## 4. GraphOS itself

From a clean GraphOS checkout at the candidate revision, after
`scripts/bootstrap.sh`:

```bash
uvx pre-commit run --all-files
uvx pre-commit run --hook-stage pre-push --all-files
uv run --no-sync pytest tests/deployment
uv build --wheel --out-dir dist
```

Install the built wheel into a fresh environment and certify it is the
one that will be promoted:

```bash
graph-os-release-canary
```

`graph-os-release-canary` fails closed (nonzero exit, no partial pass) when
a declared console entry point, the packaged engine binary, the folded
numeric kernel, or the served FastMCP major is missing or mismatched — see
`graph_os/deployment/release_canary.py`.

## 5. Web UI (`agent-webui`, pending `GRAPHOS-DEPLOY-R005` rename)

```bash
gh run list --workflow=release.yml -R Knuckles-Team/agent-webui --branch main --limit 1
```

Same proof shape as stage 1. The optional `webui` extra's package name
changes once `GRAPHOS-DEPLOY-R005` lands in both `agent-webui` and here;
this stage's check does not depend on that name.

## 6. Connector package fleet

```bash
agent-utilities-doctor --only mcp_fleet mcp_fleet_secrets --live
```

Both checks must report healthy for every connector package this release's
fleet catalog declares; `mcp_fleet` proves live reachability
(`--live`), `mcp_fleet_secrets` proves each declared secret reference
resolves. See `graph_os/deployment/doctor.py`'s `CHECKS` table.

## 7. Production redeployment

```bash
graph-os-production-ops backup --archive-root <path>
graph-os-production-ops restore-validate --archive-root <path> --scratch-root <path>
```

A qualified release must restore-validate from its own backup before
redeployment proceeds. See `graph_os/deployment/production_ops.py`.

## Scope and limits

This runbook proves each stage's own automated check; it is not itself a
live first-boot, identity, image-pull, or CNI-enforcement acceptance
receipt. Those remain tracked separately under `GRAPHOS-DEPLOY-R013` in
`specs/portable-deployment/tasks.md`.
