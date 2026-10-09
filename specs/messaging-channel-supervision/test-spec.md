# Messaging channel supervision lifecycle — test contract

Status: **READY FOR IMPLEMENTATION**. Delivery: **BUILDING**. Governing [spec](spec.md).

| Test | Requirement | Setup and expected result |
|---|---|---|
| MSG-T01 | GRAPHOS-MESSAGING-R001.1 | For every pair `(current, target)` in the legal-transition table, `transition(current, target)` returns `target`. |
| MSG-T02 | GRAPHOS-MESSAGING-R001.1 | For a representative illegal set (`STOPPED -> RUNNING`, `DEGRADED -> RUNNING`, `STOPPED -> BACKING_OFF`, `STOPPING -> RUNNING`), `transition(current, target)` raises `IllegalSupervisionTransition` naming both states. |
| MSG-T03 | GRAPHOS-MESSAGING-R001 | (future slice) `InboundRouter` reports live per-backend state matching the fixture's observed lifecycle. |
| MSG-T04 | GRAPHOS-MESSAGING-R002 | (future slice) An unconnected fixture backend never reaches `RUNNING`; a connected one does. |
| MSG-T05 | GRAPHOS-MESSAGING-R003 | (future slice) Simulated failures produce a capped, doubling delay sequence; a sustained healthy run resets it to base. |
| MSG-T06 | GRAPHOS-MESSAGING-R004 | (future slice) `stop()` during a backoff wait drains with no further restart. |
| MSG-T07 | GRAPHOS-MESSAGING-R005 | (future slice) A missing adapter surfaces `DEGRADED` with a typed reason; other configured channels still start. |
| MSG-T08 | GRAPHOS-MESSAGING-R006 | A static import check over `graph_os/messaging/{router,registry,service}.py` confirms no agent-utilities import outside `agent_utilities.messaging.{base,models}` and `agent_utilities.core.config`. |

This change lands `MSG-T01` and `MSG-T02` only, in `tests/messaging/test_supervision.py`. Run `uv run pytest tests/messaging/test_supervision.py -q`, `uv run ruff check graph_os/messaging/supervision.py tests/messaging/test_supervision.py`, and `uv run mypy graph_os/messaging/supervision.py`. `MSG-T03`–`MSG-T08` remain open under their `SPECIFIED` requirements and are not claimed delivered by this change.
