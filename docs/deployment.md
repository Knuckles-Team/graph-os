# Deployment and configuration

GraphOS ships a local MCP server, an authenticated HTTP transport, configuration
and diagnostic tools, and guarded production operations. Choose a profile,
validate it, and run the release canary before changing live traffic.

## Install

GraphOS supports Python 3.12 through 3.14.

```bash
python -m pip install graph-os
```

Add the optional Agent WebUI host integration when the same process will serve
the UI:

```bash
python -m pip install "graph-os[webui]"
```

## Generate configuration

The configuration tool supports three deployment profiles:

| Profile | Intended use |
|---|---|
| `tiny` | Local evaluation and a minimal single-process environment |
| `single-node-prod` | A production host with externalized persistence and identity |
| `enterprise` | A managed multi-service deployment with production policy controls |

Generate and validate a profile:

```bash
setup-config generate --profile tiny
setup-config doctor --profile tiny
```

The default output follows the XDG configuration directories. Generated
configuration must contain secret references such as `vault://` or
`engine://__secrets__`; do not place plaintext credentials in configuration,
examples, command history, or source control.

Inspect the complete option reference without writing configuration:

```bash
setup-config reference
```

## Container and Kubernetes deployment

The source tree ships three deployment artifacts. Each consumes an
operator-built image that carries GraphOS and the epistemic-graph engine
binary at matching releases, pinned by digest.

| Artifact | Use |
|---|---|
| `deploy/compose/compose.yaml` | One host with Docker Compose or Podman: a single GraphOS service that supervises its engine, one data volume, ports published on loopback |
| `deploy/swarm/stack.yml` | Docker Swarm: the same single GraphOS service as Compose, one replica with stop-first updates, pinned to the node that holds the data, secrets as Swarm secrets, overlay network |
| `deploy/helm/graph-os` | Kubernetes: GraphOS with its engine as a native sidecar (or child) in one pod, or a shared engine StatefulSet; namespaced RBAC, split API and web UI ingress, NetworkPolicy |

Validate before use:

```bash
docker compose -f deploy/compose/compose.yaml config --quiet
docker stack config -c deploy/swarm/stack.yml
helm lint deploy/helm/graph-os --strict
helm template graph-os deploy/helm/graph-os --namespace <namespace> --values <values>
```

The engine store has exactly one writer: never scale the unified service, pod,
or Swarm service past one replica. The bundled operator skills `graphos-genesis`
(substrate) and `graphos-deployment` (installation, identity, verification) walk
through both the development (Compose) and production (Kubernetes/Helm)
profiles end to end, including the forced first administrator, default
authorization policy, secrets backend selection, and first-boot verification.

## Local MCP transport

Use `stdio` when an MCP client launches GraphOS as a child process:

```bash
graph-os --transport stdio
```

The helper can register that portable launcher with Codex:

```bash
setup-config codex
```

For other clients, configure `graph-os --transport stdio` using the client's
standard MCP server configuration format.

## Network transport

Serve the streamable HTTP transport on a private listener with a real token
verifier. This example uses the JWT boundary:

```bash
AUTH_JWT_AUDIENCE=graph-os KG_POLICY_VERSION=baseline-v1 \
  graph-os --transport streamable-http \
  --host 127.0.0.1 --port 8000 \
  --auth-type jwt \
  --token-jwks-uri https://identity.example/.well-known/jwks.json \
  --token-issuer https://identity.example/ \
  --token-audience graph-os
```

Network serving is a security boundary. Configure validated identity, tenant
isolation, TLS, and the deployment's authorization policy before exposing the
listener beyond loopback. GraphOS refuses network serving without a built
authentication provider and rejects required authority that is absent or
invalid.

## Validate a candidate

Validate a signed candidate and emit a deterministic dependency plan with:

```bash
setup-config release-plan candidate.json --trusted-digest 'sha256:<digest>' --profile prod
```

The digest selects the canonical unsigned JSON document; it cannot establish
trust. AU's configured
`CERT_EVIDENCE_VERIFIER_COMMAND` independently authenticates the signed document
before GraphOS consumes it. The signature envelope follows AU's public
`verify_signed_evidence` contract, including algorithm, key ID, signature and
subject digest. Missing or failed verification blocks planning.

Candidate schema version 1 contains `candidate_id`, timezone-aware `created_at`,
`artifacts`, `stages`, and one GraphOS `profiles` binding. Each artifact declares
its component ID, repository, full source revision, immutable digest, image or
wheel kind, exact API/schema contract identifiers, and public GitHub build
receipt. Each stage declares whether it is optional/enabled, its probe contract,
and predecessors with matching API/schema identifiers. Unknown fields,
duplicate IDs, missing enabled predecessors, incompatible contracts and cycles
are refused. Disabled optional stages are excluded from the plan. Ordering uses
declared edges with component-ID tie-breaking.

The profile binding pins both the profile file's SHA256 bytes and its GraphOS
artifact digest. The existing profile loader validates that same material; its
release must be digest-pinned to the selected artifact. The output includes
manifest/profile digests, ordered artifact revisions and public receipts. It
reports `executed: false` and `acceptance: not_audited`: this command does not
apply artifacts, evaluate live readiness, perform rollback, or qualify a
release. Those remaining requirements are tracked by the
[dependency-ordered release specification](https://github.com/Knuckles-Team/graph-os/tree/main/specs/dependency-ordered-release).

Run diagnostics against the resolved deployment configuration, then run the
bounded release canary in the candidate environment:

```bash
agent-utilities-doctor
graph-os-release-canary
```

The canary reports aggregate readiness checks and does not print paths,
credentials, identities, or graph contents. A failed canary is a release
failure, not a warning to suppress.

The host daemon can be inspected independently:

```bash
graph-os-daemon --status
```

## Production operations

`graph-os-production-ops` provides guarded backup and restore-validation
commands. They require the deployment's workload identity, graph coordinator,
encryption, and mounted storage policy. Review the command help in the exact
candidate environment before use:

```bash
graph-os-production-ops --help
```

Production operations emit opaque digests and aggregate counts rather than
endpoints, principals, storage paths, credentials, or graph contents. Backup
and restore validation are operational changes; run them through the normal
change-control and recovery procedure for the target deployment.

## Release model

The release workflow builds the wheel after the quality and scanner jobs pass.
PyPI publication runs only for an explicit version tag and uses the protected
`pypi-publish` environment. A push to `main` updates source and documentation;
it does not publish a package.

See [Capability status](status.md) before deployment. Capability-gated paths
remain unavailable whenever their required authority is absent or unverified.
