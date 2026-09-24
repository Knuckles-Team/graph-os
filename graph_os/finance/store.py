"""Keyset-paged reads of the finance nodes GraphOS keeps in EG."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

__all__ = ["labelled"]

_PAGE = 200


async def labelled(client: Any, label: str) -> AsyncIterator[dict[str, Any]]:
    """Every node of ``label``, one deterministic keyset page at a time."""
    after: str | None = None
    while True:
        page = await client.nodes.list_by_label(label, _PAGE, after=after)
        if not page:
            return
        for _node_id, record in page:
            if isinstance(record, dict):
                yield record
        after = page[-1][0]
