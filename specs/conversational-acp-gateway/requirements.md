# GRAPHOS-ACP-001 requirements

| ID | Requirement | Verification |
|---|---|---|
| `GRAPHOS-ACP-R001` | **Conversational ACP gateway session admission and policy.** GraphOS admits an ACP session only for a verified principal from the existing identity path, binds the session immutably to that principal/tenant/scope snapshot, enforces configured idle and absolute expiry limits, and returns one typed `chat_adapter_unavailable` refusal for admission and messages whenever the agent-utilities chat adapter is absent or unreachable, without a local reply or REST fallback. | Unit and integration tests cover admission refusal for missing/invalid credentials, immutable binding, idle-expired refusal, absolute-expired refusal, the typed adapter-unavailable refusal on both admission and message, identical principal/tenant/scope projection against the REST path, and a status/doctor read that reports adapter and limit state without secrets. |
