"""Focused contracts for Atlas and object-set operation seams."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from graph_os.api.ops import atlas, object_sets
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
    assert object_sets.SearchParams.model_validate({"limit": 256}).limit == 256
    with pytest.raises(ValidationError):
        object_sets.SearchParams.model_validate({"limit": 257})
