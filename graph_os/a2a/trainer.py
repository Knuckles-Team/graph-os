"""Outbound A2A delivery of policy-training jobs to a trainer agent (EH-347).

The trainer is an A2A agent registered in EG's server registry with
``resources.a2a_role == "policy-trainer"`` (and its image digest). graph-os
sends the digest-bound job as one A2A ``message/send`` in the same wire shape
its own facade serves (:class:`A2AMessage`), follows the task with
``tasks/get`` and cancels it with ``tasks/cancel``. The message id is the
job id, so a redelivery is idempotent at the trainer.

The trainer's final task artifact is a JSON report
(``status``/``trainer_image_digest``/``artifact_digest``/``artifact_ref``/
``resources``). Anything else — a failed/rejected task or a malformed report
— becomes a ``failed`` outcome: graph-os never fabricates a success. With no
registered trainer the transport refuses with
``TRAINING_TRAINER_NOT_REGISTERED`` before any job leaves the process.

Every call carries graph-os's outbound service identity — the same one
``graph_os.fleet.child_credentials`` presents to authenticated fleet
children (``MCP_CLIENT_AUTH``: OIDC client credentials, basic, or a rotating
bearer file). A trainer whose registry entry declares
``auth_required: true`` is refused with ``TRAINING_TRAINER_AUTH_UNAVAILABLE``
when no credential resolves; the transport never degrades to an anonymous
call to such a trainer.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from graph_os.control_plane.policy_evolution import (
    PolicyEvolutionControlError,
    TrainingLease,
)

from .models import A2AMessage, A2ATextPart

__all__ = [
    "A2ATrainerTransport",
    "EgTrainerRegistry",
    "TrainerEndpoint",
    "TrainerRegistry",
]

TRAINER_ROLE = "policy-trainer"
_FINAL = {"completed", "failed", "rejected", "canceled"}
Poster = Callable[[str, dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]
OutcomeFactory = Callable[..., Any]
Credential = Callable[[], Any]


@dataclass(frozen=True)
class TrainerEndpoint:
    name: str
    url: str
    trainer_image_digest: str
    auth_required: bool = False


class TrainerRegistry(Protocol):
    async def resolve(self) -> TrainerEndpoint: ...


def _resources(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


class EgTrainerRegistry:
    """Resolve the live, enabled trainer from EG's server registry."""

    def __init__(self, client: Any, clock_ms: Callable[[], int]) -> None:
        self._registry = client.server_registry
        self._clock_ms = clock_ms

    async def resolve(self) -> TrainerEndpoint:
        now = self._clock_ms()
        for entry in await self._registry.list_all():
            resources = _resources(entry.resources)
            live = (
                str(getattr(entry.desired, "value", entry.desired)) == "enabled"
                and int(entry.lease_expires_at_ms) > now
            )
            digest = resources.get("trainer_image_digest")
            if live and resources.get("a2a_role") == TRAINER_ROLE and digest:
                return TrainerEndpoint(
                    entry.name,
                    entry.url,
                    str(digest),
                    resources.get("auth_required") is True,
                )
        raise PolicyEvolutionControlError(
            "TRAINING_TRAINER_NOT_REGISTERED",
            "no live A2A agent declares the policy-trainer role",
        )


def _service_identity() -> Any:
    from graph_os.fleet.child_credentials import child_auth

    return child_auth(None)


async def authorization_header(auth: Any, url: str) -> str | None:
    """The ``Authorization`` value an ``httpx.Auth`` puts on one request."""
    import httpx

    flow = auth.async_auth_flow(httpx.Request("POST", url))
    try:
        request = await flow.__anext__()
    finally:
        await flow.aclose()
    value = request.headers.get("Authorization")
    return str(value) if value else None


async def _safe_post(
    url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    from agent_connector_sdk.http.source_post import safe_post_json_async
    from agent_utilities.core.config import config
    from agent_utilities.core.transport_security import resolve_configured_tls_profile

    profile = resolve_configured_tls_profile("a2a")
    try:
        value = await safe_post_json_async(
            url,
            payload,
            headers=headers or None,
            timeout=60.0,
            max_bytes=int(config.source_http_max_response_bytes),
            allowed_private_hosts=tuple(config.source_http_allowed_private_hosts),
            tls=profile,
        )
    finally:
        profile.cleanup()
    if not isinstance(value, dict):
        raise PolicyEvolutionControlError("TRAINING_TRAINER_PROTOCOL", "non-object")
    return value


def _au_outcome(**fields: Any) -> Any:
    try:
        from agent_utilities.harness.policy_evolution import TrainerOutcome
    except ImportError as exc:
        raise PolicyEvolutionControlError(
            "POLICY_TRAINING_PATH_UNAVAILABLE",
            "agent-utilities does not publish TrainerOutcome",
        ) from exc
    return TrainerOutcome(**fields)


def _job_payload(spec: Any, lease: TrainingLease) -> dict[str, Any]:
    policy = getattr(spec, "policy", None)
    if dataclasses.is_dataclass(policy) and not isinstance(policy, type):
        policy_fields: dict[str, Any] = dataclasses.asdict(policy)
    else:
        policy_fields = dict(vars(policy)) if policy is not None else {}
    return {
        "job_id": str(spec.job_id),
        "method": str(spec.method),
        "policy": policy_fields,
        "lease": {
            "lease_id": lease.lease_id,
            "host_id": lease.host_id,
            "gpu_memory_bytes": lease.gpu_memory_bytes,
            "expires_at_ms": lease.expires_at_ms,
        },
    }


def _rpc(method: str, params: dict[str, Any], request_id: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}


def _report_text(task: dict[str, Any]) -> str | None:
    for artifact in task.get("artifacts") or ():
        for part in artifact.get("parts") or ():
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return str(part["text"])
    return None


class A2ATrainerTransport:
    """The :class:`TrainerTransport` over A2A JSON-RPC."""

    def __init__(
        self,
        registry: TrainerRegistry,
        *,
        poster: Poster = _safe_post,
        outcome: OutcomeFactory = _au_outcome,
        credential: Credential = _service_identity,
        poll_interval_s: float = 5.0,
    ) -> None:
        self._registry = registry
        self._post = poster
        self._credential = credential
        self._outcome = outcome
        self._interval = poll_interval_s
        self._tasks: dict[str, tuple[TrainerEndpoint, str]] = {}

    async def _headers(self, endpoint: TrainerEndpoint) -> dict[str, str]:
        from graph_os.fleet.child_credentials import ChildAuthConfigurationError

        try:
            auth = self._credential()
            header = (
                None if auth is None else await authorization_header(auth, endpoint.url)
            )
        except ChildAuthConfigurationError:
            header = None
        if header is None and endpoint.auth_required:
            raise PolicyEvolutionControlError(
                "TRAINING_TRAINER_AUTH_UNAVAILABLE",
                "the trainer requires a service credential and none resolved",
            )
        return {} if header is None else {"Authorization": header}

    async def _call(
        self, endpoint: TrainerEndpoint, body: dict[str, Any]
    ) -> dict[str, Any]:
        answer = await self._post(endpoint.url, body, await self._headers(endpoint))
        result = answer.get("result")
        if "error" in answer or not isinstance(result, dict):
            raise PolicyEvolutionControlError(
                "TRAINING_TRAINER_PROTOCOL", str(body["method"])
            )
        return result

    async def run(self, spec: Any, lease: TrainingLease) -> Any:
        endpoint = await self._registry.resolve()
        payload = _job_payload(spec, lease)
        message = A2AMessage(
            role="user",
            message_id=payload["job_id"],
            parts=[A2ATextPart(text=json.dumps(payload, sort_keys=True, default=str))],
            metadata={"graphOsPolicyJob": payload["job_id"]},
        )
        task = await self._call(
            endpoint,
            _rpc(
                "message/send",
                {"message": message.model_dump(by_alias=True, mode="json")},
                payload["job_id"],
            ),
        )
        task_id = str(task.get("id") or "")
        if not task_id:
            raise PolicyEvolutionControlError("TRAINING_TRAINER_PROTOCOL", "no task id")
        self._tasks[payload["job_id"]] = (endpoint, task_id)
        return await self._follow(endpoint, task_id, task)

    async def _follow(
        self, endpoint: TrainerEndpoint, task_id: str, task: dict[str, Any]
    ) -> Any:
        while str((task.get("status") or {}).get("state")) not in _FINAL:
            await asyncio.sleep(self._interval)
            task = await self._call(
                endpoint, _rpc("tasks/get", {"id": task_id}, task_id)
            )
        return self._final_outcome(endpoint, task)

    def _final_outcome(self, endpoint: TrainerEndpoint, task: dict[str, Any]) -> Any:
        state = str(task["status"]["state"])
        if state == "canceled":
            return self._terminal(endpoint, "cancelled")
        if state != "completed":
            return self._terminal(endpoint, "failed")
        try:
            report = json.loads(_report_text(task) or "")
        except ValueError:
            return self._terminal(endpoint, "failed")
        if not isinstance(report, dict) or report.get("status") not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            return self._terminal(endpoint, "failed")
        return self._outcome(
            status=report["status"],
            trainer_image_digest=str(
                report.get("trainer_image_digest") or endpoint.trainer_image_digest
            ),
            resources=dict(report.get("resources") or {}),
            artifact_digest=report.get("artifact_digest"),
            artifact_ref=report.get("artifact_ref"),
        )

    def _terminal(self, endpoint: TrainerEndpoint, status: str) -> Any:
        return self._outcome(
            status=status, trainer_image_digest=endpoint.trainer_image_digest
        )

    async def cancel(self, spec: Any, lease: TrainingLease) -> Any:
        job_id = str(spec.job_id)
        known = self._tasks.get(job_id)
        if known is None:
            endpoint = await self._registry.resolve()
            return self._terminal(endpoint, "cancelled")
        endpoint, task_id = known
        await self._call(endpoint, _rpc("tasks/cancel", {"id": task_id}, task_id))
        return self._terminal(endpoint, "cancelled")
