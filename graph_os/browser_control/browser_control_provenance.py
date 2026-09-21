"""Durable browser-call audit and replay evidence."""

from __future__ import annotations

from typing import Any, Literal, cast

from agent_utilities.observability.trace_ontology import outcome_id
from agent_utilities.security.persistence_privacy import persistence_reference

from graph_os.browser_control.browser_control_api import BrowserCallReceipt
from graph_os.browser_control.browser_control_common import (
    _DIGEST,
    _RESULT_ERROR_CODE,
    CancellationEffect,
    LangfuseStatus,
)
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ActiveCall,
)
from graph_os.browser_control.trace_bundle import record_trace_bundle

_BOUND_TRACE_NAMES = (
    "actor_reference",
    "attended_arm_reference",
    "attended_arm_expires_at",
    "attended_arm_issued_at",
    "attended_auth_time",
    "attended_acr",
    "attended_issuer",
    "access_token_expires_at",
    "catalog_digest",
    "tool_scope_digest",
    "tenant_reference",
    "login_session_reference",
    "principal_reference",
    "browser_session_reference",
    "origin_reference",
    "document_reference",
    "route_reference",
    "registration_generation",
    "lease_reference",
    "fence_reference",
    "policy_reference",
    "policy_version",
    "confirmation_reference",
    "schema_digest",
    "source_reference",
    "tool_id",
)


def _bound_trace_attributes(active: _ActiveCall) -> dict[str, Any]:
    refs = active.channel.refs
    return {
        "actor_reference": refs.actor_reference,
        "attended_arm_reference": refs.attended_arm_reference,
        "attended_arm_expires_at": refs.attended_arm_expires_at,
        "attended_arm_issued_at": refs.attended_arm_issued_at,
        "attended_auth_time": refs.attended_auth_time,
        "attended_acr": refs.attended_acr,
        "attended_issuer": refs.attended_issuer,
        "access_token_expires_at": refs.access_token_expires_at,
        "catalog_digest": refs.catalog_digest,
        "tool_scope_digest": refs.tool_scope_digest,
        "tenant_reference": refs.tenant_reference,
        "login_session_reference": refs.login_session_reference,
        "principal_reference": refs.principal_reference,
        "browser_session_reference": refs.browser_session_reference,
        "origin_reference": refs.origin_reference,
        "document_reference": refs.document_reference,
        "route_reference": refs.route_reference,
        "registration_generation": refs.registration_generation,
        "lease_reference": persistence_reference(
            "browser_lease", active.request.lease_id, namespace=refs.tenant_reference
        ),
        "fence_reference": persistence_reference(
            "browser_fence", active.item_id, namespace=active.request_digest
        ),
        "policy_reference": active.policy_reference,
        "policy_version": refs.policy_version,
        "confirmation_reference": (
            persistence_reference(
                "browser_confirmation",
                active.confirmation_digest,
                namespace=active.request_digest,
            )
            if active.confirmation_digest
            else "none"
        ),
        "schema_digest": active.request.schema_digest,
        "source_reference": active.descriptor.source_ref,
        "tool_id": active.request.tool_id,
    }


def _validated_replay_values(
    evidence: dict[str, Any], active: _ActiveCall, expected_status: str
) -> tuple[CancellationEffect, LangfuseStatus, str | None, str | None] | None:
    expected = _bound_trace_attributes(active)
    values = _replay_values(evidence)
    if values is None:
        return None
    effect, langfuse_status, result_digest, error_code = values
    matches = evidence.get("status") == expected_status and all(
        evidence.get(name) == value for name, value in expected.items()
    )
    valid_result = _valid_optional_match(result_digest, _DIGEST)
    valid_error = _valid_optional_match(error_code, _RESULT_ERROR_CODE)
    if not matches or not valid_result or not valid_error:
        return None
    return effect, langfuse_status, result_digest, error_code


def _valid_optional_match(value: Any, pattern: Any) -> bool:
    return value is None or (
        isinstance(value, str) and pattern.fullmatch(value) is not None
    )


def _replay_values(
    evidence: dict[str, Any],
) -> tuple[CancellationEffect, LangfuseStatus, Any, Any] | None:
    try:
        effect = CancellationEffect(str(evidence["cancellation_effect"]))
        langfuse_status = LangfuseStatus(str(evidence["langfuse_status"]))
    except (KeyError, ValueError):
        return None
    return (
        effect,
        langfuse_status,
        evidence.get("browser_result_digest"),
        evidence.get("browser_error_code"),
    )


def _read_durable_outcome(engine: Any, query: str, parameters: dict[str, str]) -> Any:
    """Run the engine's blocking read inside the configured sync runner."""

    return engine.query_cypher(query, parameters)


class BrowserProvenanceMixin(BrowserControlMixinState):
    async def _replayed_call(
        self, active: _ActiveCall, row: dict[str, Any] | None
    ) -> BrowserCallReceipt:
        running = self._active_calls.get(active.call_id)
        if running is not None:
            if running.terminal_future.done():
                return running.terminal_future.result()
            status: Literal["unknown", "pending_confirmation", "dispatched"]
            if running.timed_out:
                status = "unknown"
            elif running.confirmation_digest and not running.confirm_future.done():
                status = "pending_confirmation"
            else:
                status = "dispatched"
            return self._receipt(
                running,
                status=status,
                effect=CancellationEffect.UNKNOWN,
            )
        row_status = str((row or {}).get("status") or "")
        if row_status in {"succeeded", "failed", "cancelled"}:
            durable = await self._durable_outcome(active, expected_status=row_status)
            if durable is not None:
                return durable
        return self._receipt(
            active, status="unknown", effect=CancellationEffect.UNKNOWN
        )

    async def _audit(
        self,
        active: _ActiveCall,
        *,
        status: str,
        result: Any = None,
        result_digest: str | None = None,
        error: Any = None,
        cancellation_effect: CancellationEffect = CancellationEffect.NONE,
    ) -> None:
        attributes = {
            **_bound_trace_attributes(active),
            "cancellation_effect": cancellation_effect.value,
            "langfuse_status": active.langfuse_status.value,
        }
        if result_digest is not None:
            attributes["browser_result_digest"] = result_digest
        if isinstance(error, str) and error:
            attributes["browser_error_code"] = error
        await self._sync_runner(
            lambda: record_trace_bundle(
                self._engine,
                session=active.channel.binding.session,
                run_id=active.run_id,
                agent_name="graph-os-browser-control",
                tool_name=active.request.tool_id,
                arguments=active.request.arguments,
                result=result,
                error=error,
                status=status,
                attributes=attributes,
            )
        )

    async def _langfuse_status(
        self, *, run_id: str, tool_name: str, status: str
    ) -> LangfuseStatus:
        if self._langfuse is None or not getattr(self._langfuse, "configured", False):
            return LangfuseStatus.NOT_CONFIGURED
        exporter = self._langfuse
        try:
            recorded = await self._sync_runner(
                lambda: bool(
                    exporter.export_graph_run(
                        run_id=run_id,
                        status=status,
                        metadata={"surface": "browser_control", "tool": tool_name},
                    )
                )
            )
        except Exception:  # noqa: BLE001 - trace records exporter unavailability
            return LangfuseStatus.UNAVAILABLE
        return LangfuseStatus.RECORDED if recorded else LangfuseStatus.UNAVAILABLE

    async def _durable_outcome(
        self, active: _ActiveCall, *, expected_status: str
    ) -> BrowserCallReceipt | None:
        oid = outcome_id(active.run_id)
        names = (
            "status",
            "browser_result_digest",
            "browser_error_code",
            "cancellation_effect",
            "langfuse_status",
            *_BOUND_TRACE_NAMES,
        )
        returns = ", ".join(f"o.{name} AS {name}" for name in names)
        query = f"MATCH (o:OutcomeEvaluation {{id: $id}}) RETURN {returns} LIMIT 2"
        rows = await self._sync_runner(
            lambda: _read_durable_outcome(self._engine, query, {"id": oid})
        )
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            return None
        values = _validated_replay_values(rows[0], active, expected_status)
        if values is None:
            return None
        effect, langfuse_status, result_digest, error_code = values
        active.langfuse_status = langfuse_status
        public_status = cast(
            Literal["succeeded", "failed", "cancelled"], expected_status
        )
        return self._receipt(
            active,
            status=public_status,
            effect=effect,
            result_digest=result_digest,
            error_code=error_code,
        )


__all__ = ["BrowserProvenanceMixin"]
