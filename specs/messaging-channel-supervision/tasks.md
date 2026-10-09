# Messaging channel supervision lifecycle — tasks

Status: **READY FOR IMPLEMENTATION**. Delivery: **BUILDING**. See [design](plan.md) and [tests](test-spec.md).

- [x] Define `ChannelSupervisionState`, the legal-transition table, and `transition()`/`IllegalSupervisionTransition` in `graph_os/messaging/supervision.py` (`GRAPHOS-MESSAGING-R001.1`).
- [x] Cover every legal transition and a representative illegal set in `tests/messaging/test_supervision.py` (`MSG-T01`, `MSG-T02`).
- [x] Wire `InboundRouter.start()`/`_supervise_backend()`/`stop()` to call `transition()` and expose a per-backend state read (`GRAPHOS-MESSAGING-R001` rollup, `GRAPHOS-MESSAGING-R002`, `GRAPHOS-MESSAGING-R004`).
- [ ] Wire `MessagingRegistry.create_all_enabled` to record a typed `DEGRADED` entry for a missing/import-failed adapter instead of only logging (`GRAPHOS-MESSAGING-R005`).
- [ ] Add the backoff-sequence and healthy-reset tests against the live router (`GRAPHOS-MESSAGING-R003`).
- [ ] Add the static import-boundary check (`GRAPHOS-MESSAGING-R006`, `MSG-T08`).
- [x] Run the full test-spec, record exact default-branch evidence, then mark `GRAPHOS-MESSAGING-R001` (parent) accepted.
