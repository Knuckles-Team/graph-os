"""Source fixtures test lifecycle and the actual WebUI DTO/export interface.

The injected backend/token verifier here are synthetic and do not qualify an
installed EG adapter, broker, live cookie or deployment. No grants are written.
"""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from graph_os.identity.broker import GraphOSBrowserAuthority
from graph_os.identity.engine import IdentityUnavailable, Resolution
from graph_os.identity.engine_ports import UnavailableIdentityAuthority
from graph_os.identity.issuer import claims_for
from graph_os.identity.ports import SessionState

from .test_engine_resolution import resolution_value
from .test_issuer import SETTINGS
from .test_modes_and_browser import ORIGIN, request_scope

# Synthetic fixture tokens only; OwnersFixture.verify_token accepts no others
# and no live issuer, provider or production credential ever uses them.
_FIXTURE_TOKEN = "fixture-local-token"  # sanitizer:ignore - synthetic test token, not a real credential
_OTHER_FIXTURE_TOKEN = "other-fixture-local-token"  # sanitizer:ignore - synthetic test token, not a real credential


class CallerFixture:
    def __init__(self, resolution):
        self.actor = SimpleNamespace(
            authenticated=True,
            actor_id=resolution.principal_id,
            actor_type=resolution.kind,
            tenant_id=resolution.request_context["tenant"],
            ensure_credential_current=lambda: None,
        )
        self.tenant = self.actor.tenant_id
        self.scopes = resolution.scopes
        self.policy_version = resolution.request_context["policy_version"]
        self.context = dict(resolution.request_context)

    def ensure_authority_current(self):
        pass

    def engine_verified_context(self):
        return self.context


class OwnersFixture:
    def __init__(self):
        self.now = int(time.time())
        self.resolution = Resolution.parse(resolution_value())
        self.state = SessionState(
            self.resolution,
            (self.now + 120) * 1000,
            "opaque-fixture-binding",
            self.now * 1000,
        )
        self.error = None
        self.on_resolve = None

    async def resolve_session(self, credential):
        if self.error:
            raise self.error
        if self.on_resolve:
            await self.on_resolve()
        return self.state

    async def verify_token(self, token):
        if token not in {_FIXTURE_TOKEN, _OTHER_FIXTURE_TOKEN}:
            raise PermissionError("invalid fixture token")
        return SimpleNamespace(
            claims=claims_for(
                self.resolution,
                SETTINGS,
                source_expires_at_ms=self.state.expires_at_ms,
                now_ms=self.now * 1000,
            ),
            ensure_current=lambda: None,
        )

    def producer(self):
        return GraphOSBrowserAuthority(
            self, self.verify_token, trusted_origin=ORIGIN, clock=lambda: self.now
        )


def forwarded_scope(token=_FIXTURE_TOKEN):
    scope = request_scope()
    scope["headers"].append((b"authorization", f"Bearer {token}".encode()))
    return scope


def bound_fixture_parts():
    """A fresh owner/scope/session triple over a new ``OwnersFixture`` backend
    — the common setup every ``_bind_request`` scenario below starts from."""
    backend = OwnersFixture()
    owner, scope, session = (
        backend.producer(),
        forwarded_scope(),
        CallerFixture(backend.resolution),
    )
    return backend, owner, scope, session


def test_actual_webui_export_and_same_caller_instance_then_cleanup():
    from agent_webui.oidc_session import BrowserSessionEvidence, verify_browser_session

    async def scenario():
        backend, owner, scope, session = bound_fixture_parts()
        async with owner._bind_request(scope, session, forwarded_token=_FIXTURE_TOKEN):
            evidence = await owner.verify_request(scope)
            assert isinstance(evidence, BrowserSessionEvidence)
            assert evidence.request_scope is scope
            assert (
                await owner.session_for_request(SimpleNamespace(scope=scope)) is session
            )
            assert (
                await verify_browser_session(
                    scope, session, authority=owner, console_origin=ORIGIN
                )
                == backend.now * 1000
            )
            await owner.before_invocation(scope, session)
            assert "state" not in scope
        assert owner._requests == {}
        with pytest.raises(PermissionError):
            await owner.verify_request(scope)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["revoke", "rotate", "expire", "refresh", "substitute", "narrow"]
)
def test_transition_or_session_substitution_refuses_and_cleans(change):
    async def scenario():
        backend, owner, scope, session = bound_fixture_parts()
        async with owner._bind_request(scope, session, forwarded_token=_FIXTURE_TOKEN):
            if change == "revoke":
                backend.error = PermissionError("revoked")
            elif change == "rotate":
                backend.state = replace(backend.state, session_ref="new-binding")
            elif change == "expire":
                backend.state = replace(backend.state, expires_at_ms=0)
            elif change == "refresh":
                owner.invalidate(scope)
            elif change == "substitute":
                session = CallerFixture(backend.resolution)
            else:
                backend.state = replace(
                    backend.state, resolution=backend.resolution.narrow(())
                )
            with pytest.raises(PermissionError):
                await owner.before_invocation(scope, session)
        assert owner._requests == {}

    asyncio.run(scenario())


def test_scope_mutated_across_await_cannot_export_evidence():
    async def scenario():
        backend, owner, scope, session = bound_fixture_parts()
        async with owner._bind_request(scope, session, forwarded_token=_FIXTURE_TOKEN):

            async def mutation():
                await asyncio.sleep(0)
                scope["root_path"] = "/api"

            backend.on_resolve = mutation
            with pytest.raises(PermissionError):
                await owner.verify_request(scope)
            assert owner._requests == {}

    asyncio.run(scenario())


def test_cancellation_during_live_check_propagates_and_releases_binding():
    async def scenario():
        backend, owner, scope, session = bound_fixture_parts()
        paused = asyncio.Event()

        async def pause():
            paused.set()
            await asyncio.Event().wait()

        async def request():
            async with owner._bind_request(
                scope, session, forwarded_token=_FIXTURE_TOKEN
            ):
                backend.on_resolve = pause
                await owner.verify_request(scope)

        task = asyncio.create_task(request())
        await asyncio.wait_for(paused.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner._requests == {}

    asyncio.run(scenario())


def test_concurrent_same_subject_requests_never_share_session_instances():
    async def scenario():
        backend = OwnersFixture()
        owner = backend.producer()
        ready = [asyncio.Event(), asyncio.Event()]
        sessions = [
            CallerFixture(backend.resolution),
            CallerFixture(backend.resolution),
        ]

        async def request(index):
            scope = forwarded_scope()
            async with owner._bind_request(
                scope, sessions[index], forwarded_token=_FIXTURE_TOKEN
            ):
                ready[index].set()
                await ready[1 - index].wait()
                await owner.before_invocation(scope, sessions[index])
                with pytest.raises(PermissionError):
                    await owner.before_invocation(scope, sessions[1 - index])

        await asyncio.gather(request(0), request(1))
        assert owner._requests == {}

    asyncio.run(scenario())


def test_marker_header_or_retained_evidence_cannot_install_private_binding():
    async def scenario():
        backend = OwnersFixture()
        owner, scope = backend.producer(), forwarded_scope()
        scope["state"] = {"graphos_session_admitted": True}
        scope["headers"].append((b"x-graphos-session-admitted", b"true"))
        with pytest.raises(PermissionError):
            await owner.verify_request(scope)
        assert owner._requests == {}

    asyncio.run(scenario())


def test_missing_real_eg_session_contract_has_typed_unavailable_refusal():
    async def scenario():
        backend = OwnersFixture()
        owner = GraphOSBrowserAuthority(
            UnavailableIdentityAuthority(),
            backend.verify_token,
            trusted_origin=ORIGIN,
            clock=lambda: backend.now,
        )
        with pytest.raises(
            IdentityUnavailable, match="session evidence binding unavailable"
        ):
            async with owner._bind_request(
                forwarded_scope(),
                CallerFixture(backend.resolution),
                forwarded_token=_FIXTURE_TOKEN,
            ):
                pytest.fail("missing authority reached request body")
        assert owner._requests == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["initial_resolve", "recheck_before_await"])
def test_mutated_scope_never_leaves_a_private_binding(phase):
    async def scenario():
        backend, owner, scope, session = bound_fixture_parts()
        if phase == "initial_resolve":

            async def mutate():
                scope["method"] = "DELETE"

            backend.on_resolve = mutate
            with pytest.raises(PermissionError):
                async with owner._bind_request(
                    scope, session, forwarded_token=_FIXTURE_TOKEN
                ):
                    pytest.fail("mutated initial request was admitted")
        else:
            async with owner._bind_request(
                scope, session, forwarded_token=_FIXTURE_TOKEN
            ):
                scope["query_string"] = b"different=target"
                with pytest.raises(PermissionError):
                    await owner.verify_request(scope)
                assert owner._requests == {}
        assert owner._requests == {}

    asyncio.run(scenario())


def test_transport_failure_propagates_and_cleans_live_request():
    async def scenario():
        backend, owner, scope, session = bound_fixture_parts()
        async with owner._bind_request(scope, session, forwarded_token=_FIXTURE_TOKEN):
            backend.error = RuntimeError("fixture transport unavailable")
            with pytest.raises(RuntimeError, match="transport unavailable"):
                await owner.verify_request(scope)
            assert owner._requests == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("reference", [None, "", " ", "deadbeef1234", "DEADBEEF1234"])
def test_session_display_handle_or_missing_reference_is_not_evidence(reference):
    backend = OwnersFixture()
    with pytest.raises(IdentityUnavailable, match="session binding reference required"):
        replace(backend.state, session_ref=reference)


@pytest.mark.parametrize("phase", ["initial_binding", "before_invocation"])
@pytest.mark.parametrize("change", ["revoke", "rotate", "expire", "caller"])
def test_authority_change_during_token_await_refuses_and_cleans(phase, change):
    async def scenario():
        backend = OwnersFixture()
        scope, session = forwarded_scope(), CallerFixture(backend.resolution)
        triggered = phase == "initial_binding"
        entered = False
        invoked = False

        async def verifier(token):
            value = await backend.verify_token(token)
            if triggered:
                await asyncio.sleep(0)
                if change == "revoke":
                    backend.error = PermissionError("revoked during token verification")
                elif change == "rotate":
                    backend.state = replace(
                        backend.state, session_ref="rotated-during-verification"
                    )
                elif change == "expire":
                    backend.state = replace(backend.state, expires_at_ms=0)
                else:
                    session.scopes = ()
            return value

        owner = GraphOSBrowserAuthority(
            backend, verifier, trusted_origin=ORIGIN, clock=lambda: backend.now
        )
        with pytest.raises(PermissionError):
            async with owner._bind_request(
                scope, session, forwarded_token=_FIXTURE_TOKEN
            ):
                entered = True
                triggered = True
                try:
                    await owner.before_invocation(scope, session)
                except PermissionError:
                    # Refusal must remove the binding before context-manager exit.
                    assert owner._requests == {}
                    raise
                invoked = True
        assert entered is (phase == "before_invocation")
        assert invoked is False
        assert owner._requests == {}
        with pytest.raises(PermissionError):
            await owner.verify_request(scope)

    asyncio.run(scenario())


def test_final_session_await_rechecks_caller_facts_before_return():
    async def scenario():
        backend = OwnersFixture()
        scope, session = forwarded_scope(), CallerFixture(backend.resolution)
        owner = backend.producer()
        async with owner._bind_request(scope, session, forwarded_token=_FIXTURE_TOKEN):
            calls = 0

            async def mutate_during_final_resolve():
                nonlocal calls
                calls += 1
                if calls == 2:
                    await asyncio.sleep(0)
                    session.policy_version = "changed-policy"

            backend.on_resolve = mutate_during_final_resolve
            with pytest.raises(PermissionError):
                await owner.before_invocation(scope, session)
            assert calls == 2
            assert owner._requests == {}

    asyncio.run(scenario())
