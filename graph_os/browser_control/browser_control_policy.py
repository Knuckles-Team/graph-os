"""ActionPolicy decisions for browser capabilities and exact calls."""

from __future__ import annotations

from typing import Any

from agent_utilities.orchestration.action_policy import ActionRequest
from agent_utilities.security.persistence_privacy import persistence_reference

from graph_os.browser_control.browser_control_api import (
    BrowserCallReceipt,
    BrowserCallRequest,
)
from graph_os.browser_control.browser_control_common import (
    MutationClass,
    content_sha256,
)
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor
from graph_os.browser_control.browser_control_durability import call_identity
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ActiveCall,
    _ChannelState,
    new_active_call,
)
from graph_os.browser_control.browser_control_validation import validate_json_schema


class BrowserPolicyMixin(BrowserControlMixinState):
    async def _prepare_active_call(
        self,
        channel: _ChannelState,
        lease: dict[str, Any],
        request: BrowserCallRequest,
    ) -> tuple[_ActiveCall | None, BrowserCallReceipt | None]:
        descriptor = self._descriptor_for_call(channel, lease, request)
        await self._sync_runner(
            lambda: validate_json_schema(descriptor.input_schema, request.arguments)
        )
        identity = call_identity(
            channel.refs,
            lease_id=request.lease_id,
            request_id=request.request_id,
            tool_id=request.tool_id,
            schema_digest=request.schema_digest,
            arguments=request.arguments,
        )
        cached = self._completed.get(identity.call_id)
        if cached is not None:
            return None, cached
        authorized, policy_reference = await self._authorize_exact_call(
            channel, descriptor, identity.request_digest
        )
        confirmation_input = {
            "policy_reference": policy_reference,
            "request_digest": identity.request_digest,
        }
        confirmation_digest = (
            "sha256:" + content_sha256(confirmation_input)
            if descriptor.mutation_class is MutationClass.LOCAL_UI_MUTATION
            else None
        )
        langfuse_status = await self._langfuse_status(
            run_id=identity.run_id,
            tool_name=request.tool_id,
            status="pending" if authorized else "denied",
        )
        return (
            new_active_call(
                channel,
                descriptor,
                request,
                identity=identity,
                authorized=authorized,
                policy_reference=policy_reference,
                confirmation_digest=confirmation_digest,
                langfuse_status=langfuse_status,
            ),
            None,
        )

    async def _authorize_capabilities(
        self, channel: _ChannelState, tools: tuple[BrowserToolDescriptor, ...]
    ) -> tuple[str, ...]:
        requests = [self._policy_request(channel, tool, "catalog") for tool in tools]
        decisions = await self._sync_runner(
            lambda: tuple(self._policy.decide(request) for request in requests)
        )
        return tuple(
            self._validated_policy_reference(decision, request)
            for decision, request in zip(decisions, requests, strict=True)
        )

    async def _authorize_exact_call(
        self,
        channel: _ChannelState,
        descriptor: BrowserToolDescriptor,
        request_digest: str,
    ) -> tuple[bool, str]:
        request = self._policy_request(channel, descriptor, request_digest)
        decision = await self._sync_runner(lambda: self._policy.decide(request))
        receipt = getattr(decision, "receipt", None)
        valid_receipt = (
            receipt is not None and receipt.request_digest == request.digest()
        )
        reference = persistence_reference(
            "browser_policy",
            getattr(receipt, "receipt_id", "unavailable")
            if valid_receipt
            else "unavailable",
            namespace=request.digest(),
        )
        return bool(getattr(decision, "allowed", False) and valid_receipt), reference

    @staticmethod
    def _policy_request(
        channel: _ChannelState,
        descriptor: BrowserToolDescriptor,
        request_digest: str,
    ) -> ActionRequest:
        kind = (
            "observe"
            if descriptor.mutation_class is MutationClass.READ
            else "workspace.computer_use"
        )
        return ActionRequest(
            kind=kind,
            target=f"browser:{descriptor.tool_id}",
            params={
                "request_digest": request_digest,
                "schema_digest": descriptor.schema_digest,
                "document_reference": channel.refs.document_reference,
                "attended_arm_reference": channel.refs.attended_arm_reference,
                "attended_arm_expires_at": channel.refs.attended_arm_expires_at,
                "access_token_expires_at": channel.refs.access_token_expires_at,
                "catalog_digest": channel.refs.catalog_digest,
                "tool_scope_digest": channel.refs.tool_scope_digest,
                "registration_generation": channel.refs.registration_generation,
            },
            source="browser_control",
            reason="attended browser-local WebMCP capability",
            actor_id=channel.refs.actor_reference,
        )

    @staticmethod
    def _validated_policy_reference(decision: Any, request: ActionRequest) -> str:
        receipt = getattr(decision, "receipt", None)
        if (
            not getattr(decision, "allowed", False)
            or receipt is None
            or receipt.request_digest != request.digest()
        ):
            raise PermissionError("ActionPolicy did not authorize browser control")
        return persistence_reference(
            "browser_policy", receipt.receipt_id, namespace=request.digest()
        )


__all__ = ["BrowserPolicyMixin"]
