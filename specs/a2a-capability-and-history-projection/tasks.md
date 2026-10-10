# A2A streaming, push and transition-history projection — tasks

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED** except `GRAPHOS-A2A-R001.1`. See [design](plan.md) and [tests](test-spec.md).

- [x] Define `A2AAgentCapabilities.from_wired_authorities` plus `A2AStreamingAuthority`/`A2APushNotificationAuthority`/`A2ATransitionHistoryAuthority` runtime-checkable protocols in `graph_os/a2a/models.py` (`GRAPHOS-A2A-R001`, `GRAPHOS-A2A-R001.1`). Default capabilities stay all-false; a non-conforming authority object raises `TypeError` instead of being silently accepted. Proven by `tests/a2a/test_models.py`.
- [ ] Wait for `GRAPHOS-A2A-002` (context-budget subset) to land; do not implement the streaming cursor or push-trigger machinery ahead of it (`GRAPHOS-A2A-R006`).
- [ ] Implement `message/stream` reading agent-utilities' WorkItem event stream (`AU-CONTROL-001`) through its public port; no local event-log copy (`GRAPHOS-A2A-R002`).
- [ ] Implement `tasks/get` history read-through to epistemic-graph's durable kernel (`EG-DURABLE-KERNEL`) (`GRAPHOS-A2A-R003`).
- [ ] Add the `A2ATransitionHistoryUnavailable` typed refusal for an absent/unreachable history authority (`GRAPHOS-A2A-R004`).
  - [x] `GRAPHOS-A2A-R004.1`: landed inventory only — the protocol exists, the error type does not. Proven by `tests/a2a/test_models.py`.
  - [ ] `GRAPHOS-A2A-R004.2`: add the `A2ATransitionHistoryUnavailable` error type to `graph_os/a2a/authority.py`.
  - [ ] `GRAPHOS-A2A-R004.3`: wire `tasks/get`'s refusal (`A2A-HT06`); blocked on `GRAPHOS-A2A-002` (`GRAPHOS-A2A-R006`).
- [ ] Implement push-notification delivery over epistemic-graph's durable outbox with dead-letter reporting (`GRAPHOS-A2A-R005`).
- [ ] Wire real authorities into composition only after each path is proven; run all test-spec rows; record exact revision evidence before flipping any capability field `true` and marking its requirement accepted (`GRAPHOS-A2A-R007`).
