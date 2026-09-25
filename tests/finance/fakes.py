"""An in-memory tenant EG client with the finance surfaces' semantics.

Nodes (create-if-absent is atomic), the broker (topic routing with ``*``,
effectively-once publish per ``(producer_id, seq)``, claim/ack/reject), control
leases (one-way lifecycle, CAS on revision), the time-series store and the
``WriteBack`` ledger (actor must be the verified caller) behave like EG's;
``finance.market`` answers from a caller-set table because the math is EG's
and is tested there.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any


def principal_ref(principal: str) -> str:
    return "principal:sha256:" + hashlib.sha256(principal.encode()).hexdigest()


def topic_matches(pattern: str, key: str) -> bool:
    want, have = pattern.split("."), key.split(".")
    return len(want) == len(have) and all(
        w in ("*", h) for w, h in zip(want, have, strict=True)
    )


@dataclass
class Nodes:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def add(self, node_id: str, properties: dict[str, Any]) -> None:
        self.rows[node_id] = dict(properties)

    async def create_if_absent(self, node_id: str, properties: dict[str, Any]) -> bool:
        if node_id in self.rows:
            return False
        self.rows[node_id] = dict(properties)
        return True

    async def properties(self, node_id: str) -> dict[str, Any] | None:
        return self.rows.get(node_id)

    async def compare_and_set(
        self, node_id: str, conditions: dict[str, Any], updates: dict[str, Any]
    ) -> bool:
        row = self.rows.get(node_id)
        if row is None or any(row.get(k) != v for k, v in conditions.items()):
            return False
        row.update(updates)
        return True

    async def list_by_label(
        self, label: str, limit: int = 0, *, after: str | None = None
    ) -> list[tuple[str, Any]]:
        ids = sorted(k for k, v in self.rows.items() if v.get("type") == label)
        ids = [i for i in ids if after is None or i > after]
        return [(i, self.rows[i]) for i in (ids[:limit] if limit else ids)]


@dataclass
class Broker:
    exchanges: dict[str, str] = field(default_factory=dict)
    bindings: list[tuple[str, str, str]] = field(default_factory=list)
    queues: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    producers: dict[str, int] = field(default_factory=dict)
    acked: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    counter: int = 0

    async def declare_exchange(self, exchange: str, kind: str = "direct") -> str:
        self.exchanges[exchange] = kind
        return "ok"

    async def declare_queue(self, queue: str, **_policy: Any) -> str:
        self.queues.setdefault(queue, [])
        return "ok"

    async def bind_queue(self, exchange: str, queue: str, routing_key: str) -> str:
        self.bindings.append((exchange, queue, routing_key))
        return "ok"

    async def unbind_queue(self, exchange: str, queue: str, routing_key: str) -> bool:
        binding = (exchange, queue, routing_key)
        if binding not in self.bindings:
            return False
        self.bindings.remove(binding)
        return True

    async def publish_idempotent(
        self,
        exchange: str,
        routing_key: str,
        payload: bytes,
        *,
        producer_id: str,
        seq: int,
        now_ms: int,
    ) -> dict[str, Any]:
        if self.producers.get(producer_id, 0) >= seq:
            return {"confirmed": True, "duplicate": True, "delivered": 0}
        self.producers[producer_id] = seq
        delivered = 0
        for bound_exchange, queue, pattern in self.bindings:
            if bound_exchange == exchange and topic_matches(pattern, routing_key):
                self.counter += 1
                message = {
                    "id": f"msg:{self.counter}",
                    "payload": payload.hex(),
                    "claimed": False,
                }
                self.queues[queue].append(message)
                delivered += 1
        return {"confirmed": True, "duplicate": False, "delivered": delivered}

    async def consume(
        self,
        queue: str,
        *,
        group: str,
        consumer: str,
        now_ms: int,
        lease_ms: int = 0,
        prefetch: int = 0,
    ) -> tuple[str, dict[str, Any]] | None:
        for message in self.queues.get(queue, []):
            if not message["claimed"]:
                message["claimed"] = True
                return message["id"], {"payload": message["payload"]}
        return None

    async def ack(self, queue: str, node_id: str) -> bool:
        self.acked.append(node_id)
        self.queues[queue] = [m for m in self.queues[queue] if m["id"] != node_id]
        return True

    async def reject(
        self, queue: str, node_id: str, *, requeue: bool, now_ms: int
    ) -> str:
        self.rejected.append(node_id)
        for message in self.queues[queue]:
            if message["id"] == node_id:
                message["claimed"] = False
        return "requeued"

    def expire_claims(self) -> None:
        """A consumer died holding its claims: they become claimable again."""
        for messages in self.queues.values():
            for message in messages:
                message["claimed"] = False


@dataclass
class Leases:
    owner: FinanceClient
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def issue(
        self,
        *,
        tenant: str,
        lease_id: str,
        kind: str,
        grant: dict[str, Any],
        issued_at_ms: int,
        expires_at_ms: int,
        hard_expires_at_ms: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if lease_id in self.rows:
            return {"outcome": "collision", "lease": None}
        self.rows[lease_id] = {
            "tenant": tenant,
            "lease_id": lease_id,
            "kind": kind,
            "status": "active",
            "grant": grant,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "hard_expires_at_ms": hard_expires_at_ms,
            "revision": 1,
        }
        return {"outcome": "issued", "lease": self.view(lease_id)}

    def view(self, lease_id: str) -> dict[str, Any]:
        return {k: v for k, v in self.rows[lease_id].items() if k != "tenant"}

    async def get(self, *, tenant: str, lease_id: str) -> dict[str, Any] | None:
        row = self.rows.get(lease_id)
        return None if row is None or row["tenant"] != tenant else self.view(lease_id)

    async def transition(
        self,
        *,
        tenant: str,
        lease_id: str,
        expected_revision: int,
        to: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        row = self.rows.get(lease_id)
        if row is None or row["tenant"] != tenant:
            return {"outcome": "not_found"}
        if row["revision"] != expected_revision or row["status"] != "active":
            return {"outcome": "conflict"}
        row["status"], row["revision"] = to, row["revision"] + 1
        return {"outcome": "applied"}


@dataclass
class Series:
    points: dict[str, list[tuple[int, list[float]]]] = field(default_factory=dict)

    async def append(
        self, series_id: str, points: list[tuple[int, list[float]]], **_kw: Any
    ) -> int:
        self.points.setdefault(series_id, []).extend(points)
        return len(points)

    async def range(
        self, series_id: str, start: int, end: int
    ) -> list[tuple[int, list[float]]]:
        return [p for p in self.points.get(series_id, []) if start <= p[0] < end]


@dataclass
class Finance:
    """``finance.market`` over records the fake keeps beside their points."""

    replay: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    encoded: dict[int, dict[str, Any]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def market(self, op: str, **params: Any) -> Any:
        self.calls.append(op)
        return getattr(self, "_" + op)(**params)

    def _encode_points(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        points = []
        for record in records:
            key = len(self.encoded)
            self.encoded[key] = record
            points.append({"ts": record["open_time"], "values": [float(key)]})
        return points

    def _resolve(
        self, points: list[dict[str, Any]], finality: str, **_kw: Any
    ) -> list[dict[str, Any]]:
        return [self.encoded[int(p["values"][0])] for p in points]

    def _signal_replay(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.replay is not None, "the test sets what the replay answers"
        return self.replay(request)

    def _signal_scan(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "rows": request["states"][: request["limit"]],
            "filter": request["filter"],
        }


@dataclass
class Consensus:
    """EG ``CheckAccess``: which principals may read the tenant graph now."""

    readers: set[str] = field(default_factory=set)
    checks: list[str] = field(default_factory=list)

    async def check_access(self, agent_id: str, access: str = "read") -> bool:
        self.checks.append(agent_id)
        return access == "read" and agent_id in self.readers


@dataclass
class FinanceClient:
    caller: str | None = None
    bound: list[dict[str, Any]] = field(default_factory=list)
    nodes: Nodes = field(default_factory=Nodes)
    broker: Broker = field(default_factory=Broker)
    timeseries: Series = field(default_factory=Series)
    finance: Finance = field(default_factory=Finance)
    consensus: Consensus = field(default_factory=Consensus)
    change_sets: dict[str, dict[str, Any]] = field(default_factory=dict)
    control_leases: Leases = field(init=False)

    def __post_init__(self) -> None:
        self.control_leases = Leases(self)

    @contextlib.contextmanager
    def use_verified_context(self, claims: dict[str, Any]) -> Iterator[None]:
        self.bound.append(claims)
        prior, self.caller = self.caller, str(claims["principal"])
        try:
            yield
        finally:
            self.caller = prior

    async def _send(
        self,
        method: str,
        params: dict[str, Any] | None,
        graph: str | None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        assert method == "WriteBack" and params is not None
        op = params["op"]
        assert op["op"] == "create"
        change_set = op["change_set"]
        assert self.caller is not None, "every write runs under a verified context"
        if change_set["actor"] != principal_ref(self.caller):
            raise PermissionError(
                "ACCESS_DENIED: change-set actor must match verified request principal"
            )
        existing = self.change_sets.setdefault(change_set["change_set_id"], change_set)
        if existing != change_set:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        return change_set


@contextlib.contextmanager
def serving(client: FinanceClient) -> Iterator[FinanceClient]:
    """Compose ``client`` as graph-os's finance service executor for a test."""
    from graph_os.finance.authority import FinanceService, install_finance_service

    install_finance_service(FinanceService(lambda: contextlib.nullcontext(client)))
    try:
        yield client
    finally:
        install_finance_service(None)
