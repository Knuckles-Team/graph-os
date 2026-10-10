"""GRAPHOS-OPS-R032.2.1: identity composition root returns port-typed objects."""

import pytest

from graph_os.identity.composition import IdentityPorts, build_identity_ports
from graph_os.identity.engine import IdentityUnavailable, Resolution
from graph_os.identity.ports import CredentialState, SessionState

from .test_engine_resolution import resolution_value


class _Runtime:
    def __init__(self, credential_result=None, session_result=None):
        self.credential_result = credential_result
        self.session_result = session_result

    async def resolve_credential(self, credential):
        return self.credential_result

    async def resolve_session(self, credential):
        return self.session_result


def _states():
    resolution = Resolution.parse(resolution_value())
    return (
        CredentialState(resolution, 1000),
        SessionState(resolution, 1000, "session-binding-ref", None),
    )


@pytest.mark.spec("GRAPHOS-OPS-R032.2.1")
async def test_builds_runtime_once_and_returns_ports():
    credential_state, session_state = _states()
    built = []

    def factory(credentials, sessions):
        runtime = _Runtime(credential_state, session_state)
        built.append((credentials, sessions, runtime))
        return runtime

    ports = build_identity_ports("c", "s", runtime_factory=factory)

    assert isinstance(ports, IdentityPorts)
    assert len(built) == 1 and built[0][:2] == ("c", "s")
    assert await ports.credentials.resolve_credential("x") is credential_state
    assert await ports.sessions.resolve_session("x") is session_state
    assert len(built) == 1
    assert not isinstance(ports.credentials, _Runtime)


@pytest.mark.spec("GRAPHOS-OPS-R032.2.1")
@pytest.mark.parametrize(("credentials", "sessions"), [(None, "s"), ("c", None)])
def test_absent_sources_fail_closed_without_building(credentials, sessions):
    def factory(*_):
        raise AssertionError("runtime must not be built")

    with pytest.raises(IdentityUnavailable):
        build_identity_ports(credentials, sessions, runtime_factory=factory)


@pytest.mark.spec("GRAPHOS-OPS-R032.2.1")
async def test_wrong_authority_answers_fail_closed():
    ports = build_identity_ports(
        "c", "s", runtime_factory=lambda *_: _Runtime({"a": 1}, object())
    )
    with pytest.raises(IdentityUnavailable):
        await ports.credentials.resolve_credential("x")
    with pytest.raises(IdentityUnavailable):
        await ports.sessions.resolve_session("x")
