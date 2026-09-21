"""Semantic providers preserve independent pack identities."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os import semantic_content
from graph_os.semantic_content import provision_semantic_content


@pytest.mark.asyncio
async def test_provisioning_imports_and_attaches_each_provider_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    imported: list[str] = []
    reprojected: list[str] = []
    attached: list[str] = []

    def provider(connector: str) -> Any:
        return lambda: SimpleNamespace(
            connector=connector,
            package_root=tmp_path,
            package_version="1.0.0",
            manifest_path=None,
        )

    async def provision(content: Any, *, sink: Any) -> Any:
        assert sink == "verified-sink"
        imported.append(content.connector)
        return SimpleNamespace(pack_digest=f"sha256:{content.connector}")

    async def attach(connector: str) -> None:
        attached.append(connector)

    async def reproject(connector: str) -> str:
        reprojected.append(connector)
        return f"reproject:{connector}"

    monkeypatch.setattr(
        "graph_os.semantic_content.provision_connector_content", provision
    )

    receipts = await provision_semantic_content(
        (provider("graph-os"), provider("agent-utilities")),
        sink="verified-sink",
        reproject_pack=reproject,
        attach_pack=attach,
    )

    assert imported == ["graph-os", "agent-utilities"]
    assert reprojected == imported
    assert attached == imported
    assert [receipt.import_outcome.pack_digest for receipt in receipts] == [
        "sha256:graph-os",
        "sha256:agent-utilities",
    ]
    assert [receipt.reproject_receipt for receipt in receipts] == [
        "reproject:graph-os",
        "reproject:agent-utilities",
    ]


@pytest.mark.asyncio
async def test_duplicate_connector_provider_fails_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def provider() -> Any:
        return SimpleNamespace(connector="graph-os")

    async def provision(content: Any, *, sink: Any) -> Any:
        nonlocal calls
        calls += 1
        return content, sink

    monkeypatch.setattr(
        "graph_os.semantic_content.provision_connector_content", provision
    )

    with pytest.raises(ValueError, match="unique connectors"):
        await provision_semantic_content(
            (provider, provider),
            sink=object(),
            reproject_pack=lambda connector: None,  # type: ignore[arg-type]
            attach_pack=lambda connector: None,  # type: ignore[arg-type]
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_readiness_requires_current_projected_attached_pack_heads() -> None:
    statuses = {
        connector: SimpleNamespace(
            head=SimpleNamespace(record_id=f"record:{connector}"),
            projection=SimpleNamespace(projection="applied", graph="tenant-a"),
        )
        for connector in ("graph-os", "agent-utilities")
    }
    sources = tuple(
        SimpleNamespace(
            source_id=f"pack:{connector}",
            origin=SimpleNamespace(
                origin="pack", connector=connector, record_id=f"record:{connector}"
            ),
            shapes_sha256="ab" * 32,
        )
        for connector in statuses
    )

    async def list_schema(client: Any, params: Any, graph: str) -> Any:
        return SimpleNamespace(dynamic_sources=sources)

    async def status(client: Any, request: Any, graph: str) -> Any:
        return statuses[request.connector]

    await semantic_content.verify_semantic_content(
        client=object(),
        tenant_id="tenant-a",
        graph="tenant-a",
        connectors=("graph-os", "agent-utilities"),
        schema_reader=list_schema,
        status_reader=status,
    )


@pytest.mark.asyncio
async def test_readiness_fails_closed_on_stale_attachment() -> None:
    async def list_schema(client: Any, params: Any, graph: str) -> Any:
        return SimpleNamespace(
            dynamic_sources=(
                SimpleNamespace(
                    source_id="pack:graph-os",
                    origin=SimpleNamespace(
                        origin="pack",
                        connector="graph-os",
                        record_id="record:old",
                    ),
                    shapes_sha256="ab" * 32,
                ),
            )
        )

    async def status(client: Any, request: Any, graph: str) -> Any:
        return SimpleNamespace(
            head=SimpleNamespace(record_id="record:new"),
            projection=SimpleNamespace(projection="applied", graph="tenant-a"),
        )

    with pytest.raises(
        semantic_content.SemanticContentNotReadyError, match="absent or stale"
    ):
        await semantic_content.verify_semantic_content(
            client=object(),
            tenant_id="tenant-a",
            graph="tenant-a",
            connectors=("graph-os",),
            schema_reader=list_schema,
            status_reader=status,
        )
