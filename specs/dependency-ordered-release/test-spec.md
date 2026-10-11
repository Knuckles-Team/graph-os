# GRAPHOS-RELEASE — Test specification

Status: SPECIFIED. Governing [spec](spec.md). Evidence remains PENDING.

| Test ID | Requirement | Level | Fixture and input | Expected observation |
|---|---|---|---|---|
| T-RL-01 | RL-01 | Contract | Signed candidate with full revisions and `sha256` artifacts | Canonical manifest digest and reviewable dry-run graph. |
| T-RL-02 | RL-01 | Negative | Mutable tag, malformed digest, bad signature, duplicate component | Refusal before apply with stable error code and no target mutation. |
| T-RL-03 | RL-02 | Unit | Engine → GraphOS → WebUI and optional connector edges | Stable topological order; disabled optional component excluded. |
| T-RL-04 | RL-02 | Negative | Missing required predecessor or cycle | Refusal identifies offending edge; no partial rollout. |
| T-RL-05 | RL-03 | Integration | Disposable target with matching predecessor digests and probes | Each dependent starts only after predecessor readiness and function pass. |
| T-RL-06 | RL-03 | Negative | Stale image, incompatible API, failing function or timeout | Dependent not started; exact stage failure receipt. |
| T-RL-07 | RL-04 | Served | Installed wheel, engine binary, local identity fixture, MCP/REST | Local canary and separate authenticated/denied served receipts tied to candidate. |
| T-RL-08 | RL-05 | Fault injection | Later stage fails after prior stage passes | Scheduling stops; reversible previous digest restored and reprobed, or recovery pending. |
| T-RL-09 | RL-06 | CI and release | Clean checkout fixture and hosted release job | PR job needs no private service; release job attaches digest, integration and consumer evidence. |
| T-RL-10 | RL-07 | Contract | Candidate with fixture secret references and tokens | Receipt has digests, stage order, time and public evidence URL, with no credential or private endpoint. |

## Verification record

For each command and served probe, record exact source commit, candidate manifest digest, artifact digest, environment class, timestamp, CI URL and pass/fail. Run focused manifest/order/rollback tests, full applicable Python checks, cccc/KISS/Dupehound/jscpd gates, build/wheel smoke, and release-only hosted probes. A missing fixture or unavailable hosted environment leaves acceptance open; it does not create a pass.
