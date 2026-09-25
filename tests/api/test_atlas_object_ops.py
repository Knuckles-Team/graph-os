"""Focused contracts for Atlas and object-set operation seams."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from graph_os.api.ops import CURATED_SOURCES, atlas, object_sets
from graph_os.api.registry import Composite, Effect


def test_catalog_specs_share_exact_result_shape() -> None:
    ops = atlas.specs()
    assert {op.id for op in ops} == {"atlas.providers.list", "atlas.sources.list"}
    assert all(
        isinstance(op.binding, Composite) and op.effect is Effect.READ for op in ops
    )
    assert all(op.result is atlas.CatalogResult for op in ops)


@pytest.mark.asyncio
async def test_catalog_requires_bound_authority_and_validates_projection() -> None:
    caller = object()
    context = SimpleNamespace(caller=caller, services={})
    with pytest.raises(RuntimeError, match="unavailable"):
        await atlas.catalog_handler(context, {}, atlas.specs()[0])

    class Catalog:
        async def list_providers(self, actual_caller):
            assert actual_caller is caller
            return {
                "catalog_version": "v1",
                "observed_at": "2026-09-25T00:00:00Z",
                "providers": [],
            }

    context.services["atlas_source_catalog"] = Catalog()
    result = await atlas.catalog_handler(context, {}, atlas.specs()[0])
    assert result == {
        "catalog_version": "v1",
        "observed_at": "2026-09-25T00:00:00Z",
        "providers": [],
    }


@pytest.mark.asyncio
async def test_catalog_rejects_missing_provider_availability() -> None:
    class Catalog:
        async def list_providers(self, caller):
            return {
                "catalog_version": "v1",
                "observed_at": "now",
                "providers": [{"source_id": "x", "label": "X"}],
            }

    context = SimpleNamespace(
        caller=object(), services={"atlas_source_catalog": Catalog()}
    )
    with pytest.raises(ValidationError):
        await atlas.catalog_handler(context, {}, atlas.specs()[0])


@pytest.mark.asyncio
async def test_object_search_requires_bound_caller_scoped_authority() -> None:
    caller = object()
    context = SimpleNamespace(caller=caller, services={})
    with pytest.raises(RuntimeError, match="unavailable"):
        await object_sets.search_handler(
            context, {"query": "x"}, object_sets.specs()[0]
        )

    class Search:
        async def search(self, actual_caller, request):
            assert actual_caller is caller
            assert request.query == "x"
            return {"ids": ["n1"], "rows": [{"id": "n1", "label": "X"}], "count": 1}

    context.services["object_sets"] = Search()
    assert await object_sets.search_handler(
        context, {"query": "x"}, object_sets.specs()[0]
    ) == {"ids": ["n1"], "rows": [{"id": "n1", "label": "X"}], "count": 1}


def test_object_search_rejects_inconsistent_rows() -> None:
    with pytest.raises(ValidationError):
        object_sets.SearchResult.model_validate(
            {"ids": ["n1"], "rows": [{"id": "n2"}], "count": 1}
        )


def test_object_search_preserves_legacy_limit_ceiling() -> None:
    assert object_sets.SearchParams().limit == 50
    assert object_sets.SearchParams.model_validate({"limit": 256}).limit == 256
    with pytest.raises(ValidationError):
        object_sets.SearchParams.model_validate({"limit": 257})


def test_object_search_enforces_legacy_byte_bounds() -> None:
    with pytest.raises(ValidationError):
        object_sets.SearchParams.model_validate({"query": "é" * 4097})
    with pytest.raises(ValidationError):
        object_sets.SearchParams.model_validate({"kind": "é" * 65})


def test_unbound_atlas_and_object_sets_are_not_in_served_registry() -> None:
    names = {name for name, _factory in CURATED_SOURCES}
    assert "atlas" not in names
    assert "object_sets" not in names


@pytest.mark.asyncio
async def test_by_label_uses_verified_tenant_commons_and_eg_rls_rows() -> None:
    class Nodes:
        async def list_by_label_union(self, label, graphs, limit):
            assert (label, graphs, limit) == (
                "Document",
                ["tenant-a", "__commons__"],
                17,
            )
            return [("n1", {"type": "Document"})]

    context = SimpleNamespace(
        caller=SimpleNamespace(tenant="tenant-a"),
        client=SimpleNamespace(nodes=Nodes()),
    )
    result = await object_sets.by_label_handler(
        context, {"label": "Document", "limit": 17}, object_sets.served_specs()[0]
    )
    assert result == {
        "ids": ["n1"],
        "rows": [{"type": "Document", "id": "n1"}],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_by_label_rejects_missing_tenant_and_bad_engine_row() -> None:
    class Nodes:
        async def list_by_label_union(self, label, graphs, limit):
            return [("n1", {"id": "n2"})]

    context = SimpleNamespace(
        caller=SimpleNamespace(tenant=""),
        client=SimpleNamespace(nodes=Nodes()),
    )
    with pytest.raises(RuntimeError, match="tenant"):
        await object_sets.by_label_handler(
            context, {"label": "Document"}, object_sets.served_specs()[0]
        )
    context.caller.tenant = "tenant-a"
    with pytest.raises(ValueError, match="mismatched"):
        await object_sets.by_label_handler(
            context, {"label": "Document"}, object_sets.served_specs()[0]
        )
