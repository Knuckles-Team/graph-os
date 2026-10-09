"""Browser transport checks; no durable session or MFA authority is mocked in."""

import asyncio
import base64

import pytest

from graph_os.identity.browser import (
    SESSION_COOKIE,
    _RequestBinding,
    csrf_token_for,
    require_mutation_proof,
    session_cookie_header,
    session_from_scope,
)
from graph_os.identity.engine import IdentityUnavailable

TOKEN = base64.urlsafe_b64encode(b"s" * 32).decode().rstrip("=")
ORIGIN = "https://console.invalid"
# Synthetic fixture token only; nothing resolves or verifies it as a real JWT.
_FIXTURE_FORWARDED_TOKEN = "private-fixture-jwt"  # sanitizer:ignore - synthetic test token, not a real credential


def request_scope():
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/self",
        "raw_path": b"/api/self",
        "query_string": b"",
        "headers": [
            (b"host", b"console.invalid"),
            (b"origin", ORIGIN.encode()),
            (b"cookie", f"{SESSION_COOKIE}={TOKEN}".encode()),
            (b"x-csrf-token", csrf_token_for(TOKEN).encode()),
        ],
    }


def test_valid_mutation_and_authoritative_cookie_lifetime():
    scope = request_scope()
    require_mutation_proof(scope, trusted_origin=ORIGIN)
    assert session_from_scope(scope) == TOKEN
    cookie = session_cookie_header(TOKEN, expires_at_ms=200_999, now_ms=100_000)
    assert b"Max-Age=100" in cookie
    for attribute in (b"Secure", b"HttpOnly", b"SameSite=Lax", b"Path=/"):
        assert attribute in cookie
    assert b"Domain=" not in cookie
    with pytest.raises(PermissionError):
        session_cookie_header(TOKEN, expires_at_ms=100_999, now_ms=100_000)


@pytest.mark.parametrize(
    "cookie",
    [
        f"{SESSION_COOKIE}=bad",
        SESSION_COOKIE,
        "au_session=anything",
        f"{SESSION_COOKIE}={TOKEN}; {SESSION_COOKIE}={TOKEN}",
        f"{SESSION_COOKIE}={TOKEN}; au_session=anything",
        f"{SESSION_COOKIE} ={TOKEN}",
        f"{SESSION_COOKIE}={TOKEN}; {SESSION_COOKIE}\t={TOKEN}",
        f"{SESSION_COOKIE}={TOKEN}; au_session =anything",
        f"{SESSION_COOKIE}={TOKEN}; au_session.0\t=anything",
    ],
)
def test_presented_invalid_cookie_is_never_absent(cookie):
    scope = request_scope()
    scope["headers"] = [(b"cookie", cookie.encode())]
    with pytest.raises(PermissionError):
        session_from_scope(scope)
    assert session_from_scope({"headers": []}) is None


@pytest.mark.parametrize("name", [b"origin", b"x-csrf-token", b"cookie"])
@pytest.mark.parametrize("change", ["missing", "duplicate", "incorrect"])
def test_mutation_transport_refuses_bad_proofs(name, change):
    scope = request_scope()
    existing = next(pair for pair in scope["headers"] if pair[0] == name)
    if change == "duplicate":
        scope["headers"].append(existing)
    else:
        scope["headers"] = [pair for pair in scope["headers"] if pair[0] != name]
        if change == "incorrect":
            scope["headers"].append((name, b"incorrect"))
    with pytest.raises(PermissionError):
        require_mutation_proof(scope, trusted_origin=ORIGIN)


def test_host_and_state_cannot_redefine_trusted_origin():
    scope = request_scope()
    scope["headers"] = [
        (name, b"https://evil.invalid" if name == b"origin" else value)
        for name, value in scope["headers"]
    ]
    scope["state"] = {"graphos_session_admitted": True}
    with pytest.raises(PermissionError):
        require_mutation_proof(scope, trusted_origin=ORIGIN)


@pytest.mark.parametrize(
    "origin", [None, 1, "", "https://console.invalid/path", "https://é.invalid"]
)
def test_bad_origin_configuration_is_unavailable(origin):
    with pytest.raises(IdentityUnavailable):
        require_mutation_proof(request_scope(), trusted_origin=origin)


@pytest.mark.parametrize(
    "field,value",
    [
        ("method", "DELETE"),
        ("path", "/api/other"),
        ("raw_path", b"/api/other"),
        ("query_string", b"target=other"),
        ("root_path", "/api"),
        ("scheme", "https"),
        ("server", ("different.invalid", 443)),
    ],
)
def test_same_object_mutation_across_await_invalidates_snapshot(field, value):
    async def scenario():
        scope, session = request_scope(), object()
        binding = _RequestBinding.capture(
            scope,
            session,
            session_ref="opaque-ref",
            forwarded_token=_FIXTURE_FORWARDED_TOKEN,
        )

        async def authority_recheck():
            await asyncio.sleep(0)
            scope[field] = value

        await authority_recheck()
        with pytest.raises(PermissionError):
            binding.ensure_unchanged(
                scope,
                session,
                session_ref="opaque-ref",
                forwarded_token=_FIXTURE_FORWARDED_TOKEN,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name", [b"origin", b"cookie", b"authorization", b"x-csrf-token"]
)
def test_credential_and_proof_header_substitution_refuses(name):
    scope, session = request_scope(), object()
    binding = _RequestBinding.capture(
        scope,
        session,
        session_ref="opaque-ref",
        forwarded_token=_FIXTURE_FORWARDED_TOKEN,
    )
    scope["headers"] = [(key, val) for key, val in scope["headers"] if key != name]
    scope["headers"].append((name, b"different"))
    with pytest.raises(PermissionError):
        binding.ensure_unchanged(
            scope,
            session,
            session_ref="opaque-ref",
            forwarded_token=_FIXTURE_FORWARDED_TOKEN,
        )


@pytest.mark.parametrize("change", ["scope", "session", "reference", "token"])
def test_instance_and_pairing_identity_not_subject_equivalence(change):
    scope, session = (
        request_scope(),
        {"subject": "same-subject", "tenant": "same-tenant"},
    )
    binding = _RequestBinding.capture(
        scope,
        session,
        session_ref="opaque-ref",
        forwarded_token=_FIXTURE_FORWARDED_TOKEN,
    )
    binding.ensure_unchanged(
        scope,
        session,
        session_ref="opaque-ref",
        forwarded_token=_FIXTURE_FORWARDED_TOKEN,
    )
    with pytest.raises(PermissionError):
        binding.ensure_unchanged(
            dict(scope) if change == "scope" else scope,
            dict(session) if change == "session" else session,
            session_ref="different" if change == "reference" else "opaque-ref",
            forwarded_token="different"
            if change == "token"
            else _FIXTURE_FORWARDED_TOKEN,
        )
    assert TOKEN not in repr(binding) and _FIXTURE_FORWARDED_TOKEN not in repr(binding)
