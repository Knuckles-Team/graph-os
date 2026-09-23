"""Governance and live-path tests for the Graph OS browser-control core."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.knowledge_graph.core.session import GraphSession, use_session
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext
from agent_utilities.security.persistence_privacy import persistence_reference

from graph_os.browser_control.browser_control_api import (
    BrowserCallRequest,
    BrowserChannelBinding,
    CancelCallRequest,
    CatalogRegistrationReceipt,
    IssueLeaseRequest,
    ReconcileCallRequest,
    RenewLeaseRequest,
)
from graph_os.browser_control.browser_control_attendance_api import RecentAuthGrant
from graph_os.browser_control.browser_control_binding import (
    binding_references,
    descriptor_catalog_digest,
    descriptor_tool_scope_digest,
)
from graph_os.browser_control.browser_control_client import (
    CatalogRegisterMessage,
    ControlCancelledMessage,
    ControlConfirmMessage,
    ControlResultMessage,
)
from graph_os.browser_control.browser_control_common import (
    CancellationEffect,
    ConfirmationPolicy,
    MutationClass,
    canonical_json,
    content_sha256,
    schema_sha256,
)
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor
from graph_os.browser_control.browser_control_registration import (
    active_registration_matches,
    register_catalog,
)
from graph_os.browser_control.browser_control_service import (
    BrowserControlService,
    browser_control_factory_kwargs,
)


def _negative_claim() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "claimed": False,
        "reason": "empty",
        "work_item_id": None,
        "kind": None,
        "payload_ref": None,
        "lease_holder_ref": None,
        "lease_epoch": None,
        "fencing_token": None,
        "lease_expires_at_ms": None,
        "attempt": None,
        "max_attempts": None,
        "tenant_in_flight": None,
        "changed_work_item_ids": [],
    }


class _Authority:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.claim_count = 0
        self.claim_requests: list[Any] = []
        self.commit_outcomes: list[str] = []

    def claim_work_item(self, request: Any) -> dict[str, Any]:
        node = self.nodes.get(str(request.work_item_id))
        if node is None or node.get("status") != "ready":
            return _negative_claim()
        self.claim_count += 1
        self.claim_requests.append(request)
        epoch = int(node.get("lease_epoch") or 0) + 1
        node.update(
            status="leased",
            lease_owner=request.worker_ref,
            lease_epoch=epoch,
            fencing_token=epoch,
            attempt=1,
        )
        return {
            "schema_version": "1",
            "claimed": True,
            "reason": "claimed",
            "work_item_id": request.work_item_id,
            "kind": node["kind"],
            "payload_ref": node["payload_ref"],
            "lease_holder_ref": request.worker_ref,
            "lease_epoch": epoch,
            "fencing_token": epoch,
            "lease_expires_at_ms": request.now_ms + request.lease_ms,
            "attempt": 1,
            "max_attempts": 1,
            "tenant_in_flight": 1,
            "changed_work_item_ids": [request.work_item_id],
        }

    def commit_work_item_result(self, request: dict[str, Any]) -> dict[str, Any]:
        node = self.nodes[request["work_item_id"]]
        if node.get("status") in {"succeeded", "failed", "cancelled"}:
            return {"status": "noop"}
        self.commit_outcomes.append(str(request["outcome"]))
        node.update(
            status=str(request["outcome"]),
            result_ref=request.get("result_ref"),
            error_ref=request.get("error_ref"),
        )
        return {"status": "committed"}

    def cancel_work_item(self, request: dict[str, Any]) -> dict[str, Any]:
        node = self.nodes.get(request["work_item_id"])
        if node is None:
            return {"status": "missing"}
        if node.get("status") in {"ready", "submitted"}:
            node["status"] = "cancelled"
            return {"status": "cancelled"}
        return {"status": "in_flight"}


class _EgWorkItems:
    """The generated EG ``work_items`` namespace, over the fake authority."""

    def __init__(self, authority: _Authority) -> None:
        self._authority = authority

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        item_id = request["work_item_id"]
        node = self._authority.nodes.get(item_id)
        created = node is None
        if node is None:
            node = {
                "id": item_id,
                "node_type": "WorkItem",
                "tenant": request["context"]["tenant_id"],
                "kind": request["kind"],
                "status": "ready",
                "payload_ref": request["input_ref"],
                "metadata": dict(request["metadata"]),
                "command_digest": request["command_digest"],
            }
            self._authority.nodes[item_id] = node
        elif node["command_digest"] != request["command_digest"]:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        return {
            "work_item_id": item_id,
            "status": node["status"],
            "created": created,
            "replayed": not created,
        }

    def get(self, *, tenant: str, work_item_id: str) -> dict[str, Any] | None:
        node = self._authority.nodes.get(work_item_id)
        if node is None or node.get("tenant") != tenant:
            return None
        return {
            "work_item_id": work_item_id,
            "kind": node["kind"],
            "status": node["status"],
            "input_ref": node["payload_ref"],
            "metadata": node["metadata"],
            "version": 1,
            "updated_at_ms": 0,
        }

    def get_outcome(self, *, tenant: str, work_item_id: str) -> dict[str, Any] | None:
        view = self.get(tenant=tenant, work_item_id=work_item_id)
        if view is None:
            return None
        node = self._authority.nodes[work_item_id]
        return {
            "work_item": view,
            "trace_ref": node.get("trace_ref"),
            "tool_call_refs": [],
            "outcome_ref": node.get("outcome_ref"),
            "outcome": node.get("outcome"),
        }

    def claim(self, request: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        return self._authority.claim_work_item(SimpleNamespace(**request))

    def commit_result(self, **request: Any) -> dict[str, Any]:
        return self._authority.commit_work_item_result(request)

    def cancel(self, **request: Any) -> dict[str, Any]:
        return self._authority.cancel_work_item(request)


class _EgControlLeases:
    """The generated EG ``control_leases`` namespace, over the fake authority."""

    def __init__(self, authority: _Authority) -> None:
        self._authority = authority

    def issue(self, *, tenant: str, lease_id: str, **request: Any) -> dict[str, Any]:
        if lease_id in self._authority.nodes:
            return {"outcome": "collision", "lease": None}
        assert (
            0
            < request["issued_at_ms"]
            <= request["expires_at_ms"]
            <= request["hard_expires_at_ms"]
        )
        self._authority.nodes[lease_id] = {
            "id": lease_id,
            "node_type": "ControlLease",
            "tenant": tenant,
            "kind": request["kind"],
            "grant": dict(request["grant"]),
            "status": "active",
            "issued_at_ms": request["issued_at_ms"],
            "expires_at_ms": request["expires_at_ms"],
            "hard_expires_at_ms": request["hard_expires_at_ms"],
            "revision": 1,
        }
        return {
            "outcome": "issued",
            "lease": self.get(tenant=tenant, lease_id=lease_id),
        }

    def get(self, *, tenant: str, lease_id: str) -> dict[str, Any] | None:
        node = self._authority.nodes.get(lease_id)
        if node is None or node.get("tenant") != tenant or "grant" not in node:
            return None
        return {
            key: node[key]
            for key in (
                "kind",
                "grant",
                "status",
                "issued_at_ms",
                "expires_at_ms",
                "hard_expires_at_ms",
                "revision",
            )
        } | {"lease_id": lease_id}

    def transition(
        self, *, tenant: str, lease_id: str, expected_revision: int, to: str, **_: Any
    ) -> dict[str, Any]:
        node = self._authority.nodes.get(lease_id)
        if node is None or node.get("tenant") != tenant:
            return {"outcome": "not_found", "lease": None}
        legal = {
            "active": {"consumed", "revoked", "expired"},
            "consumed": {"revoked", "expired"},
        }
        if node["revision"] != expected_revision or to not in legal.get(
            node["status"], set()
        ):
            return {
                "outcome": "conflict",
                "lease": self.get(tenant=tenant, lease_id=lease_id),
            }
        node.update(status=to, revision=expected_revision + 1)
        return {
            "outcome": "applied",
            "lease": self.get(tenant=tenant, lease_id=lease_id),
        }


class _Engine:
    def __init__(self) -> None:
        self._work_item_engine = _Authority()
        self.graph_compute = SimpleNamespace(
            client=SimpleNamespace(
                work_items=_EgWorkItems(self._work_item_engine),
                control_leases=_EgControlLeases(self._work_item_engine),
            )
        )
        self.trace_batches: list[list[dict[str, Any]]] = []
        self.fail_audit = False

    def batch_typed_mutations(
        self, mutations: list[dict[str, Any]], **_kwargs: Any
    ) -> bool:
        if self.fail_audit:
            raise RuntimeError("audit unavailable")
        self.trace_batches.append(mutations)
        for mutation in mutations:
            if mutation.get("kind") == "node":
                node_id = str(mutation["id"])
                self._work_item_engine.nodes[node_id] = {
                    "id": node_id,
                    "node_type": mutation["node_type"],
                    **mutation["properties"],
                }
        return True


class _Policy:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow

    def decide(self, request: Any) -> Any:
        receipt = SimpleNamespace(
            request_digest=request.digest(), receipt_id=f"policy-{request.digest()}"
        )
        return SimpleNamespace(allowed=self.allow, receipt=receipt)


class _Exporter:
    def __init__(self, *, configured: bool, succeeds: bool) -> None:
        self.configured = configured
        self.succeeds = succeeds

    def export_graph_run(self, **_kwargs: Any) -> bool:
        return self.succeeds


class _SessionStatus:
    def __init__(self) -> None:
        self.live = True

    async def __call__(self, _binding: BrowserChannelBinding) -> bool:
        return self.live


@pytest.fixture
def session() -> GraphSession:
    actor = ActorContext(
        actor_id="browser-user",
        actor_type=ActorType.HUMAN,
        roles=("user",),
        tenant_id="tenant-a",
        authenticated=True,
    )
    return GraphSession(
        actor=actor,
        tenant="tenant-a",
        graph="tenant-a",
        scopes=frozenset({"kg:write"}),
        policy_version="policy-7",
        audience="graph-runtime",
    )


def _ref(label: str, value: str) -> str:
    return f"{label}_" + __import__("hashlib").sha256(value.encode()).hexdigest()


async def _live_binding_session() -> bool:
    return True


def _binding(session: GraphSession, **changes: Any) -> BrowserChannelBinding:
    now = float(changes.pop("_now", time.time()))
    tool = changes.pop("_tool", None) or _tool()
    default = BrowserChannelBinding(
        session=session,
        login_session_ref=_ref("login", "session"),
        browser_session_ref=_ref("browser", "browser"),
        principal_ref=persistence_reference(
            "browser_principal", "browser-user", namespace="test"
        ),
        origin="https://webui.example.invalid",
        document_ref=_ref("document", "document"),
        route_id="graph",
        registration_generation=1,
        attended_arm_ref=_ref("attended", "arm"),
        attended_arm_expires_at=now + 600,
        access_token_expires_at=now + 900,
        catalog_digest=descriptor_catalog_digest((tool,)),
        tool_scope_digest=descriptor_tool_scope_digest((tool,)),
        attended_arm_issued_at=now - 1,
        attended_auth_time=now - 2,
        attended_acr="urn:example:acr:step-up",
        attended_issuer="https://identity.example.invalid/realms/graph-os",
        session_revalidator=_live_binding_session,
        attended=True,
    )
    return replace(default, **changes)


def _tool(
    *, mutation: bool = False, nullable_output: bool = False
) -> BrowserToolDescriptor:
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string", "maxLength": 20}},
        "required": ["value"],
        "additionalProperties": False,
    }
    object_output = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    output_schema = (
        {"anyOf": [object_output, {"type": "null"}]}
        if nullable_output
        else object_output
    )
    return BrowserToolDescriptor(
        tool_id="agent-webui.navigate" if mutation else "agent-webui.get-page-context",
        version="1.0.0",
        input_schema=input_schema,
        output_schema=output_schema,
        schema_digest=schema_sha256(input_schema, output_schema),
        mutation_class=(
            MutationClass.LOCAL_UI_MUTATION if mutation else MutationClass.READ
        ),
        confirmation_policy=(
            ConfirmationPolicy.EXACT_REQUEST if mutation else ConfirmationPolicy.NONE
        ),
        required_roles=("user",),
        source_ref="agent-webui:src/lib/webmcp/tools.ts",
    )


def _grant(binding: BrowserChannelBinding) -> RecentAuthGrant:
    return RecentAuthGrant(
        session=binding.session,
        grant_ref=binding.attended_arm_ref,
        login_session_ref=binding.login_session_ref,
        browser_session_ref=binding.browser_session_ref,
        principal_ref=binding.principal_ref,
        origin=binding.origin,
        route_id=binding.route_id,
        access_token_expires_at=binding.access_token_expires_at,
        grant_issued_at=binding.attended_arm_issued_at - 1,
        grant_expires_at=binding.attended_arm_issued_at + 59,
        attended_auth_time=binding.attended_auth_time,
        attended_acr=binding.attended_acr,
        attended_issuer=binding.attended_issuer,
    )


async def _runner(operation: Any) -> Any:
    return operation()


async def _live_session(_binding: BrowserChannelBinding) -> bool:
    return await _live_binding_session()


class _Sent(list[Any]):
    async def append_async(self, value: Any) -> None:
        self.append(value)


async def _setup(
    session: GraphSession,
    *,
    mutation: bool = False,
    nullable_output: bool = False,
    exporter: _Exporter | None = None,
) -> tuple[BrowserControlService, Any, _Engine, _Sent, BrowserToolDescriptor]:
    engine = _Engine()
    sent = _Sent()
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=exporter or _Exporter(configured=False, succeeds=False),
    )
    tool = _tool(mutation=mutation, nullable_output=nullable_output)
    binding = _binding(session, _tool=tool)
    await service.finalize_attended_arm(_grant(binding), binding)
    connection = await service.open_channel(binding, sent.append_async)
    registration = await connection.receive(
        CatalogRegisterMessage(
            authority="browser-local",
            route_id=binding.route_id,
            registration_generation=binding.registration_generation,
            catalog_digest=binding.catalog_digest,
            tool_scope_digest=binding.tool_scope_digest,
            tools=(tool,),
        )
    )
    assert registration == CatalogRegistrationReceipt(
        route_id=binding.route_id,
        registration_generation=binding.registration_generation,
        catalog_digest=binding.catalog_digest,
        tool_scope_digest=binding.tool_scope_digest,
    )
    return service, connection, engine, sent, tool


def test_factory_implements_the_real_webui_port_protocol() -> None:
    from agent_webui.browser_control import browser_control_port_enabled

    def app_factory(*, browser_control: Any = None) -> Any:
        return browser_control

    kwargs = browser_control_factory_kwargs(
        app_factory,
        _Engine(),
        _runner,
        session_revalidator=_live_session,
    )

    assert browser_control_port_enabled(kwargs["browser_control"])


async def _lease(
    service: BrowserControlService,
    binding: BrowserChannelBinding,
    tool: BrowserToolDescriptor,
) -> Any:
    return await service.issue_lease(
        IssueLeaseRequest(
            document_ref=binding.document_ref,
            tool_ids=(tool.tool_id,),
            attended=True,
        )
    )


@pytest.mark.asyncio
async def test_read_call_is_audited_and_claimed_before_one_dispatch(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        task = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-1",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        assert sent[0].type == "control.call"
        assert sent[0].authorization == "read"
        assert sent[0].confirmation_digest is None
        assert engine.trace_batches, "pending audit must precede dispatch"
        assert engine._work_item_engine.claim_count == 1
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id, status="succeeded", result={"ok": True}
            )
        )
        receipt = await task
    assert receipt.status == "succeeded"
    assert receipt.effect is CancellationEffect.BROWSER_REPORTED_COMMITTED
    assert receipt.langfuse_status == "not_configured"


@pytest.mark.asyncio
async def test_cancelled_caller_terminalizes_claimed_work_item(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="cancelled-caller",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        execution.cancel()
        await _wait_for_messages(sent, 2)
        await connection.receive(
            ControlCancelledMessage(
                call_id=sent[0].call_id,
                effect=CancellationEffect.NONE,
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await execution
    call_items = [
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.control.call"
    ]
    assert call_items[0]["status"] == "cancelled"
    assert service._active_calls == {}


@pytest.mark.asyncio
async def test_ambiguous_fence_admission_recovers_only_owned_work_item(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.browser_control import browser_control_cancellation as cancellation
    from graph_os.browser_control import browser_control_durability as durability

    original_get = durability.get_work_item
    failed_after_create = False

    def fail_first_read_after_create(engine: Any, tenant: str, item_id: str) -> Any:
        nonlocal failed_after_create
        if not failed_after_create and item_id in engine._work_item_engine.nodes:
            failed_after_create = True
            raise RuntimeError("injected post-admission read failure")
        return original_get(engine, tenant, item_id)

    monkeypatch.setattr(durability, "get_work_item", fail_first_read_after_create)
    monkeypatch.setattr(cancellation, "_FENCE_RECOVERY_SECONDS", 0.0)
    with use_session(session):
        service, _connection, engine, _sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        with pytest.raises(RuntimeError, match="post-admission"):
            await service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="ambiguous-admission",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        for _ in range(100):
            rows = [
                node
                for node in engine._work_item_engine.nodes.values()
                if node.get("kind") == "browser.control.call"
            ]
            if rows and rows[0].get("status") == "cancelled":
                break
            await asyncio.sleep(0)
    call_items = [
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.control.call"
    ]
    assert failed_after_create
    assert call_items[0]["status"] == "cancelled"
    assert service._active_calls == {}


@pytest.mark.asyncio
async def test_mutation_waits_for_durable_exact_confirmation(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session, mutation=True)
        lease = await _lease(service, _binding(session), tool)
        task = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-2",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        prompt = sent[0]
        assert prompt.type == "control.confirmation_request"
        assert engine._work_item_engine.claim_count == 0
        await connection.receive(
            ControlConfirmMessage(
                call_id=prompt.call_id,
                confirmation_digest=prompt.confirmation_digest,
            )
        )
        await _wait_for_messages(sent, 2)
        call = sent[1]
        assert call.type == "control.call"
        assert call.authorization == "confirmed_mutation"
        assert call.confirmation_digest == prompt.confirmation_digest
        assert engine._work_item_engine.claim_count == 1
        assert any(
            mutation["properties"].get("status") == "confirmed"
            for batch in engine.trace_batches
            for mutation in batch
            if mutation["kind"] == "node"
        )
        await connection.receive(
            ControlResultMessage(
                call_id=call.call_id, status="succeeded", result={"ok": True}
            )
        )
        assert (await task).status == "succeeded"


@pytest.mark.asyncio
async def test_audit_failure_prevents_dispatch_and_reaps_owned_fence(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.browser_control import browser_control_outcome

    monkeypatch.setattr(browser_control_outcome, "_UNKNOWN_RECONCILE_SECONDS", 0.0)
    with use_session(session):
        service, _connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        engine.fail_audit = True
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-3",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        original_cancel = engine._work_item_engine.cancel_work_item
        cancel_attempts = 0

        def fail_first_cancel(request: dict[str, Any]) -> dict[str, Any]:
            nonlocal cancel_attempts
            cancel_attempts += 1
            if cancel_attempts == 1:
                return {"status": "in_flight"}
            return original_cancel(request)

        monkeypatch.setattr(
            engine._work_item_engine, "cancel_work_item", fail_first_cancel
        )
        engine.fail_audit = False
        for _ in range(100):
            rows = [
                node
                for node in engine._work_item_engine.nodes.values()
                if node.get("kind") == "browser.control.call"
            ]
            if rows and rows[0].get("status") == "cancelled":
                break
            await asyncio.sleep(0)
    assert sent == []
    assert engine._work_item_engine.claim_count == 0
    assert cancel_attempts == 2
    assert rows[0]["status"] == "cancelled"
    assert service._active_calls == {}


@pytest.mark.asyncio
async def test_terminal_audit_failure_schedules_durable_reconciliation(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="terminal-audit-failure",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        engine.fail_audit = True
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        receipt = await execution
        active = service._active_calls[receipt.call_id]
        assert active.reaper_task is not None
        assert not active.reaper_task.done()
        active.reaper_task.cancel()
        await asyncio.gather(active.reaper_task, return_exceptions=True)
    assert receipt.status == "unknown"
    assert receipt.error_code == "durable_outcome_unavailable"
    assert engine._work_item_engine.claim_count == 1


@pytest.mark.asyncio
async def test_channel_refuses_second_catalog_registration(
    session: GraphSession,
) -> None:
    with use_session(session):
        _service, connection, _engine, _sent, tool = await _setup(session)
        with pytest.raises(RuntimeError, match="already registered"):
            await connection.receive(
                CatalogRegisterMessage(
                    authority="browser-local",
                    route_id="graph",
                    registration_generation=1,
                    catalog_digest=descriptor_catalog_digest((tool,)),
                    tool_scope_digest=descriptor_tool_scope_digest((tool,)),
                    tools=(tool,),
                )
            )


@pytest.mark.asyncio
async def test_disconnect_durably_retires_current_registration(
    session: GraphSession,
) -> None:
    with use_session(session):
        _service, connection, engine, _sent, _tool_value = await _setup(session)
        await connection.disconnect("browser_navigation")
    registration = next(
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.registration"
    )
    arm = next(
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.attended_arm"
    )
    assert registration["status"] == "revoked"
    assert arm["status"] == "revoked"


@pytest.mark.asyncio
async def test_registration_recomputes_metadata_digest_before_authority(
    session: GraphSession,
) -> None:
    engine = _Engine()
    sent = _Sent()
    tool = _tool()
    binding = _binding(session, _tool=tool)
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    changed = tool.model_copy(
        update={
            "mutation_class": MutationClass.LOCAL_UI_MUTATION,
            "confirmation_policy": ConfirmationPolicy.EXACT_REQUEST,
        }
    )
    with use_session(session):
        await service.finalize_attended_arm(_grant(binding), binding)
        connection = await service.open_channel(binding, sent.append_async)
        with pytest.raises(PermissionError, match="digest claim"):
            await connection.receive(
                CatalogRegisterMessage(
                    authority="browser-local",
                    route_id=binding.route_id,
                    registration_generation=binding.registration_generation,
                    catalog_digest=binding.catalog_digest,
                    tool_scope_digest=binding.tool_scope_digest,
                    tools=(changed,),
                )
            )
    assert not any(
        node.get("kind") == "browser.registration"
        for node in engine._work_item_engine.nodes.values()
    )


@pytest.mark.asyncio
async def test_attended_receipt_is_single_use_across_replicas(
    session: GraphSession,
) -> None:
    engine = _Engine()
    tool = _tool()
    binding = _binding(session, _tool=tool)
    first = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    second = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    with use_session(session):
        await first.finalize_attended_arm(_grant(binding), binding)
        await first.open_channel(binding, _Sent().append_async)
        with pytest.raises(PermissionError, match="replayed|active"):
            await second.open_channel(binding, _Sent().append_async)


@pytest.mark.asyncio
async def test_finalized_arm_can_be_revoked_before_channel_open(
    session: GraphSession,
) -> None:
    engine = _Engine()
    tool = _tool()
    binding = _binding(session, _tool=tool)
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    with use_session(session):
        await service.finalize_attended_arm(_grant(binding), binding)
        receipt = await service.revoke_attended_arm(binding)
        repeated = await service.revoke_attended_arm(binding)
        with pytest.raises(PermissionError, match="not active"):
            await service.open_channel(binding, _Sent().append_async)
    assert receipt.status == "revoked"
    assert repeated == receipt


@pytest.mark.asyncio
async def test_expired_arm_revocation_does_not_require_live_backchannel(
    session: GraphSession,
) -> None:
    engine = _Engine()
    tool = _tool()
    binding = _binding(session, _tool=tool)
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    with use_session(session):
        await service.finalize_attended_arm(_grant(binding), binding)

        async def unavailable(_binding: BrowserChannelBinding) -> bool:
            raise RuntimeError("identity provider unavailable")

        service._session_revalidator = unavailable
        service._clock = lambda: binding.attended_arm_expires_at + 1
        receipt = await service.revoke_attended_arm(binding)
    assert receipt.status == "revoked"


@pytest.mark.asyncio
async def test_finalize_rejects_pre_redirect_route_snapshot(
    session: GraphSession,
) -> None:
    engine = _Engine()
    tool = _tool()
    binding = _binding(session, _tool=tool)
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    stale_grant = replace(_grant(binding), route_id="pre-redirect")
    with use_session(session):
        with pytest.raises(PermissionError, match="final binding"):
            await service.finalize_attended_arm(stale_grant, binding)
    assert engine._work_item_engine.nodes == {}


@pytest.mark.asyncio
async def test_live_session_failure_closes_channel_before_catalog(
    session: GraphSession,
) -> None:
    session_status = _SessionStatus()

    engine = _Engine()
    tool = _tool()
    binding = _binding(session, _tool=tool)
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=session_status,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    with use_session(session):
        await service.finalize_attended_arm(_grant(binding), binding)
        connection = await service.open_channel(binding, _Sent().append_async)
        session_status.live = False
        with pytest.raises(PermissionError, match="no longer live"):
            await connection.receive(
                CatalogRegisterMessage(
                    authority="browser-local",
                    route_id=binding.route_id,
                    registration_generation=binding.registration_generation,
                    catalog_digest=binding.catalog_digest,
                    tool_scope_digest=binding.tool_scope_digest,
                    tools=(tool,),
                )
            )
        with pytest.raises(RuntimeError, match="closed"):
            await connection.receive(
                CatalogRegisterMessage(
                    authority="browser-local",
                    route_id=binding.route_id,
                    registration_generation=binding.registration_generation,
                    catalog_digest=binding.catalog_digest,
                    tool_scope_digest=binding.tool_scope_digest,
                    tools=(tool,),
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "succeeds", "expected"),
    [
        (False, False, "not_configured"),
        (True, False, "unavailable"),
        (True, True, "recorded"),
    ],
)
async def test_langfuse_status_is_truthful(
    session: GraphSession, configured: bool, succeeds: bool, expected: str
) -> None:
    with use_session(session):
        exporter = _Exporter(configured=configured, succeeds=succeeds)
        service, connection, _engine, sent, tool = await _setup(
            session, exporter=exporter
        )
        lease = await _lease(service, _binding(session), tool)
        task = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-langfuse",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id, status="succeeded", result={"ok": True}
            )
        )
        receipt = await task
    assert receipt.langfuse_status == expected


@pytest.mark.asyncio
async def test_stale_generation_and_identity_are_refused(session: GraphSession) -> None:
    with use_session(session):
        service, _connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        document = next(
            node
            for node in engine._work_item_engine.nodes.values()
            if node.get("kind") == "browser.document"
        )
        document["grant"]["registration_generation"] = 2
        with pytest.raises(PermissionError, match="stale"):
            await service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-stale",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
    assert sent == []

    other = replace(session, actor=replace(session.actor, actor_id="other-user"))
    with use_session(other), pytest.raises(PermissionError):
        await service.issue_lease(
            IssueLeaseRequest(
                document_ref=_binding(session).document_ref,
                tool_ids=(tool.tool_id,),
                attended=True,
            )
        )


@pytest.mark.asyncio
async def test_browser_authority_requires_verified_human_actor(
    session: GraphSession,
) -> None:
    agent_session = replace(
        session,
        actor=replace(session.actor, actor_type=ActorType.AI_AGENT),
    )
    service = BrowserControlService(
        _Engine(),
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(allow=True),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
    )
    with use_session(agent_session), pytest.raises(PermissionError, match="human"):
        await service._validate_binding_authority(_binding(agent_session))


@pytest.mark.asyncio
async def test_every_caller_action_rejects_actor_drift(session: GraphSession) -> None:
    with use_session(session):
        service, connection, _engine, sent, tool = await _setup(session)
        binding = _binding(session)
        lease = await _lease(service, binding, tool)
    other = replace(session, actor=replace(session.actor, actor_id="other-user"))
    with use_session(other):
        with pytest.raises(PermissionError):
            await service.issue_lease(
                IssueLeaseRequest(
                    document_ref=binding.document_ref,
                    tool_ids=(tool.tool_id,),
                    attended=True,
                )
            )
        with pytest.raises(PermissionError):
            await service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="wrong-actor",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
    with use_session(session):
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="active-identity",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        call_id = sent[0].call_id
    with use_session(other):
        with pytest.raises(PermissionError):
            await service.cancel_call(
                CancelCallRequest(call_id=call_id, reason="operator_cancel")
            )
        with pytest.raises(PermissionError):
            await service.reconcile_call(ReconcileCallRequest(call_id=call_id))
    with use_session(session):
        await connection.receive(
            ControlResultMessage(
                call_id=call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        await execution
    with use_session(other):
        with pytest.raises(PermissionError):
            await service.cancel_call(
                CancelCallRequest(call_id=call_id, reason="operator_cancel")
            )
        with pytest.raises(PermissionError):
            await service.reconcile_call(ReconcileCallRequest(call_id=call_id))


@pytest.mark.asyncio
async def test_browser_event_revalidates_live_generation(session: GraphSession) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-event-revalidation",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        document = next(
            node
            for node in engine._work_item_engine.nodes.values()
            if node.get("kind") == "browser.document"
        )
        document["grant"]["registration_generation"] = 2
        result = ControlResultMessage(
            call_id=sent[0].call_id,
            status="succeeded",
            result={"ok": True},
        )
        with pytest.raises(PermissionError, match="stale"):
            await connection.receive(result)
        document["grant"]["registration_generation"] = 1
        await connection.receive(result)
        assert (await execution).status == "succeeded"


@pytest.mark.asyncio
async def test_unconfirmed_cancel_reports_none_and_never_dispatches(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, _connection, engine, sent, tool = await _setup(session, mutation=True)
        lease = await _lease(service, _binding(session), tool)
        task = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-cancel",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                    timeout_seconds=10,
                )
            )
        )
        await _wait_for_messages(sent, 1)
        receipt = await service.cancel_call(
            CancelCallRequest(call_id=sent[0].call_id, reason="operator_cancel")
        )
        execute_receipt = await task
    assert receipt.effect is CancellationEffect.NONE
    assert receipt.status == "cancelled"
    assert execute_receipt == receipt
    assert engine._work_item_engine.claim_count == 0
    assert [message.type for message in sent] == ["control.confirmation_request"]


@pytest.mark.asyncio
async def test_cancel_after_claim_wins_before_browser_dispatch(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with use_session(session):
        service, _connection, _engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        entered = asyncio.Event()
        release = asyncio.Event()
        original = service._dispatch

        async def pause_before_dispatch(
            active: Any, *, authority_deadline: float
        ) -> bool:
            entered.set()
            await release.wait()
            return await original(active, authority_deadline=authority_deadline)

        monkeypatch.setattr(service, "_dispatch", pause_before_dispatch)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="cancel-before-dispatch",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await entered.wait()
        call_id = next(iter(service._active_calls))
        cancelled = await service.cancel_call(
            CancelCallRequest(call_id=call_id, reason="operator_cancel")
        )
        release.set()
        completed = await execution
    assert cancelled.status == "cancelled"
    assert completed == cancelled
    assert sent == []


@pytest.mark.asyncio
async def test_terminal_audit_cannot_be_overwritten_by_dispatched_audit(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        entered = asyncio.Event()
        release = asyncio.Event()
        original = service._audit_lifecycle

        async def pause_dispatched(active: Any, *, status: str) -> Any:
            if status == "dispatched":
                entered.set()
                await release.wait()
            return await original(active, status=status)

        monkeypatch.setattr(service, "_audit_lifecycle", pause_dispatched)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="terminal-audit-order",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await entered.wait()
        cancellation = asyncio.create_task(
            service.cancel_call(
                CancelCallRequest(call_id=sent[0].call_id, reason="operator_cancel")
            )
        )
        await _wait_for_messages(sent, 2)
        await connection.receive(
            ControlCancelledMessage(
                call_id=sent[0].call_id,
                effect=CancellationEffect.NONE,
            )
        )
        cancelled = await cancellation
        release.set()
        assert await execution == cancelled
    statuses = [
        mutation["properties"].get("status")
        for batch in engine.trace_batches
        for mutation in batch
        if mutation["kind"] == "node"
    ]
    assert statuses[-1] == "cancelled"
    assert "dispatched" not in statuses[statuses.index("cancelled") + 1 :]


@pytest.mark.asyncio
async def test_claim_is_followed_by_immediate_live_revalidation(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with use_session(session):
        service, _connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        session_status = _SessionStatus()

        original_claim = engine._work_item_engine.claim_work_item

        def expire_after_claim(request: Any) -> dict[str, Any]:
            claim = original_claim(request)
            session_status.live = False
            return claim

        service._session_revalidator = session_status
        monkeypatch.setattr(
            engine._work_item_engine, "claim_work_item", expire_after_claim
        )
        receipt = await service.execute_call(
            BrowserCallRequest(
                lease_id=lease.lease_id,
                request_id="expire-after-claim",
                tool_id=tool.tool_id,
                schema_digest=tool.schema_digest,
                arguments={"value": "safe"},
            )
        )
    assert receipt.status == "cancelled"
    assert sent == []


@pytest.mark.asyncio
async def test_policy_denial_is_audited_without_fence_or_dispatch(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, _connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        service._policy.allow = False
        receipt = await service.execute_call(
            BrowserCallRequest(
                lease_id=lease.lease_id,
                request_id="request-denied",
                tool_id=tool.tool_id,
                schema_digest=tool.schema_digest,
                arguments={"value": "safe"},
            )
        )
    assert receipt.status == "denied"
    assert receipt.error_code == "policy_denied"
    assert sent == []
    assert engine._work_item_engine.claim_count == 0
    assert any(
        mutation["properties"].get("status") == "denied"
        for batch in engine.trace_batches
        for mutation in batch
        if mutation["kind"] == "node"
    )


@pytest.mark.asyncio
async def test_duplicate_delivery_never_dispatches_twice(session: GraphSession) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        request = BrowserCallRequest(
            lease_id=lease.lease_id,
            request_id="request-duplicate",
            tool_id=tool.tool_id,
            schema_digest=tool.schema_digest,
            arguments={"value": "safe"},
        )
        first = asyncio.create_task(service.execute_call(request))
        await _wait_for_messages(sent, 1)
        replay = await service.execute_call(request)
        assert replay.status == "dispatched"
        assert len(sent) == 1
        assert engine._work_item_engine.claim_count == 1
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        assert (await first).status == "succeeded"


@pytest.mark.asyncio
async def test_unknown_cancel_accepts_late_result_and_reconciles(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, _engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-late-result",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                    timeout_seconds=10,
                )
            )
        )
        await _wait_for_messages(sent, 1)
        cancellation = asyncio.create_task(
            service.cancel_call(
                CancelCallRequest(call_id=sent[0].call_id, reason="operator_cancel")
            )
        )
        await _wait_for_messages(sent, 2)
        await connection.receive(
            ControlCancelledMessage(
                call_id=sent[0].call_id,
                effect=CancellationEffect.UNKNOWN,
            )
        )
        uncertain = await cancellation
        assert uncertain.status == "unknown"
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        assert (await execution).status == "succeeded"
        reconciled = await service.reconcile_call(
            ReconcileCallRequest(call_id=sent[0].call_id)
        )
    assert reconciled.status == "succeeded"
    assert reconciled.effect is CancellationEffect.BROWSER_REPORTED_COMMITTED


@pytest.mark.asyncio
async def test_browser_reported_commit_cancel_wakes_execution(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, _engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="request-commit-cancel",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                    timeout_seconds=10,
                )
            )
        )
        await _wait_for_messages(sent, 1)
        cancellation = asyncio.create_task(
            service.cancel_call(
                CancelCallRequest(call_id=sent[0].call_id, reason="operator_cancel")
            )
        )
        await _wait_for_messages(sent, 2)
        await connection.receive(
            ControlCancelledMessage(
                call_id=sent[0].call_id,
                effect=CancellationEffect.BROWSER_REPORTED_COMMITTED,
            )
        )
        cancel_receipt = await cancellation
        execute_receipt = await execution
    assert cancel_receipt.status == "succeeded"
    assert execute_receipt == cancel_receipt


@pytest.mark.asyncio
async def test_renewal_requires_fresh_step_up_and_channel(
    session: GraphSession,
) -> None:
    now = 1_000.0
    with use_session(session):
        engine = _Engine()
        sent = _Sent()
        service = BrowserControlService(
            engine,
            sync_runner=_runner,
            session_revalidator=_live_session,
            action_policy=_Policy(),
            langfuse_exporter=_Exporter(configured=False, succeeds=False),
            clock=lambda: now,
        )
        tool = _tool()
        binding = _binding(session, _now=now, _tool=tool)
        await service.finalize_attended_arm(_grant(binding), binding)
        connection = await service.open_channel(binding, sent.append_async)
        await connection.receive(
            CatalogRegisterMessage(
                authority="browser-local",
                route_id=binding.route_id,
                registration_generation=binding.registration_generation,
                catalog_digest=binding.catalog_digest,
                tool_scope_digest=binding.tool_scope_digest,
                tools=(tool,),
            )
        )
        lease = await _lease(service, binding, tool)
        with pytest.raises(PermissionError, match="fresh step-up"):
            await service.renew_lease(
                RenewLeaseRequest(
                    lease_id=lease.lease_id,
                    requested_ttl_seconds=300,
                    attended=True,
                )
            )
    assert lease.expires_at <= binding.attended_arm_expires_at


@pytest.mark.asyncio
async def test_expired_arm_expires_lease_before_call(session: GraphSession) -> None:
    now = 1_000.0
    engine = _Engine()
    sent = _Sent()
    tool = _tool()
    binding = _binding(session, _now=now, _tool=tool)
    service = BrowserControlService(
        engine,
        sync_runner=_runner,
        session_revalidator=_live_session,
        action_policy=_Policy(),
        langfuse_exporter=_Exporter(configured=False, succeeds=False),
        clock=lambda: now,
    )
    with use_session(session):
        await service.finalize_attended_arm(_grant(binding), binding)
        connection = await service.open_channel(binding, sent.append_async)
        await connection.receive(
            CatalogRegisterMessage(
                authority="browser-local",
                route_id=binding.route_id,
                registration_generation=binding.registration_generation,
                catalog_digest=binding.catalog_digest,
                tool_scope_digest=binding.tool_scope_digest,
                tools=(tool,),
            )
        )
        lease = await _lease(service, binding, tool)
        now = binding.attended_arm_expires_at
        with pytest.raises(PermissionError, match="authority expired"):
            await service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="expired-arm",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
    assert engine._work_item_engine.nodes[lease.lease_id]["status"] == "expired"
    assert sent == []


def test_canonical_json_matches_shared_browser_vector() -> None:
    value = {
        "z": [True, None, 9_007_199_254_740_991, -9_007_199_254_740_991],
        "a": "café 雪 😀",
        "\ue000": "bmp-private-use",
        "😀": "astral",
        "nested": {"beta": 2, "alpha": 1},
    }
    expected = (
        '{"a":"café 雪 😀","nested":{"alpha":1,"beta":2},'
        '"z":[true,null,9007199254740991,-9007199254740991],'
        '"😀":"astral","\ue000":"bmp-private-use"}'
    )
    assert canonical_json(value) == expected
    assert (
        f"sha256:{content_sha256(value)}"
        == "sha256:d8fc415c3a0c2de5f586eed3e8a6be46807b24ef0110f398e8a2157425df1862"
    )


@pytest.mark.parametrize(
    "value",
    [1.0, -0.0, 9_007_199_254_740_992, "\ud800", {"\udfff": "invalid"}],
)
def test_canonical_json_rejects_cross_runtime_ambiguous_values(value: Any) -> None:
    with pytest.raises(ValueError):
        canonical_json(value)


def test_result_error_code_matches_browser_wire_machine_code() -> None:
    accepted = ControlResultMessage(
        call_id="browsercall_0123456789abcdef0123456789abcdef",
        status="failed",
        error_code="browser-execution_failed",
    )
    assert accepted.error_code == "browser-execution_failed"
    with pytest.raises(ValueError):
        ControlResultMessage(
            call_id="browsercall_0123456789abcdef0123456789abcdef",
            status="failed",
            error_code="browser.execution.failed",
        )


@pytest.mark.asyncio
async def test_claim_lifetime_is_capped_to_remaining_authority(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        binding = _binding(session)
        lease = await _lease(service, binding, tool)
        lease_node = engine._work_item_engine.nodes[lease.lease_id]
        lease_node["expires_at_ms"] = int((time.time() + 1.0) * 1000)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="bounded-claim",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                    timeout_seconds=120,
                )
            )
        )
        await _wait_for_messages(sent, 1)
        assert engine._work_item_engine.claim_requests[0].lease_ms <= 1_000
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        assert (await execution).status == "succeeded"


@pytest.mark.asyncio
async def test_cached_reconcile_revalidates_live_session(session: GraphSession) -> None:
    session_status = _SessionStatus()

    with use_session(session):
        engine = _Engine()
        service = BrowserControlService(
            engine,
            sync_runner=_runner,
            session_revalidator=session_status,
            action_policy=_Policy(),
            langfuse_exporter=_Exporter(configured=False, succeeds=False),
        )
        sent = _Sent()
        tool = _tool()
        binding = _binding(session, _tool=tool)
        await service.finalize_attended_arm(_grant(binding), binding)
        connection = await service.open_channel(binding, sent.append_async)
        await connection.receive(
            CatalogRegisterMessage(
                authority="browser-local",
                route_id=binding.route_id,
                registration_generation=binding.registration_generation,
                catalog_digest=binding.catalog_digest,
                tool_scope_digest=binding.tool_scope_digest,
                tools=(tool,),
            )
        )
        lease = await _lease(service, binding, tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="cached-authority",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        receipt = await execution
        session_status.live = False
        with pytest.raises(PermissionError, match="no longer live"):
            await service.reconcile_call(ReconcileCallRequest(call_id=receipt.call_id))


@pytest.mark.asyncio
async def test_claimed_cancellation_commits_native_cancelled_outcome(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="native-cancelled",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        cancellation = asyncio.create_task(
            service.cancel_call(
                CancelCallRequest(call_id=sent[0].call_id, reason="operator_cancel")
            )
        )
        await _wait_for_messages(sent, 2)
        await connection.receive(
            ControlCancelledMessage(
                call_id=sent[0].call_id,
                effect=CancellationEffect.NONE,
            )
        )
        receipt = await cancellation
        assert await execution == receipt
    assert receipt.status == "cancelled"
    assert engine._work_item_engine.commit_outcomes[-1] == "cancelled"


@pytest.mark.asyncio
async def _complete_then_forget(
    session: GraphSession, request_id: str
) -> tuple[Any, Any, _Engine, Any, list[Any], BrowserCallRequest]:
    service, connection, engine, sent, tool = await _setup(session)
    lease = await _lease(service, _binding(session), tool)
    request = BrowserCallRequest(
        lease_id=lease.lease_id,
        request_id=request_id,
        tool_id=tool.tool_id,
        schema_digest=tool.schema_digest,
        arguments={"value": "safe"},
    )
    execution = asyncio.create_task(service.execute_call(request))
    await _wait_for_messages(sent, 1)
    await connection.receive(
        ControlResultMessage(
            call_id=sent[0].call_id, status="succeeded", result={"ok": True}
        )
    )
    original = await execution
    service._completed.clear()
    service._completed_authority.clear()
    return service, original, engine, connection, sent, request


async def test_durable_replay_recovers_the_committed_outcome(
    session: GraphSession,
) -> None:
    """Replay reads EG GetWorkItemOutcome, never a caller-named node."""
    with use_session(session):
        service, original, engine, _c, sent, request = await _complete_then_forget(
            session, "durable-replay"
        )
        nodes = engine._work_item_engine.nodes
        committed = next(
            node
            for node in nodes.values()
            if node.get("node_type") == "OutcomeEvaluation"
            and node.get("status") == "succeeded"
        )
        call_item = next(
            node
            for node in nodes.values()
            if node.get("kind") == "browser.control.call"
        )
        call_item["outcome"] = dict(committed)
        replay = await service.execute_call(request)
    assert replay.status == "succeeded"
    assert replay.result is None
    assert replay.result_digest == original.result_digest
    assert len(sent) == 1


async def test_replay_without_a_committed_outcome_is_unknown_not_redispatched(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, _original, _engine, _c, sent, request = await _complete_then_forget(
            session, "durable-replay-unbound"
        )
        replay = await service.execute_call(request)
    assert replay.status == "unknown"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_successful_null_result_has_durable_content_digest(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, _engine, sent, tool = await _setup(
            session, nullable_output=True
        )
        lease = await _lease(service, _binding(session, _tool=tool), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="null-result",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result=None,
            )
        )
        receipt = await execution
    assert receipt.result is None
    assert receipt.result_digest == f"sha256:{content_sha256(None)}"


@pytest.mark.asyncio
async def test_replay_rejects_changed_work_item_envelope(
    session: GraphSession,
) -> None:
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        request = BrowserCallRequest(
            lease_id=lease.lease_id,
            request_id="tampered-fence",
            tool_id=tool.tool_id,
            schema_digest=tool.schema_digest,
            arguments={"value": "safe"},
        )
        execution = asyncio.create_task(service.execute_call(request))
        await _wait_for_messages(sent, 1)
        work_item = next(
            node
            for node in engine._work_item_engine.nodes.values()
            if node.get("kind") == "browser.control.call"
        )
        original_input = work_item["payload_ref"]
        work_item["payload_ref"] = "changed"
        with pytest.raises(PermissionError, match="durable fence"):
            await service.execute_call(request)
        work_item["payload_ref"] = original_input
        await connection.receive(
            ControlResultMessage(
                call_id=sent[0].call_id,
                status="succeeded",
                result={"ok": True},
            )
        )
        assert (await execution).status == "succeeded"


@pytest.mark.asyncio
async def test_unknown_call_is_reaped_after_durable_reconciliation_audit(
    session: GraphSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graph_os.browser_control import browser_control_outcome

    monkeypatch.setattr(browser_control_outcome, "_UNKNOWN_RECONCILE_SECONDS", 0.0)
    with use_session(session):
        service, connection, engine, sent, tool = await _setup(session)
        lease = await _lease(service, _binding(session), tool)
        execution = asyncio.create_task(
            service.execute_call(
                BrowserCallRequest(
                    lease_id=lease.lease_id,
                    request_id="unknown-reaper",
                    tool_id=tool.tool_id,
                    schema_digest=tool.schema_digest,
                    arguments={"value": "safe"},
                )
            )
        )
        await _wait_for_messages(sent, 1)
        cancellation = asyncio.create_task(
            service.cancel_call(
                CancelCallRequest(call_id=sent[0].call_id, reason="operator_cancel")
            )
        )
        await _wait_for_messages(sent, 2)
        await connection.receive(
            ControlCancelledMessage(
                call_id=sent[0].call_id,
                effect=CancellationEffect.UNKNOWN,
            )
        )
        assert (await cancellation).status == "unknown"
        reaped = await execution
    assert reaped.error_code == "reconciliation_window_expired"
    assert reaped.call_id not in service._active_calls
    call_items = [
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.control.call"
    ]
    assert len(call_items) == 1
    assert call_items[0]["status"] == "failed"
    assert any(
        mutation["properties"].get("status") == "unknown_reaped"
        for batch in engine.trace_batches
        for mutation in batch
        if mutation["kind"] == "node"
    )


def _records(engine: _Engine, kind: str) -> list[dict[str, Any]]:
    return [
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == kind
    ]


def _two_generations(session: GraphSession, label: str) -> tuple[Any, Any, Any]:
    tool = _tool()
    first = _binding(session, _tool=tool)
    second = replace(
        first,
        registration_generation=2,
        attended_arm_ref=_ref("attended", label),
    )
    return tool, first, second


def test_new_registration_retires_prior_document_generation(
    session: GraphSession,
) -> None:
    engine = _Engine()
    tool, first, second = _two_generations(session, "second-arm")
    now = time.time()
    with use_session(session):
        register_catalog(engine, binding_references(first), (tool,), now=now)
        register_catalog(engine, binding_references(second), (tool,), now=now)
    registrations = _records(engine, "browser.registration")
    documents = _records(engine, "browser.document")
    assert sorted(node["status"] for node in registrations) == ["active", "revoked"]
    assert sorted(node["status"] for node in documents) == ["active", "revoked"]


def test_registration_retry_completes_interrupted_retirement(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    leases = engine.graph_compute.client.control_leases
    tool, first, second = _two_generations(session, "retry-arm")
    now = time.time()
    original = leases.transition
    failed = False

    def fail_first_registration_revoke(**request: Any) -> dict[str, Any]:
        nonlocal failed
        node = engine._work_item_engine.nodes.get(request["lease_id"], {})
        if node.get("kind") == "browser.registration" and not failed:
            failed = True
            return {"outcome": "conflict", "lease": None}
        return original(**request)

    with use_session(session):
        register_catalog(engine, binding_references(first), (tool,), now=now)
        monkeypatch.setattr(leases, "transition", fail_first_registration_revoke)
        with pytest.raises(RuntimeError, match="not retired"):
            register_catalog(engine, binding_references(second), (tool,), now=now)
        interrupted = _records(engine, "browser.registration")
        assert sorted(node["status"] for node in interrupted) == ["active", "active"]
        assert not active_registration_matches(
            engine,
            binding_references(first),
            first.catalog_digest,
            first.tool_scope_digest,
        )
        assert active_registration_matches(
            engine,
            binding_references(second),
            second.catalog_digest,
            second.tool_scope_digest,
        )
        register_catalog(engine, binding_references(second), (tool,), now=now)
    registrations = _records(engine, "browser.registration")
    assert sorted(node["status"] for node in registrations) == ["active", "revoked"]


def test_failed_document_issue_leaves_new_registration_unpointed(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    leases = engine.graph_compute.client.control_leases
    tool, first, second = _two_generations(session, "failed-activation-arm")
    now = time.time()
    original = leases.issue

    def fail_document_issue(**request: Any) -> dict[str, Any]:
        if request["kind"] == "browser.document":
            raise RuntimeError("document record unavailable")
        return original(**request)

    with use_session(session):
        register_catalog(engine, binding_references(first), (tool,), now=now)
        monkeypatch.setattr(leases, "issue", fail_document_issue)
        with pytest.raises(RuntimeError, match="document record unavailable"):
            register_catalog(engine, binding_references(second), (tool,), now=now)
        assert active_registration_matches(
            engine,
            binding_references(first),
            first.catalog_digest,
            first.tool_scope_digest,
        )
        assert not active_registration_matches(
            engine,
            binding_references(second),
            second.catalog_digest,
            second.tool_scope_digest,
        )


@pytest.mark.asyncio
async def test_disconnect_retries_transient_registration_retirement(
    session: GraphSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.browser_control import browser_control_channel as channel_module

    original_retire = channel_module.retire_catalog
    attempts = 0

    def fail_first_retirement(*args: Any, **kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected retirement outage")
        original_retire(*args, **kwargs)

    monkeypatch.setattr(channel_module, "retire_catalog", fail_first_retirement)
    monkeypatch.setattr(channel_module, "_AUTHORITY_RETRY_SECONDS", 0.0)
    with use_session(session):
        _service, connection, engine, _sent, _tool_value = await _setup(session)
        await connection.disconnect("test_disconnect")
    registrations = [
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.registration"
    ]
    arms = [
        node
        for node in engine._work_item_engine.nodes.values()
        if node.get("kind") == "browser.attended_arm"
    ]
    assert attempts == 2
    assert registrations[0]["status"] == "revoked"
    assert arms[0]["status"] == "revoked"


async def _wait_for_messages(sent: list[Any], count: int) -> None:
    for _ in range(100):
        if len(sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} browser messages, received {len(sent)}")
