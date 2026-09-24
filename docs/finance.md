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

`graph_finance` (MCP) and its REST twin `POST /graph/finance` check the
verified caller, then do the work on GraphOS's own service identity:

1. The caller's session must hold the action's exact finance domain scope
   (the Scope column below). Otherwise the call is refused with
   `FINANCE_SCOPE_REQUIRED`.
2. EG `CheckAccess` must say the caller's own read of the tenant graph would
   be admitted. Otherwise the call is refused with
   `FINANCE_READ_AUTHORITY_REQUIRED`.
3. The action then runs on the GraphOS service client. Every subscription,
   track and proposal it creates names the verified caller as its owner, and
   only that caller can list or cancel it.

Callers therefore never need the broker, time-series, compute or lease scopes
themselves.

| Action | Scope | What it does |
|---|---|---|
| `subscribe` | `finance:alerts` | Creates a durable subscription to trend flips. The optional `filter` takes `asset_class`, `timeframe`, `listing_id` and `direction`. |
| `unsubscribe`, `subscriptions` | `finance:alerts` | Cancels or lists the caller's own subscriptions. |
| `alerts` | `finance:alerts` | Returns the caller's inbox of delivered flips, newest first. |
| `track`, `untrack`, `tracked` | `finance:track` | Manages the caller's own tracks: the listing and timeframe pairs the schedule keeps current. A series stays scheduled while anyone tracks it. |
| `backfill` | `finance:backfill` | Runs a full `period` backfill and scan of one series now. |
| `scan` | `finance:alerts` | Returns the latest-state scanner over every scanned series (EG `FinanceMarket.signal_scan`). |
| `explain_flip` | `finance:alerts` | Explains one flip in the caller's inbox: the math first, then claims that each cite gathered news. |
| `propose_order` | `finance:propose-order` | Records a live-order proposal for a person to decide. It places nothing. |
| `order_status` | `finance:propose-order` | Reads the state of one of the caller's own proposals: pending, approved, denied or expired. |

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
4. Before each write, the pass asks EG `CheckAccess` whether the subscriber
   can still read the tenant graph. If the subscriber's access was revoked,
   the message is acknowledged as withheld and nothing is delivered. A later
   re-grant does not replay withheld flips.

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

Each tick also keeps the finance-v1 catalog that the Markets app reads:

- `FinancialInstrument`, `Venue`, `Listing` and `BarSeries` nodes for every
  tracked series (`BarSeries` names its `tsdbSeriesId`, `tickSize` and
  `volumeStep`);
- one `SignalState` node per series (`signalOf`, `trendDirection`,
  `dataStatus`, and the engine's full state as `checkpoint`), so a scanner
  reads the latest state instead of replaying every series;
- `MacroEvent` nodes for the sourced FOMC decisions from `market-data-mcp`;
- CoinMarketCap `marketCap`, `marketCapRank` and `marketCapAsOf` on the crypto
  instruments.

The tick then runs one delivery pass. A failure on one series or feed does not
stop the rest. Set the interval to `0` to turn the schedule off.

A tracked series names its listing (`listing_id`, `symbol`, `base`, `quote`,
`venue`, `listing_type`, `asset_class`), its `interval` and backfill `period`,
the `price_decimals` and `volume_decimals` its integer ticks use, and the
trailing-trend parameters (`atr_period`, `multiplier_milli`, `basis`).

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

People who use the markets features hold only the finance domain scopes:
`finance:alerts`, `finance:track`, `finance:backfill` and
`finance:propose-order`.

The GraphOS service identity executes the work and needs these EG scopes:

- `compute:finance`;
- `timeseries:read` and `timeseries:write`;
- `broker:admin`, `broker:publish`, `broker:consume` and `broker:ack`;
- `security:check`, for the `CheckAccess` caller and subscriber checks;
- `lease:read` and `lease:write`, to record proposals and read their state;
- node read and write.

The people who decide orders are the members of the Keycloak group
`live-order-approvers`, which has the operator as its only member. The group
grants:

- `finance:approve-live-order`;
- `connector:write-back`;
- `lease:read` and `lease:write`.

The realm declarations and the apply procedure are in `services/keycloak`,
`realm/FINANCE.md`.

No service identity ever holds `finance:approve-live-order` or
`connector:write-back`. The `emerald-exchange` connector must also list the
approvers in `EMERALD_LIVE_ORDER_APPROVERS`. When that list is unset, every
live order is refused. It executes a change set only when the change set's
actor is one of the approvers, so an agent that could write EG records
directly still cannot authorise an order.
