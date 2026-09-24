# Markets: flip alerts, the scan schedule and live-order approval

GraphOS delivers the markets features of the platform without owning any of
their math. The bars and the signal belong to `epistemic-graph`. The flip
explainer agent belongs to `agent-utilities`. Orders go through the
`emerald-exchange` connector. GraphOS composes those pieces, runs the
schedule, delivers alerts, and holds the human approval step in front of every
live order.

Everything these surfaces return is informational only. An alert, a scan row
or an explanation never authorises an order.

## The `graph_finance` tool

`graph_finance` (MCP) and its REST twin `POST /graph/finance` run as the
verified caller on the tenant's EG client.

| Action | What it does |
|---|---|
| `subscribe` | Creates a durable subscription to trend flips. The optional `filter` takes `asset_class`, `timeframe`, `listing_id` and `direction`. |
| `unsubscribe`, `subscriptions` | Cancels or lists the caller's own subscriptions. |
| `alerts` | Returns the caller's inbox of delivered flips, newest first. |
| `track`, `untrack`, `tracked` | Manages which listing and timeframe pairs the schedule keeps current. |
| `backfill` | Runs a full `period` backfill and scan of one series now. |
| `scan` | Returns the latest-state scanner over every scanned series (EG `FinanceMarket.signal_scan`). |
| `explain_flip` | Explains one flip in the caller's inbox: the math first, then claims that each cite gathered news. |
| `propose_order` | Records a live-order proposal for a person to decide. It places nothing. |
| `order_status` | Reads a proposal's state: pending, approved, denied or expired. |

The tool has no approve action.

## Flip alerts

A flip reaches subscribers once per flip record: an emitted flip, a revision
of one, or a retraction.

1. The schedule publishes each record on the EG broker topic exchange
   `finance.flip`. Publishing is idempotent on the record's content-addressed
   id, so a re-run over the same bars enqueues nothing new.
2. Each subscription is a durable queue bound with the owner's filter pattern.
   A flip published while GraphOS is down waits in the queue.
3. The delivery pass writes each message into the owner's inbox with EG's
   atomic create-if-absent. A redelivered message is therefore acknowledged as
   a duplicate. A failed write is requeued, and dead-lettered after five
   attempts.

## The schedule

With `GRAPHOS_FINANCE_SCHEDULE_INTERVAL_S` greater than zero (default `300`),
every tick does the following for each tracked series:

1. It pages bars out of the `emerald-exchange` connector through the served
   fleet. The first tick backfills the full `period`; later ticks fetch a
   recent window.
2. It appends only new or changed bar versions to the EG time-series store.
3. It replays the stored versions through EG `FinanceMarket.signal_replay`,
   publishes the flip records, and stores the latest signal state for the
   scanner.

The tick then runs one delivery pass. A failure on one series does not stop
the others. Set the interval to `0` to turn the schedule off.

## Live orders

A live order has one path, and a person decides it:

1. **Propose.** Any verified caller, agents included, calls
   `graph_finance(action="propose_order", intent=...)`. This issues a
   `finance.order.approval` lease that holds the order and the proposer's
   identity.
2. **Decide.** A signed-in person calls `POST /finance/orders/approve` or
   `POST /finance/orders/deny` from the operator console. These are plain
   routes, not tools. The body names the `approval_id` and the
   `intent_digest` the person was shown. GraphOS refuses the decision when any
   of the following holds:
   - the session lacks the exact `finance:approve-live-order` scope;
   - the session is delegated;
   - the view is stale;
   - on approval, the approver is the proposer.
3. **Record.** An approval records the D18 `WriteBack` change set
   `finance-order:<approval_id>` under the approver's own verified session.
   Epistemic-graph refuses a change set whose actor is not the verified
   caller.
4. **Execute.** `emerald-exchange` places the order from that change set once,
   through `emerald_live_orders(action="execute_approved")`, after it checks
   the change set against the approval.

In paper mode, the default, no live order exists at all.

## Required identity

The GraphOS service identity needs the following EG scopes for the schedule
and delivery:

- `compute:finance`;
- the time-series read and write scopes;
- `broker:admin`, `broker:publish`, `broker:consume` and `broker:ack`;
- node read and write.

People who decide orders need:

- `finance:approve-live-order`;
- `connector:write-back`;
- `lease:read` and `lease:write`.

No service identity ever holds `finance:approve-live-order`.
