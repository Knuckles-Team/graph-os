# GRAPHOS-INGRESS-001 — Implementation tasks

Status: SPECIFIED. Governing [spec](spec.md) and [plan](plan.md).

`GRAPHOS-INGRESS-R001` is sliced per the rapid-delivery contract (size score
above 6, more than 2 code roots): `GRAPHOS-INGRESS-R001.1` (this slice) is the
typed public-ingress profile model plus its load-time validation and refusal
tests, standalone from `EnvironmentProfile`. `.2`+ wire it into the existing
profile/gateway/MCP identity path and the cutover/rollback behavior.

- [x] `GRAPHOS-INGRESS-R001.1` — Typed `PublicIngressProfile` model (version,
  public_origin, tls_secret_ref, callback_uri, trusted_proxy) with closed-schema
  validation and refusal tests for T-IN-01/T-IN-02 in
  `graph_os/deployment/public_ingress_identity.py` +
  `tests/deployment/test_public_ingress_identity.py`. Standalone: not yet wired
  into `EnvironmentProfile`, the gateway, or MCP identity admission.
- [ ] Inventory current profile, config generator, doctor, gateway, MCP and identity routes; record exact reusable interfaces and any missing fields.
- [ ] Extend the single closed profile and render path with canonical origin, TLS reference, exact callback and trusted proxy contract; add migration validation if keys change.
- [ ] Wire ingress settings to the existing GraphOS gateway and MCP identity admission; keep application and engine authorization unchanged.
- [ ] Implement explicit time-bounded safe redirect policy and rollback input; remove any superseded host-specific shortcuts.
- [ ] Build local TLS proxy and mock OIDC fixtures; prove all T-IN positive and negative cases, including no downstream effect on denial.
- [ ] Capture exact revision, wheel, scanner and served receipts; update public deployment instructions and `status.json` only after the corresponding proof exists.
