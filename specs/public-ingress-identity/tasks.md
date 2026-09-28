# GRAPHOS-INGRESS-001 — Implementation tasks

Status: SPECIFIED. Governing [spec](spec.md) and [plan](plan.md).

- [ ] Inventory current profile, config generator, doctor, gateway, MCP and identity routes; record exact reusable interfaces and any missing fields.
- [ ] Extend the single closed profile and render path with canonical origin, TLS reference, exact callback and trusted proxy contract; add migration validation if keys change.
- [ ] Wire ingress settings to the existing GraphOS gateway and MCP identity admission; keep application and engine authorization unchanged.
- [ ] Implement explicit time-bounded safe redirect policy and rollback input; remove any superseded host-specific shortcuts.
- [ ] Build local TLS proxy and mock OIDC fixtures; prove all T-IN positive and negative cases, including no downstream effect on denial.
- [ ] Capture exact revision, wheel, scanner and served receipts; update public deployment instructions and `status.json` only after the corresponding proof exists.
