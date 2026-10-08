# Delivery tasks and evidence

- [x] Implement typed deployment profile, preflight, doctor, release-canary and production-ops modules. Evidence: `graph_os/deployment/` and `tests/deployment/`. **Source implemented; independent install and first-boot acceptance pending.**
- [ ] Add and verify the fresh-checkout development path, including a disposable engine and clear optional component selection.
- [ ] Complete migration of deployment modules and scripts from agent-runtime internals onto supported public ports.
- [ ] Render and validate both Compose and Kubernetes profiles from one closed schema; reject plaintext secrets and floating production images.
- [ ] Add positive and negative first-boot/rollback tests using provisioned CI resources and reference-only credentials.
- [ ] Audit hook installation and required CI gates; make all ordinary PR gates reproducible in a cloud checkout.
- [ ] Publish usage to the GraphOS GitHub Pages site and link the contribution guide to this spec and a verified setup command.
- [ ] Record exact commit, package hashes, CI runs, profile digests and redacted functional receipts; then promote individual deliverables to Verified/Released/Accepted.

**Current evidence gap:** The existing source and test names establish implementation progress. They do not establish a public clean-install, profile matrix or first-boot acceptance receipt for this complete contract.

## R013 landed evidence and remaining acceptance

Evidence reconciled against [main `937d38569cef9f07abf46fd064aff98c24a33c85`](https://github.com/Knuckles-Team/graph-os/commit/937d38569cef9f07abf46fd064aff98c24a33c85).
Each merge below is an ancestor of that revision. These receipts establish
bounded source and CI progress. **GRAPHOS-DEPLOY-R013 remains BUILDING; the
spec remains NOT_AUDITED.** The other requirements retain their existing states;
the R005 WebUI rename dependency remains separate and open.

- [x] Land Kubernetes preflight client checks:
  [PR #23](https://github.com/Knuckles-Team/graph-os/pull/23),
  merge `2abb9ebb3905e2daedc3e4c5e83dc5682c13f2bd`,
  [CI 37174546666](https://github.com/Knuckles-Team/graph-os/actions/runs/37174546666)
  at `7694c6eb891a23186805bc8cb62c531fc642ee5f`.
  The [preflight tests](../../tests/deployment/test_preflight.py) cover
  `dev`, `test`, `prod`, `enterprise`, validated named targets, missing
  kubectl/Helm, failed/timed-out probes and invalid profiles. Successful client
  probes leave cluster access and first boot unverified.
- [x] Land the [Kubernetes observation verifier](../../graph_os/deployment/kubernetes_first_boot.py)
  and [offline tests](../../tests/deployment/test_kubernetes_first_boot.py):
  [PR #24](https://github.com/Knuckles-Team/graph-os/pull/24),
  merge `0da5f7440603cd3112bf25dc091d84a9a902fb1d`,
  [CI 37175887401](https://github.com/Knuckles-Team/graph-os/actions/runs/37175887401)
  at `80a4c66e071862f1335f2e3487090005f728d62e`.
  It checks ownership, rendered/observed configuration, image digests, pod
  readiness and PVCs with bounded read-only collection. Missing or mismatched
  engine/identity/administrator/secrets evidence stays blocked. Supplied
  snapshot-bound flags are not authenticated application proof:
  `acceptance` is always `not_qualified`, including when the CLI exits zero.
- [x] Land extension pull-secret references and configured TCP-port rules:
  [PR #25](https://github.com/Knuckles-Team/graph-os/pull/25),
  merge `e9d99298003518d7b54f33a9854b48e471f6926c`,
  [CI 37173473826](https://github.com/Knuckles-Team/graph-os/actions/runs/37173473826)
  at `7d700fc7cefca8c680bb5879cca71a1d1ff3a5e3`.
  The [Helm fixtures](../../tests/deployment/test_helm_extensions.py) cover
  unified sidecar/child and shared-engine renders, absent/configured pull
  secrets, custom/shared extension ports (including 9123), and disabled
  NetworkPolicy. The extension-port rule selects all release pods and admits
  those ports from the same namespace; external ingress peers retain only
  core ports and egress rules are unchanged. CI provisions SHA-256-verified
  Helm 4.3.0. This is render proof, not registry-pull or CNI proof.

All three cited runs passed gates (normal commit/push hooks, locked-environment
mypy and full pytest), scanner quality and wheel build. Release-tag dependency
readiness and publication were skipped. A built wheel is not an independent
installed-package or deployment acceptance receipt.

The following checks remain open and require a separately authorized
qualification environment; this documentation phase performs none of them.

- [ ] **Profile matrix:** independently provision both profiles required by
  [R013](requirements.md): development on single-host Compose with none/local
  identity, and production on Kubernetes/Helm with external/local identity,
  proving each claimed mode once its owning contract is available. Record
  profile name/version and digest, chart/Compose revision, resolved render
  digest, exact release/package hashes, image digests, engine sidecar contract
  and selected secrets backend. The Kubernetes preflight names and the
  child/shared render fixtures do not substitute for this R013 profile matrix
  or its sidecar requirement.
- [ ] **Readiness before traffic:** prove real engine and application readiness,
  the required first administrator/bootstrap authority and the selected
  secrets backend before admission. Missing/unhealthy engine, missing admin,
  unresolved secret reference or a failing functional probe must keep traffic
  unaccepted. Record one durable engine writer and the actual persistent volume;
  pod/TCP readiness alone is insufficient.
- [ ] **Identity and default policy:** qualify the selected mode through the
  served authority described by
  [GRAPHOS-IDENTITY-001](../identity-access/spec.md) and its
  [requirements](../identity-access/requirements.md).
  Prove none-mode loopback/Host/Origin and exposure guards, local first-admin
  setup where selected, and external-provider sign-in and principal/scope
  mapping where selected. Prove the same default-policy allow/deny decision
  through MCP and API, with no administrative or approver authority granted to
  a service identity. Missing identity authority remains blocked; chart values,
  process health and verifier input flags cannot qualify an identity mode.
- [ ] **Selected secrets:** demonstrate runtime resolution through the declared
  backend using references only, plus refusal for an absent/unresolvable
  required reference or unavailable backend. Keep values, tokens and private
  endpoints out of receipts, configuration, logs and traces.
- [ ] **Real image pulls:** on the selected Kubernetes release, demonstrate a
  fresh authenticated registry pull of each selected connector/component's
  pinned image using the configured pull-secret references, and match observed
  imageIDs to the intended digests. In a disposable negative case, missing or
  invalid credentials must prevent readiness; a cached image or rendered
  Secret name is insufficient proof.
- [ ] **Live CNI enforcement:** record the enforcing CNI and applied policy,
  then prove same-namespace access to configured extension TCP ports (including
  9123), denial from an unallowed namespace, and denial of unconfigured ports.
  Exercise the rule's actual release-wide pod selector. Confirm that configured
  external ingress peers retain only core-port access and that only the
  declared same-namespace, DNS and explicit extra egress paths are permitted.
  A rendered NetworkPolicy does not prove enforcement.
- [ ] **Functional and recovery receipts:** follow the applicable
  [first-boot verification steps](../../graph_os/skills/graphos-deployment/references/first-boot-verification.md)
  with the selected components: authenticated MCP initialization/discovery and
  an allowed call, the same API result and an unauthorized refusal, and a real
  browser sign-in/sign-out when WebUI is selected. Preserve the test contract's
  idempotent governed-action, engine persistence across restart, isolated
  restore and failed-startup/rollback evidence. Bind every redacted receipt to
  the exact profile/render/release and leave unsupported capability checks open.
