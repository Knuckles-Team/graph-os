"""GRAPHOS-FLEET-R030: access contracts register as unapproved mappings."""

from __future__ import annotations

import builtins
import sys
import types
from dataclasses import dataclass, field

import pytest

from graph_os.fleet import access_contracts as ac

vg = pytest.importorskip("agent_utilities.knowledge_graph.virtual_graph")
VirtualCatalog = vg.VirtualCatalog
TripleOntology = vg.TripleOntology

CLS = "https://example.org/onto#Ticket"


@dataclass(frozen=True)
class _Entry:
    uri: str
    body: bytes


@dataclass(frozen=True)
class _Pack:
    entries: tuple[_Entry, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _Contract:
    contract_iri: str
    source_id: str = "tickets"
    entity: str = "ticket"
    class_iri: str = CLS
    key_field: str = "id"
    access_kind: str = "mcp_tool"
    operation: str = "list_tickets"
    predicates: tuple[tuple[str, str], ...] = (
        ("https://example.org/onto#title", "title"),
    )

    def virtual_mapping_fields(self) -> dict[str, object]:
        return {
            "mapping_id": self.contract_iri,
            "source_id": self.source_id,
            "entity": self.entity,
            "class_iri": self.class_iri,
            "key_field": self.key_field,
            "predicates": self.predicates,
        }


TTL = "@prefix ac: <https://example.org/ac#> . # fake connector ontology"


@pytest.fixture
def fake_sdk(monkeypatch):
    seen: list[str] = []

    def parse(text: str):
        seen.append(text)
        return (_Contract("urn:ac:1"), _Contract("urn:ac:2", entity="ticket"))

    module = types.ModuleType("agent_connector_sdk.access_contract")
    module.parse_access_contracts = parse
    monkeypatch.setitem(sys.modules, "agent_connector_sdk.access_contract", module)
    return seen


def _pack() -> _Pack:
    return _Pack(
        entries=(
            _Entry("ontology://tickets", TTL.encode()),
            _Entry("shapes://tickets", b"ignored"),
        )
    )


def test_registers_unapproved_mappings(fake_sdk) -> None:
    catalog = VirtualCatalog()
    added = ac.register_access_contracts(
        _pack(), connector="tickets-mcp", catalog=catalog
    )
    assert added == 2
    assert fake_sdk == [TTL]
    connection = catalog.connections["tickets"]
    assert connection.kind == "mcp"
    assert connection.endpoint_ref == "fleet://tickets-mcp"
    assert [m.approved for m in catalog.mappings] == [False, False]
    ontology = TripleOntology([])
    assert catalog.mapping_for(CLS, ontology) is None


def test_repeat_pass_adds_no_duplicate(fake_sdk) -> None:
    catalog = VirtualCatalog()
    ac.register_access_contracts(_pack(), connector="t", catalog=catalog)
    assert ac.register_access_contracts(_pack(), connector="t", catalog=catalog) == 0
    assert len(catalog.mappings) == 2


def test_missing_sdk_module_skips(monkeypatch) -> None:
    monkeypatch.delitem(
        sys.modules, "agent_connector_sdk.access_contract", raising=False
    )
    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "agent_connector_sdk.access_contract":
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    catalog = VirtualCatalog()
    assert ac.register_access_contracts(_pack(), connector="t", catalog=catalog) == 0
    assert catalog.mappings == []


def test_onboarding_registers_after_import(fake_sdk, monkeypatch) -> None:
    import asyncio

    from graph_os.deployment import semantic_provisioning
    from graph_os.fleet.onboarding import FleetEndpoint, FleetOnboarding

    async def fake_import(*args, **kwargs):
        return None

    async def capture(endpoint, *, auth):
        return _pack()

    monkeypatch.setattr(semantic_provisioning, "import_attested_pack", fake_import)
    catalog = VirtualCatalog()

    class _Engine:
        class graph_compute:
            @staticmethod
            def for_graph(name):
                return types.SimpleNamespace(async_client=object())

    onboarding = FleetOnboarding(
        engine=_Engine(),
        session=types.SimpleNamespace(graph="g"),
        capture=capture,
        virtual_catalog=catalog,
    )
    asyncio.run(
        onboarding.onboard(FleetEndpoint("tickets-mcp", "http://x/mcp"), auth=None)
    )
    assert len(catalog.mappings) == 2
    assert not any(m.approved for m in catalog.mappings)
