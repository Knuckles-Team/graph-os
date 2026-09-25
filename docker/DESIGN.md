# Container delivery boundary

GraphOS is a runnable Python distribution. Its native `graph-os` command serves
the MCP composition over `stdio` or `streamable-http`, and its optional WebUI
co-service is composed by the same process. The wheel and runtime code are
owned by this repository.

This directory does not currently publish an authoritative Dockerfile. Image
construction remains with the deployment owners. The repository does ship
render-only deployment artifacts that consume such an image by reference:
`deploy/compose/compose.yaml` (one host) and the `deploy/helm/graph-os` chart
(Kubernetes). They name no registry and pin nothing; the operator supplies the
immutable image reference. That separation is a
packaging boundary, not an indication that GraphOS is a scaffold or that its
runtime still lives in agent-utilities.

An eventual image definition here must:

- install exact released GraphOS, epistemic-graph, agent-connector-sdk,
  agent-utilities, and optional agent-webui artifacts;
- run the public `graph-os` entrypoint rather than an agent-utilities alias;
- resolve endpoints and credentials from deployment configuration and secret
  references, never from values embedded in the image;
- retain the same fail-closed capability checks documented in
  `docs/status.md`; and
- be adopted atomically by the owning deployment manifests before this
  repository claims container delivery is complete.

Until that image-owner cutover is designed and validated, adding a second
Dockerfile here would create two competing deployment authorities.
