# Access elevation and adaptive guardrails

This page covers three related controls:

- just-in-time access elevation;
- error-budget throttling of fleet children;
- guardrail-profile evolution.

epistemic-graph (EG) holds the authority for all three. This service
composes the surfaces, observes the signals and applies EG's answers.

## Just-in-time access elevation

An elevation is time-boxed read or write access to one named graph, for the
identity that asks for it. It grants nothing until a different person approves
it. Its window starts at approval and ends on its own. It can be revoked at any
time. EG checks it at the graph-access chokepoint, audits every use and every
transition, and never lets an elevation reach administration.

| Surface | Request | List | Revoke | Approve |
|---|---|---|---|---|
| `graph_elevation` (MCP) and `POST /graph/elevation` | yes | yes | yes | no |
| Agents in chat (through `graph_elevation`) | yes | yes | yes | no |
| A2A `elevation/request`, `elevation/list`, `elevation/revoke` | yes | yes | yes | refused (`-32004`) |
| Operator console, `POST /elevations/approve` | — | — | — | yes |

Approval is a person's decision and is never exposed as a tool. The approval
body names only `elevation_id` and the `request_digest` of the request the
operator reviewed. The approver is always the signed-in session. The request
is refused before it reaches EG when any of these holds:

- the session acts through a delegation, for example an agent acting on a
  person's behalf (`ELEVATION_APPROVER_DELEGATED`);
- the session lacks the exact `rbac:approve-elevation` scope. `*`,
  `kg:admin` and `rbac:*` do not stand in for it (`ELEVATION_APPROVER_SCOPE`);
- the approver is the requester (`ELEVATION_SELF_APPROVAL`);
- the request changed after the operator saw it (`ELEVATION_STALE_VIEW`).

EG then applies its own two-person, exact-digest, one-shot and hard-expiry
rules. Never grant `rbac:approve-elevation` to a service identity.

## Error-budget throttling

A fleet child opts in by declaring an `error_budget` in its configuration.
The declaration sets a capacity and an AIMD policy, and may add evolution
bounds:

```json
{
  "error_budget": {
    "capacity": 8,
    "policy": {
      "error_budget_ppm": 50000, "recovery_ppm": 10000, "min_samples": 20,
      "decrease_per_mille": 500, "increase_step": 1, "floor": 1,
      "cooldown_ms": 30000
    }
  }
}
```

Once a minute the controller does the following for every declaring child:

1. It reads the child's requests and errors for the last 60 seconds from
   Prometheus (`SCALING_PROMETHEUS_URL`). The counter is
   `agent_utilities_mcp_child_calls_total`. Timeouts and transport failures
   count as errors. Calls this service shed itself count as neither.
2. It sends the window to EG `ThrottleCapacityCell` on the cell
   `fleet/child/<name>`.
3. It bounds the child's new admissions by the ceiling EG returns. Calls
   already in flight finish.

EG narrows the ceiling multiplicatively on an error burst. It gives the
ceiling back additively, and only on healthy windows. The ceiling never goes
above the declared capacity or below the floor. Every step is recorded on the
cell and in the graph audit chain.

A failed step keeps the last ceiling and never widens it. With no Prometheus
URL configured, no child is throttled. The process identity needs
`capacity:throttle` to send windows and `capacity:admin` to declare cells.

## Guardrail-profile evolution

A declaration may add `bounds`: a ladder of `levels` profiles running from
`loosest` to `tightest` over the four AIMD knobs. Each profile sets
`error_budget_ppm`, `recovery_ppm`, `decrease_per_mille` and `increase_step`.
The declared policy must sit on the ladder.

Every 15 windows, EG `Decide` proposes whether each bounded child's profile
should hold, move one level tighter or move one level looser. The decision
point is `au.guardrail.profile`. It is a policy question, so EG never explores
it. Only moves inside the ladder are offered.

- **Tighten** is applied at once, as a compare-and-swap on the cell epoch.
- **Loosen** is never applied on a proposal. It is filed as an
  `action.approval` (`guardrail.loosen`) in the fleet approval queue. Only the
  console grant route (`POST /fleet/approvals/grant`) may decide it; the agent
  governance tool is refused. It is applied only once that approval is bound
  to the exact plan and cell epoch, so it cannot be replayed later.
- A cell whose policy was set by hand, off the ladder, is left alone.
