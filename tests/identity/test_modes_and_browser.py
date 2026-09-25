"""``none``-mode guard rails (IDM-07) and the browser session transport (IDM-08)."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.identity.browser import (
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_refusal,
    csrf_token_for,
    session_from_scope,
)
from graph_os.identity.modes import (
    NONE_MODE_ACK,
    NoneModeExposureRefused,
    NoneModeRequestGuard,
    default_mode_for_profile,
    is_loopback_host,
    mode_banner,
    refuse_unsafe_none_mode,
)

SESSION = "s" * 43


def _scope(
    method: str = "GET", *, kind: str = "http", scheme: str = "https", **headers: str
) -> dict[str, Any]:
    raw = [
        (name.replace("_", "-").encode(), value.encode())
        for name, value in headers.items()
    ]
    return {"type": kind, "method": method, "scheme": scheme, "headers": raw}


@pytest.mark.parametrize(
    ("profile", "mode"),
    [
        ("tiny", "none"),
        (None, "none"),
        ("single-node-prod", "local"),
        ("enterprise", "local"),
        ("unknown", "local"),
    ],
)
def test_profile_defaults_follow_the_operator_ruling(
    profile: str | None, mode: str
) -> None:
    assert default_mode_for_profile(profile) == mode


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "[::1]", "localhost", "127.0.0.9"]
)
def test_loopback_names(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "10.0.0.5", "graph-os.arpa", "localhost.evil"]
)
def test_non_loopback_names(host: str) -> None:
    assert not is_loopback_host(host)


def test_none_mode_refuses_a_non_loopback_listener_without_the_exact_ack() -> None:
    with pytest.raises(NoneModeExposureRefused):
        refuse_unsafe_none_mode(
            mode="none", profile="tiny", bind_hosts=["0.0.0.0"], ack=None
        )
    with pytest.raises(NoneModeExposureRefused):
        typo = NONE_MODE_ACK.lower()
        refuse_unsafe_none_mode(
            mode="none", profile="tiny", bind_hosts=["0.0.0.0"], ack=typo
        )
    refuse_unsafe_none_mode(
        mode="none", profile="tiny", bind_hosts=["0.0.0.0"], ack=NONE_MODE_ACK
    )


def test_production_profiles_refuse_none_even_on_loopback() -> None:
    with pytest.raises(NoneModeExposureRefused):
        refuse_unsafe_none_mode(
            mode="none", profile="single-node-prod", bind_hosts=["127.0.0.1"], ack=None
        )


def test_other_modes_bind_anywhere() -> None:
    refuse_unsafe_none_mode(
        mode="local", profile="enterprise", bind_hosts=["0.0.0.0"], ack=None
    )


def test_banner_only_in_none_mode() -> None:
    assert mode_banner("none") and "administrator" in str(mode_banner("none"))
    assert mode_banner("local") is None


@pytest.mark.parametrize(
    ("scope", "reason"),
    [
        (_scope(host="localhost:8080"), None),
        (_scope(host="[::1]:8080"), None),
        (_scope(host="evil.example"), "host_not_loopback"),
        (_scope(host="127.0.0.1.nip.io"), "host_not_loopback"),
        (_scope("POST", host="localhost:8080"), None),
        (_scope("POST", host="localhost:8080", origin="https://localhost:8080"), None),
        (
            _scope("POST", host="localhost:8080", origin="https://evil.example"),
            "origin_not_same_host",
        ),
        (
            _scope("POST", host="localhost:8080", origin="https://localhost:9999"),
            "origin_not_same_host",
        ),
    ],
)
def test_none_mode_host_and_origin_guard(
    scope: dict[str, Any], reason: str | None
) -> None:
    assert NoneModeRequestGuard().refusal(scope) == reason


def test_duplicate_host_headers_are_refused() -> None:
    scope = _scope(host="localhost")
    scope["headers"].append((b"host", b"evil.example"))
    assert NoneModeRequestGuard().refusal(scope) == "host_not_loopback"


def test_acknowledged_exposure_answers_one_extra_name() -> None:
    guard = NoneModeRequestGuard(exposed_name="demo.lan")
    assert guard.refusal(_scope(host="demo.lan:8080")) is None
    assert guard.refusal(_scope(host="other.lan")) == "host_not_loopback"


def test_session_cookie_parsing_refuses_duplicates_and_bad_shapes() -> None:
    assert session_from_scope(_scope(cookie=f"{SESSION_COOKIE}={SESSION}")) == SESSION
    duplicated = _scope(
        cookie=f"{SESSION_COOKIE}={SESSION}; {SESSION_COOKIE}={SESSION}"
    )
    assert session_from_scope(duplicated) is None
    assert session_from_scope(_scope(cookie=f"{SESSION_COOKIE}=short")) is None
    assert session_from_scope(_scope()) is None


def test_csrf_token_is_bound_to_the_session() -> None:
    assert csrf_token_for(SESSION) == csrf_token_for(SESSION)
    assert csrf_token_for(SESSION) != csrf_token_for("t" * 43)


@pytest.mark.parametrize(
    ("scope", "reason"),
    [
        (_scope(host="app.test"), None),
        (_scope("POST", host="app.test"), "origin_not_same_origin"),
        (
            _scope("POST", host="app.test", origin="https://evil.test"),
            "origin_not_same_origin",
        ),
        (
            _scope("POST", host="app.test", origin="http://app.test"),
            "origin_not_same_origin",
        ),
        (
            _scope("POST", host="app.test", origin="https://app.test"),
            "csrf_token_mismatch",
        ),
        (
            _scope(
                "POST", host="app.test", origin="https://app.test", x_csrf_token="wrong"
            ),
            "csrf_token_mismatch",
        ),
        (
            _scope(
                "POST",
                host="app.test",
                origin="https://app.test",
                x_csrf_token=csrf_token_for(SESSION),
            ),
            None,
        ),
        (
            _scope(kind="websocket", scheme="wss", host="app.test"),
            "origin_not_same_origin",
        ),
        (
            _scope(
                kind="websocket",
                scheme="wss",
                host="app.test",
                origin="https://app.test",
            ),
            None,
        ),
    ],
)
def test_cookie_state_changes_need_origin_and_token(
    scope: dict[str, Any], reason: str | None
) -> None:
    assert csrf_refusal(scope, SESSION) == reason


def test_csrf_header_name_is_lowercase_bytes() -> None:
    assert CSRF_HEADER == b"x-csrf-token"
