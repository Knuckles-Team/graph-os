"""Real composition seam with explicitly synthetic provider/service evidence."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from graph_os.api.http import app as api_app
from graph_os.api.registry import contract_admission
from graph_os.api.registry.eg_binding import EgContractError
from graph_os.mcp_server.composition import NativeGatewayApplication
from tests.api.test_contract_admission import installed_contract as installed_contract
from tests.api.test_contract_admission import startup_evidence as startup_evidence


class _Auth:
    async def authenticate(self, request):
        return SimpleNamespace(
            principal_kind="service",
            delegated=False,
            effective_scopes=frozenset(
                {"query:read", "admin:cluster", "service:control"}
            ),
        )

    def is_console_request(self, request, caller):
        return False


async def _visibility(op, caller):
    return True


async def _invoke(*args, **kwargs):
    raise AssertionError("discovery must not invoke operations")


def _dependencies(registry):
    return dict(
        services=SimpleNamespace(registry=registry),
        visibility=_visibility,
        authenticator=_Auth(),
        invoke=_invoke,
    )


@pytest.mark.parametrize("prefix", ["", "/api"])
def test_real_composition_admits_before_constructing_and_mounting(
    startup_evidence, monkeypatch, prefix
):
    registry, _, _ = startup_evidence
    events = []
    admit = contract_admission.validate_contract_admission
    construct = api_app.create_api_application

    def admission(candidate):
        assert candidate is registry
        admit(candidate)
        events.append("admitted")

    def create(**kwargs):
        assert events == ["admitted"]
        child = construct(**kwargs)
        events.append("constructed")
        return child

    monkeypatch.setattr(contract_admission, "validate_contract_admission", admission)
    monkeypatch.setattr(api_app, "create_api_application", create)
    parent = FastAPI()
    NativeGatewayApplication().mount_rest_routes(
        parent, prefix=prefix, **_dependencies(registry)
    )
    assert events == ["admitted", "constructed"]
    assert [route.path for route in parent.routes if route.path == "/api/v1"] == [
        "/api/v1"
    ]

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent), base_url="http://test"
        ) as client:
            return await client.get("/api/v1/registry")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["registry_digest"] == registry.digest


@pytest.mark.parametrize("symbol", ["REGISTRY_DIGEST", "EG_RECEIPT_DIGEST"])
def test_mismatch_prevents_construction_and_any_mount(
    startup_evidence, monkeypatch, symbol
):
    registry, generated, _ = startup_evidence
    setattr(generated, symbol, "c" * 64)

    def forbidden(**kwargs):
        raise AssertionError("application constructed despite admission refusal")

    monkeypatch.setattr(api_app, "create_api_application", forbidden)
    parent = FastAPI()
    before = tuple(parent.routes)
    with pytest.raises(EgContractError):
        NativeGatewayApplication().mount_rest_routes(
            parent, prefix="", **_dependencies(registry)
        )
    assert tuple(parent.routes) == before


def test_partial_composition_does_not_fall_back_to_legacy_routes(startup_evidence):
    registry, _, _ = startup_evidence
    parent = FastAPI()
    before = tuple(parent.routes)
    with pytest.raises(EgContractError, match="composition requires"):
        NativeGatewayApplication().mount_rest_routes(
            parent, prefix="", services=SimpleNamespace(registry=registry)
        )
    assert tuple(parent.routes) == before
