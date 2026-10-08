"""``graph-os-production-ops provision-semantic-content`` through its CLI entrypoint.

The EG transport is faked at ``client._send`` so every generated sender, model
and decode runs for real; the process session, AU's policy-gated import
authority and the pack builder are the real ones.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_utilities.knowledge_graph.core.session import GraphSession

from graph_os.deployment import production_ops, semantic_provisioning
from tests._eg_fakes import AllowPolicy, FakeEgEngine, service_session

TENANT = "tenant-a"
GRAPH = "tenant__tenant-a__default"
URL = "https://graph-os.example/mcp"
OWNER = "principal:sha256:" + "e" * 64


def _binding(generation: int) -> dict[str, Any]:
    return {
        "configuration_revision": 1,
        "catalog_generation": generation,
        "snapshot_digest": "5" * 64,
        "child_connection_generation": 1,
        "authorization_scope_digest": "4" * 64,
    }


class _Engine(FakeEgEngine):
    """Answers the provisioning ops for one tenant graph."""

    def __init__(self, *, registered: set[str], graphs: set[str]) -> None:
        super().__init__()
        self.registered = registered
        self.graphs = graphs
        self.attests: list[dict[str, Any]] = []

    def _on_ListGraphs(self, _params: Any, _key: Any) -> Any:
        return [
            {"name": name, "type": "Team", "valid": True, "index_manifests": []}
            for name in sorted(self.graphs)
        ]

    def _on_CreateGraph(self, params: Any, _key: Any) -> Any:
        self.graphs.add(params["graph_name"])
        return {"created": params["graph_name"]}

    def _on_ListRegisteredServers(self, _params: Any, _key: Any) -> Any:
        entries = [
            {
                "name": name,
                "url": URL,
                "transport": "streamable_http",
                "desired": "enabled",
                "resources": {},
                "ttl_secs": 86_400,
                "registered_at_ms": 1,
                "last_heartbeat_ms": 1,
                "lease_expires_at_ms": 2**40,
            }
            for name in sorted(self.registered)
        ]
        return {
            "schema_version": 1,
            "entries": entries,
            "next_cursor": None,
            "observed_at_ms": 1,
            "total_live": len(entries),
            "registry_revision": 7,
            "registry_digest": "7" * 64,
        }

    def _on_RegisterServer(self, params: Any, _key: Any) -> Any:
        assert params["url"] == URL
        self.registered.add(params["name"])
        return "registered"

    def _on_catalog_authority_status(self, _params: Any, _key: Any) -> Any:
        return None

    def _on_attest_self_served_catalog(self, params: Any, key: Any) -> Any:
        request = params["op"]["request"]
        assert key == request["context"]["idempotency_key"]
        self.attests.append(request)
        return _binding(1)

    def _on_catalog_request_owner_principal(self, _params: Any, _key: Any) -> Any:
        return OWNER

    def _on_status(self, params: Any, _key: Any) -> Any:
        self.status_reads = getattr(self, "status_reads", 0) + 1
        return {
            "schema_version": 1,
            "tenant_id": TENANT,
            "connector": params["op"]["request"]["connector"],
            "members": {"published": 0, "withdrawn": 0, "retired": 0},
            "projection": {
                "projection": "applied",
                "graph": "pack__" + "d" * 64,
                "graph_version": 1,
            },
            "warnings": [],
        }

    def _on_reproject(self, _params: Any, _key: Any) -> Any:
        return {"reprojected": True}

    def _on_GraphSchema(self, params: Any, _key: Any) -> Any:
        assert params["op"]["op"] == "attach_pack"
        return {
            "changed": True,
            "composed_digest": "c" * 64,
            "graph": GRAPH,
            "graph_version": 1,
            "schema_version": 1,
        }


class _Sink:
    """Stands in for the SDK sink; resolves the real AU authority per import."""

    imported: list[tuple[str, Any, Any]] = []

    def __init__(self, client: Any, pack_import_authority: Any) -> None:
        self._resolve = pack_import_authority

    async def import_pack(self, pack: Any) -> Any:
        binding, context = await self._resolve(pack.connector)
        _Sink.imported.append((pack.connector, binding, context))
        return type("Unchanged", (), {"result": "unchanged"})()


def _session() -> GraphSession:
    return service_session(TENANT, GRAPH)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from graph_os.mcp_server import bootstrap, runtime

    state: dict[str, Any] = {"verified": []}
    engine = _Engine(registered=set(), graphs=set())
    session = _session()
    monkeypatch.setattr(runtime, "_mint_process_session", lambda transport: session)
    monkeypatch.setattr(runtime, "_get_engine", lambda: engine)
    monkeypatch.setattr(bootstrap, "_wait_for_engine_materialization", lambda e: None)
    monkeypatch.setattr(
        "agent_utilities.api.provisioning.get_action_policy",
        lambda _engine: AllowPolicy(),
    )
    monkeypatch.setattr(
        "agent_connector_sdk.sinks.epistemic_graph.EpistemicGraphSink", _Sink
    )

    async def verify(**kwargs: Any) -> None:
        from graph_os.semantic_content import SemanticContentNotReadyError

        attached = {
            call[2]["op"]["connector"]
            for call in engine.calls
            if call[0] == "GraphSchema"
        }
        missing = set(kwargs["connectors"]) - attached
        if missing:
            raise SemanticContentNotReadyError(f"not attached: {sorted(missing)}")
        state["verified"].append(kwargs)

    monkeypatch.setattr(semantic_provisioning, "verify_semantic_content", verify)
    _Sink.imported = []
    state["engine"] = engine
    return state


def test_cli_provisions_both_packs_under_eg_issued_bindings(
    wired: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    engine: _Engine = wired["engine"]

    code = production_ops.main(["provision-semantic-content", "--served-url", URL])

    report = json.loads(capsys.readouterr().out)
    assert code == 0, report
    assert report["ok"] is True
    assert report["graph"] == GRAPH and report["graph_created"] is True
    # A fresh store also lacks the WorkItem control graph (leases, queues).
    assert report["control_graph_created"] is True
    assert "__control__" in engine.graphs
    assert sorted(report["registered"]) == ["agent-utilities", "graph-os"]
    assert sorted(attest["connector"] for attest in engine.attests) == [
        "agent-utilities",
        "graph-os",
    ]
    for attest in engine.attests:
        # The first binding of a fresh store: no generation to fence on.
        assert attest.get("expected_catalog_generation") is None
        assert attest["server_name"] == attest["connector"]
    for connector, binding, context in _Sink.imported:
        assert binding.catalog_generation == 1
        assert context.principal == OWNER and context.tenant_id == TENANT
    # The import's own projection applied, so nothing was re-projected.
    assert not [call for call in engine.calls if call[0] == "reproject"]
    attaches = [call for call in engine.calls if call[0] == "GraphSchema"]
    assert {call[1] for call in attaches} == {GRAPH}
    final = [call for call in wired["verified"] if len(call["connectors"]) == 2]
    assert len(final) == 1 and final[0]["graph"] == GRAPH


def test_rerun_registers_and_creates_nothing(
    wired: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    engine: _Engine = wired["engine"]
    engine.registered.update({"graph-os", "agent-utilities"})
    engine.graphs.update({GRAPH, "__control__"})

    assert production_ops.main(["provision-semantic-content", "--served-url", URL]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["graph_created"] is False and report["registered"] == []
    assert report["control_graph_created"] is False
    assert not [
        call for call in engine.calls if call[0] in {"CreateGraph", "RegisterServer"}
    ]


def test_failed_verification_exits_non_zero(
    wired: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def refuse(**_kwargs: Any) -> None:
        raise RuntimeError("semantic pack is not attached")

    monkeypatch.setattr(semantic_provisioning, "verify_semantic_content", refuse)

    assert production_ops.main(["provision-semantic-content", "--served-url", URL]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_served_url_is_required(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GRAPH_OS_SERVED_MCP_URL", raising=False)

    assert production_ops.main(["provision-semantic-content"]) == 1
    assert (
        json.loads(capsys.readouterr().out)["error_type"] == "ProductionOperationError"
    )


def test_registration_digest_matches_engine_canonical_json() -> None:
    # EG digests compact, key-sorted JSON of {"resources", "url"}.
    digest = semantic_provisioning.registration_config_digest(URL, {"b": 1, "a": [2]})
    import hashlib

    expected = hashlib.sha256(
        b'{"resources":{"a":[2],"b":1},"url":"https://graph-os.example/mcp"}'
    ).hexdigest()
    assert digest == expected


async def test_boot_creates_tenant_and_control_graphs_on_a_fresh_store() -> None:
    from contextlib import nullcontext

    from graph_os.deployment.semantic_provisioning import ensure_base_graphs

    engine = _Engine(registered=set(), graphs=set())
    session = type("S", (), {"graph": GRAPH})()
    created = await ensure_base_graphs(
        engine=engine, session=session, bind_graph=lambda _g: nullcontext()
    )
    assert created == {GRAPH: True, "__control__": True}
    assert engine.graphs == {GRAPH, "__control__"}
    again = await ensure_base_graphs(
        engine=engine, session=session, bind_graph=lambda _g: nullcontext()
    )
    assert again == {GRAPH: False, "__control__": False}


async def test_boot_skips_engines_without_native_graphs() -> None:
    from graph_os.deployment.semantic_provisioning import ensure_base_graphs

    assert await ensure_base_graphs(engine=object(), session=object()) == {}
