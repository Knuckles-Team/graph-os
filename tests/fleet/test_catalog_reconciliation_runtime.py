"""Runtime proof for the served four-family catalog reconciliation path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from graph_os.fleet.catalog_reconciliation import CatalogContractError
from graph_os.fleet.multiplexer import MCPMultiplexer


def _mux(tmp_path: Path) -> MCPMultiplexer:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-mcp"}}}),
        encoding="utf-8",
    )
    mux = MCPMultiplexer(config)
    mux.load_catalog()
    return mux


def _row(*, template_type: str = "string") -> tuple[str, dict[str, Any]]:
    return (
        "docs",
        {
            "tools": [{"name": "search", "description": "Search", "inputSchema": {}}],
            "resources": [{"uri": "docs://index", "name": "Index"}],
            "resource_templates": [
                {
                    "uriTemplate": "docs://{topic}",
                    "name": "Topic",
                    "arguments": {"topic": {"type": template_type}},
                }
            ],
            "native_prompts": [
                {"name": "summarize", "arguments": [{"name": "subject"}]}
            ],
            "catalog_family_errors": {},
            "error": None,
        },
    )


@pytest.mark.asyncio
async def test_refresh_atomically_digests_all_families_and_exact_writer_receipt(
    tmp_path: Path,
) -> None:
    mux = _mux(tmp_path)
    rows = [_row()]
    mux._probe_catalog_candidate_rows = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda _deadline: rows
    )
    receipts: list[tuple[int, str]] = []

    async def writer(
        _catalog: dict[str, Any],
        _configs: dict[str, Any],
        _bindings: dict[str, Any],
        *,
        catalog_generation: int,
        snapshot_digest: str,
    ) -> dict[str, Any]:
        receipts.append((catalog_generation, snapshot_digest))
        return {
            "status": "ok",
            "catalog_generation": catalog_generation,
            "snapshot_digest": snapshot_digest,
        }

    mux._fleet_catalog_writer = writer
    first = await mux.reconcile_current_catalog(request_id="refresh-1")
    unchanged = await mux.reconcile_current_catalog(request_id="refresh-2")
    rows[0] = _row(template_type="integer")
    changed = await mux.reconcile_current_catalog(request_id="refresh-3")

    assert first.changed is True
    assert unchanged.changed is False
    assert unchanged.catalog_generation == first.catalog_generation
    assert unchanged.snapshot_digest == first.snapshot_digest
    assert changed.changed is True
    assert changed.catalog_generation == first.catalog_generation + 1
    assert changed.snapshot_digest != first.snapshot_digest
    assert receipts == [
        (first.catalog_generation, first.snapshot_digest),
        (unchanged.catalog_generation, unchanged.snapshot_digest),
        (changed.catalog_generation, changed.snapshot_digest),
    ]
    snapshot = mux.catalog_snapshot()
    child = snapshot.children[0]
    assert len(child.tools) == 1
    assert len(child.resources) == 1
    assert len(child.resource_templates) == 1
    assert len(child.prompts) == 1


@pytest.mark.asyncio
async def test_refresh_fails_truthfully_without_generic_durability_capability(
    tmp_path: Path,
) -> None:
    mux = _mux(tmp_path)
    mux._probe_catalog_candidate_rows = AsyncMock(  # type: ignore[method-assign]
        return_value=[_row()]
    )

    with pytest.raises(CatalogContractError) as raised:
        await mux.reconcile_current_catalog(request_id="refresh-unavailable")

    assert raised.value.code == "reingestion-unreconciled"
    assert mux.catalog_identity().catalog_generation == 0


@pytest.mark.asyncio
async def test_refresh_rejects_stale_generation_digest_acknowledgement(
    tmp_path: Path,
) -> None:
    mux = _mux(tmp_path)
    mux._probe_catalog_candidate_rows = AsyncMock(  # type: ignore[method-assign]
        return_value=[_row()]
    )

    async def stale_writer(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "catalog_generation": 99,
            "snapshot_digest": "0" * 64,
        }

    mux._fleet_catalog_writer = stale_writer
    with pytest.raises(CatalogContractError) as raised:
        await mux.reconcile_current_catalog(request_id="refresh-stale")

    assert raised.value.code == "reingestion-unreconciled"
    assert mux.catalog_identity().catalog_generation == 0
