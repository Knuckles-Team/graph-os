# docker/ — design note (no Dockerfile yet)

This directory intentionally holds **no Dockerfile and no compose file** at scaffold
time. graph-os is not yet a runnable image: the deployable composition (MCP server,
REST gateway, control plane, fleet gateway, webui hosting) still lives in and is
built from `agent-utilities`, via
`agent-packages/agent-utilities/docker/graphos-unified-kaniko-job.yaml` (the one
canonical kaniko job — see
`inventory/k8s-migration/GRAPHOS-LOCAL-REDEPLOY.md`). Copying that job here now
would be a fake implementation with no code behind it; RF-ADR-009 §2.4/§6 is
explicit that the image build **moves in Migration Wave 5**, alongside the code
it packages, not before.

## What moves here, in W5

- A `docker/Dockerfile` (or a kaniko job manifest, matching whatever the fleet
  convention is by W5) that builds the `graph-os` package built by *this*
  repository's `pyproject.toml`, replacing today's unified AU+webui kaniko build.
- The image continues to be built to the fleet's **private internal
  registry** (never Docker Hub, per the existing convention) — the exact
  registry host/repository name is deployment configuration, not something
  this public repository's docs should hard-code, and is a W5 decision in
  any case.
- `services/graph-os` is updated (RF-ADR-009 §6) to build from this repository's
  image instead of `agent-utilities`' unified image. This scaffold does **not**
  touch `services/graph-os` — that is explicitly out of scope per this lane's
  instructions.

## Why a placeholder note instead of nothing

`docker/` is a fleet convention every `agents/*`/`agent-packages/*` repository
ships (see `agent-packages/CLAUDE.md` "Fleet conventions" — "Every repo ships …
`docker/` …"). This note satisfies that convention's *intent* (a place documenting
how the repository is containerized) without shipping a Dockerfile that builds
nothing, references source that does not exist in this repository yet, or
silently drifts from the real kaniko job it would have been copied from.
