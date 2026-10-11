# GRAPHOS-INGRESS — Test specification

Status: SPECIFIED. Governing [spec](spec.md). All evidence is PENDING; publishing this test contract is not a test pass.

| Test ID | Requirement | Level | Fixture and input | Expected observation |
|---|---|---|---|---|
| T-IN-01 | IN-01 | Unit | Complete generic HTTPS profile, local OIDC metadata, reference-only secrets | Deterministic redacted render with exact host, callback and profile digest. |
| T-IN-02 | IN-01 | Unit | Missing TLS ref, malformed callback, HTTP non-loopback, unknown field | Each refuses with section/key diagnostic; no secret value appears. |
| T-IN-03 | IN-02 | Integration | Local TLS proxy with configured trust, authenticated request | GraphOS gateway reaches existing identity middleware and expected application route. |
| T-IN-04 | IN-02 | Integration | Untrusted proxy sends forged forwarded host/scheme | Effective authority stays with transport Host or request is rejected. |
| T-IN-05 | IN-03 | Contract | Valid OIDC token, exact Host/Origin, tenant grant | Authorized read succeeds and engine sees verified principal/scope. |
| T-IN-06 | IN-03 | Contract | Missing/expired token, wildcard Host, wrong Origin, cross-tenant grant | 401/403 or profile refusal before mutation; no anonymous retry. |
| T-IN-07 | IN-04 | Integration | Explicit old/new origins, both callbacks, valid expiry | Only safe browser navigation redirects to new origin during the window. |
| T-IN-08 | IN-04 | Integration | Old API POST, credential-bearing URL, expired redirect | No redirect, no credential leak, no effect. |
| T-IN-09 | IN-05 | CI | Clean checkout, disposable loopback proxy and fake OIDC issuer | Required tests pass with no network host, private domain or live secret store. |
| T-IN-10 | IN-06 | Served | Exact image digest, generic test domain, local IdP, authenticated MCP and denied request | Receipt records TLS/issuer/callback, `tools/list`, denial, revision and no token/secret. |

## Release proof

Record the exact commit, CI URL, `uv run pytest` result, `uv build --wheel` artifact digest, cccc/KISS/Dupehound/jscpd scanner results and hosted probe receipt separately. A source test cannot establish DNS, TLS, callback or served acceptance. Negative tests must inspect both HTTP result and absence of downstream engine effect. If a test is skipped, record its unmet fixture and keep acceptance open.
