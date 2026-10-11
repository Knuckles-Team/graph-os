# A2A streaming, push and transition-history projection — test contract

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED** except `GRAPHOS-A2A-R001.1`. Governing [spec](spec.md).

| Test | Requirement | Setup and expected result |
|---|---|---|
| A2A-HT01 | A2A-H01, GRAPHOS-A2A-R001.1 | No authorities supplied: `A2AAgentCapabilities.from_wired_authorities()` returns all-false; `A2AAgentCapabilities()` default also all-false. Landed and proven by `tests/a2a/test_models.py::test_default_capabilities_are_all_false` and `::test_from_wired_authorities_with_none_stays_false`. |
| A2A-HT02 | A2A-H01, GRAPHOS-A2A-R001.1 | A conforming authority object for one capability is supplied: only that field is true, the other two stay false. Landed and proven by `tests/a2a/test_models.py::test_from_wired_authorities_advertises_only_what_is_wired` and `::test_from_wired_authorities_all_three_wired`. |
| A2A-HT03 | A2A-H01, GRAPHOS-A2A-R001.1 | A non-conforming object (missing the protocol's method) is supplied for any of the three authority kwargs: `from_wired_authorities` raises `TypeError`; no field is silently set true. Landed and proven by `tests/a2a/test_models.py::test_from_wired_authorities_refuses_non_conforming_object`. |
| A2A-HT04 | A2A-H02 | With a scripted AU WorkItem event fixture, `message/stream` emits ordered status/step/terminal events matching the fixture; a missing or stale event in the fixture yields a typed `UNAVAILABLE`, not a fabricated event. (Open — depends on `GRAPHOS-A2A`.) |
| A2A-HT05 | A2A-H03 | With a scripted EG durable-kernel fixture, `tasks/get` with history requested returns exactly the fixture's transition ledger. (Open — depends on `GRAPHOS-A2A`.) |
| A2A-HT06 | A2A-H04 | No `A2ATransitionHistoryAuthority` wired, or the wired fixture authority raises on call: `tasks/get` history returns `A2ATransitionHistoryUnavailable` with a privacy-safe message and no history data. (Open.) |
| A2A-HT07 | A2A-H05 | Push target fails once then recovers: delivered once via the durable outbox, surviving a simulated process restart. A target that fails repeatedly past the outbox's configured attempt budget is dead-lettered and reported. (Open.) |
| A2A-HT08 | A2A-H06 | With the `GRAPHOS-A2A` dependency flag unset, composition refuses to wire a real streaming/push/history authority; capability fields stay false regardless of attempted wiring. (Open.) |
| A2A-HT09 | A2A-H07 | Integration fixture exercises a chosen subset of streaming/push/history; a contract test asserts the Agent Card's advertised subset at that revision exactly matches what the fixture actually exercises. (Open.) |

Run `uv run pytest tests/a2a`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy graph_os`, and `uvx --from pre-commit==4.6.0 pre-commit run --files <changed>` for this PR's `GRAPHOS-A2A-R001.1` slice (A2A-HT01–HT03). The remaining rows (A2A-HT04–HT09) require `GRAPHOS-A2A` and live AU/EG fixtures and stay open until implemented. Capture exact commit, command, versions and result in a local `evidence.md` before changing delivery to `ACCEPTED`.
