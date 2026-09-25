"""A tenant EG client whose ``RbacElevation`` ledger keeps EG's two-person rules.

The caller is whoever the surface bound with ``use_verified_context`` -- the
fake never reads identity from a request body, so a surface that tried to
smuggle one in would fail the ``actor`` assertion.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ElevationClient:
    now_ms: int = field(default_factory=lambda: time.time_ns() // 1_000_000)
    caller: str | None = None
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    ops: list[str] = field(default_factory=list)
    bound: list[dict[str, Any]] = field(default_factory=list)

    @contextlib.contextmanager
    def use_verified_context(self, claims: dict[str, Any]) -> Iterator[None]:
        self.bound.append(claims)
        prior, self.caller = self.caller, str(claims["agent_id"])
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
        assert method == "RbacElevation" and params is not None
        assert "actor" not in params, "identity never travels in a body"
        assert self.caller is not None, "every call runs under a verified context"
        op = params["op"]
        self.ops.append(op["op"])
        return getattr(self, "_" + op["op"])(op.get("request"))

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        digest = hashlib.sha256(
            json.dumps([body, self.caller], sort_keys=True).encode()
        ).hexdigest()
        lease = {
            "elevation_id": body["elevation_id"],
            "grantee": self.caller,
            "requester_parties": [self.caller],
            "scopes": body["scopes"],
            "span_ms": body["span_ms"],
            "justification": body["justification"],
            "status": "requested",
            "requested_at_ms": self.now_ms,
            "request_digest": digest,
            "revision": 1,
        }
        self.leases[body["elevation_id"]] = lease
        return lease

    def _list(self, _body: None) -> list[dict[str, Any]]:
        return list(self.leases.values())

    def _approve(self, body: dict[str, Any]) -> dict[str, Any]:
        lease = self.leases[body["elevation_id"]]
        assert self.caller not in lease["requester_parties"]
        lease.update(
            status="active",
            approved_at_ms=self.now_ms,
            hard_expires_at_ms=self.now_ms + lease["span_ms"],
        )
        return lease

    def _revoke(self, body: dict[str, Any]) -> dict[str, Any]:
        lease = self.leases[body["elevation_id"]]
        lease.update(status="revoked", ended_at_ms=self.now_ms)
        return lease
