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

Throttling starts **observe-only**. Every fleet child gets an error budget.
When a child declares none, it gets the default: capacity equal to its
`max_concurrency`, a 5% error budget, recovery at or below 1% errors, 20
samples per window, halve on a burst, give back one slot per healthy window,
floor 1, and a one-minute cooldown. In `observe` mode the ceiling is computed,
recorded in EG, logged and exported, but the child's admission is never
limited.

Enforcement is a per-child opt-in. A child declares `mode: "enforce"`
in its `error_budget`:

```json
{
  "error_budget": {
    "capacity": 8,
    "mode": "enforce",
    "policy": {
      "error_budget_ppm": 50000, "recovery_ppm": 10000, "min_samples": 20,
      "decrease_per_mille": 500, "increase_step": 1, "floor": 1,
      "cooldown_ms": 30000
    }
  }
}
```

An invalid declaration is logged and the child falls back to the observe-only
default. It is never enforced.

Once a minute the controller does the following for every child:

1. It reads the child's requests and errors for the last 60 seconds from
   Prometheus (`SCALING_PROMETHEUS_URL`). The counter is
   `agent_utilities_mcp_child_calls_total`. Timeouts and transport failures
   count as errors. Calls this service shed itself count as neither.
2. It sends the window to EG `ThrottleCapacityCell` on the cell
   `fleet/child/<name>`.
3. It exports the ceiling EG returns as
   `agent_utilities_mcp_child_throttle_ceiling{server,mode}`.
4. For an `enforce` child only, it bounds new admissions by that ceiling.
   Calls already in flight finish. Switching a child back to `observe` lifts
   its ceiling.

EG narrows the ceiling multiplicatively on an error burst. It gives the
ceiling back additively, and only on healthy windows. The ceiling never goes
above the declared capacity or below the floor. Every step is recorded on the
cell and in the graph audit chain.

A failed step keeps the last ceiling and never widens it. With no Prometheus
URL configured, nothing is observed or throttled. The process identity needs
`capacity:throttle` to send windows and `capacity:admin` to declare cells.

## Guardrail-profile evolution

A declaration may add `bounds`: a ladder of `levels` profiles running from
`loosest` to `tightest` over the four AIMD knobs. Each profile sets
`error_budget_ppm`, `recovery_ppm`, `decrease_per_mille` and `increase_step`.
The declared policy must sit on the ladder.

Evolution applies only to `enforce` children. Every 15 windows, EG `Decide`
proposes whether each such bounded child's profile
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
