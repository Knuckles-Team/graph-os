# A2A context-budget tool-subset admission — test contract

Status: **READY FOR IMPLEMENTATION**. Delivery: **NOT ACCEPTED**. Governing spec: [GRAPHOS-A2A-002](spec.md).

| Test | Requirement | Setup and expected result |
|---|---|---|
| A2A-ADM-T01 | GRAPHOS-A2A-002-R001, GRAPHOS-A2A-002-R001.1 | Construct a `ToolSubsetAdmissionRequest` with a representative agent graph reference and `A2AContextBudget`. Call `admit_tool_subset`. Expect `admitted is False`, `admitted_tools == ()`, `refusal_reason == "EG_ASSEMBLE_UNAVAILABLE"`, and a non-empty `provenance` naming the unavailable EG operation. |
| A2A-ADM-T02 | GRAPHOS-A2A-002-R004 | For both the refused result in A2A-ADM-T01 and any future admitted result, assert the decision's `provenance` field is non-empty and, on refusal, `refusal_reason` is one of the typed enumeration values. |
| A2A-ADM-T03 | GRAPHOS-A2A-002-R002 (open) | Once an EG assembly client is injectable: a scripted committed `AgentAssemble` answer covering the request's agent graph and budget yields progress past `EG_ASSEMBLE_UNAVAILABLE`; a scripted abstention or unavailable answer still refuses with that same reason. |
| A2A-ADM-T04 | GRAPHOS-A2A-002-R003 (open) | Once wired: given a committed EG answer but an envelope lacking the allowed-tool-subset field, refuse with `ENVELOPE_LACKS_ALLOWED_TOOL_SUBSET`. Given both, admit with `admitted_tools` equal to the EG answer's subset and `provenance` naming the EG decision record and the signed envelope. |
| A2A-ADM-T05 | GRAPHOS-A2A-002-R005 | A scripted partial or stale upstream answer (fewer tools than the agent graph requires, or an expired decision record) refuses; it never reduces to a smaller admitted subset. |
| A2A-ADM-T06 | GRAPHOS-A2A-002-R006 | `uv run pytest tests/a2a/test_admission.py` passes from a fresh public clone with no external network, private sibling checkout, or live identity provider. |

Run `uv run pytest tests/a2a/test_admission.py`, `uv run ruff check graph_os/a2a/admission.py tests/a2a/test_admission.py`, `uv run ruff format --check graph_os/a2a/admission.py tests/a2a/test_admission.py`, and `uv run mypy graph_os/a2a/admission.py`. A2A-ADM-T03 and A2A-ADM-T04 remain open until the EG and AU dependencies (`EG-DECISION-ENGINE-R036`, `AU-CONTROL-R035`) are callable; this PR lands A2A-ADM-T01, A2A-ADM-T02, and A2A-ADM-T06 only. Capture exact commit, command, and result before changing delivery to `ACCEPTED`.
