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


TTL = "@prefix ac: <https://example.org/ac#> . ac:c a ac:AccessContract ."


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

    from agent_utilities.api.session import use_session

    from graph_os.deployment import semantic_provisioning
    from graph_os.fleet.onboarding import FleetEndpoint, FleetOnboarding
    from tests._eg_fakes import service_session

    async def fake_import(*args, **kwargs):
        return None

    async def capture(endpoint, *, auth):
        return _pack()

    monkeypatch.setattr(semantic_provisioning, "import_attested_pack", fake_import)
    catalog = VirtualCatalog()

    class _StatusClient:
        """Answers ConnectorPack.status with no head: always "changed"."""

        async def _send(self, _method, _params, _graph, *, idempotency_key=None):
            return {
                "connector": "tickets-mcp",
                "head": None,
                "importer": None,
                "last_receipt": None,
                "members": {"published": 0, "retired": 0, "withdrawn": 0},
                "projection": {"projection": "none"},
                "schema_version": 1,
                "tenant_id": "t",
                "warnings": [],
            }

    class _Engine:
        class graph_compute:
            @staticmethod
            def for_graph(name):
                return types.SimpleNamespace(async_client=_StatusClient())

    session = service_session("t", "g")
    onboarding = FleetOnboarding(
        engine=_Engine(),
        session=session,
        capture=capture,
        virtual_catalog=catalog,
    )
    with use_session(session):
        asyncio.run(
            onboarding.onboard(FleetEndpoint("tickets-mcp", "http://x/mcp"), auth=None)
        )
    assert len(catalog.mappings) == 2
    assert not any(m.approved for m in catalog.mappings)


AC_NS = "https://knuckles-team.github.io/agent-connector-sdk/access#"
DOMAIN_TTL = (
    "@prefix ex: <https://example.org/onto#> .\n"
    'ex:Ticket ex:comment """A ticket.\nSpans lines.""" .\n'
)


def _real_parser():
    return pytest.importorskip("agent_connector_sdk.access_contract")


def test_only_contract_bodies_reach_parser(fake_sdk) -> None:
    pack = _Pack(
        entries=(
            _Entry("ontology://domain", DOMAIN_TTL.encode()),
            _Entry("ontology://contracts", TTL.encode()),
        )
    )
    catalog = VirtualCatalog()
    assert ac.register_access_contracts(pack, connector="t", catalog=catalog) == 2
    assert fake_sdk == [TTL]


def test_malformed_contracts_file_is_skipped(monkeypatch, caplog) -> None:
    def parse(text: str):
        raise ValueError("bad turtle")

    module = types.ModuleType("agent_connector_sdk.access_contract")
    monkeypatch.setattr(module, "parse_access_contracts", parse, raising=False)
    monkeypatch.setitem(sys.modules, "agent_connector_sdk.access_contract", module)
    pack = _Pack(entries=(_Entry("ontology://broken", TTL.encode()),))
    catalog = VirtualCatalog()
    with caplog.at_level("WARNING"):
        added = ac.register_access_contracts(pack, connector="t", catalog=catalog)
    assert added == 0
    assert "ontology://broken" in caplog.text


def test_real_parser_skips_triple_quoted_domain_ontology() -> None:
    sdk = _real_parser()
    pack = _Pack(
        entries=(
            _Entry("ontology://domain", DOMAIN_TTL.encode()),
            _Entry("ontology://contracts", b"@prefix ac: <" + AC_NS.encode() + b"> ."),
        )
    )
    assert ac.parse_pack_contracts(pack) == ()
    with pytest.raises(sdk.AccessContractError):
        sdk.parse_access_contracts(DOMAIN_TTL)
