# GRAPHOS-ACP-001 — Design and implementation plan

Status: BUILDING. Governing [spec](spec.md).

## Existing system and reuse

GraphOS already has one principal/tenant/scope resolution path consumed by REST and MCP ([GRAPHOS-IDENTITY-001](../identity-access/spec.md)); ACP admission must call that same path rather than re-implement verification. `graph_os.identity` is the existing owner of session and principal types; `graph_os.gateway` is the existing owner of serving composition. No ACP session store exists yet; `R001.1` adds the smallest typed model and admission check, not a transport.

## Admission and binding

A session-policy model records the configured idle and absolute limits and the current chat-adapter availability. An admission check takes the resolved principal (or its absence) and the adapter-availability flag, and returns one of: admitted (with an immutable principal/tenant/scope snapshot and both expiry timestamps computed at admission), a typed unauthenticated refusal, or the typed `chat_adapter_unavailable` refusal. No field of the admitted snapshot is mutable after admission; a later call must open a new session rather than widen an existing one.

## Idle and expiry policy

Idle expiry is `last_activity_at + idle_limit`; absolute expiry is `admitted_at + absolute_limit`. A touch/use call evaluates both against the current time before treating a session as usable and refuses with a distinct typed reason for each; it never resets the absolute limit and only advances `last_activity_at` on success.

## Adapter-unavailable refusal

The admission check and the per-message check both consult the same adapter-availability input and return the identical typed refusal code when it is unavailable, before any session is admitted or any reply is attempted. This is the explicit, intentional behavior while agent-utilities' chat adapter does not yet exist or is down — not an error path to be hidden.

## Live integration path

`R001.1` ships the typed model and admission check with no transport wiring; a later child slice wires it behind the actual ACP endpoint once agent-utilities' adapter is reachable. Existing identity resolution and status/doctor reporting are the integration points; no new identity issuer or session cookie is introduced.

## Quality and release gates

Focused tests run via `uv run pytest` against the new module only. Applicable hooks are the repository's existing `complexity-staged`, `kiss-staged`, `dupehound-changed` and `jscpd-differential` fast-subset checks on the changed files. No duplicated identity-resolution or session-store implementation; reuse `graph_os.identity` types where a shared shape already exists.
