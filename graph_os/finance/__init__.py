"""GraphOS finance delivery: flip alerts, scheduling and governed orders.

GraphOS composes; it owns no finance math and no signal state machine.

* :mod:`.topic`, :mod:`.subscriptions`, :mod:`.delivery` -- trend flips are
  published on the epistemic-graph broker topic exchange ``finance.flip``
  (idempotent on each flip record's content-addressed id) and fanned out to
  durable per-owner subscription queues, delivered once per subscription into
  an inbox (EH-416). An alert is information; nothing here can place an order.
* :mod:`.bars`, :mod:`.scheduler` -- the backfill/scan schedule: bars from the
  emerald-exchange connector into the EG time-series store, the EG
  ``FinanceMarket`` signal replay, flips onto the topic, latest signal states
  for the scanner (EH-419).
* :mod:`.orders` -- live-order proposals as ``finance.order-proposal`` leases;
  only the operator console route approves one and records the D18 change set
  under the approver's own verified identity (EH-423).
"""
