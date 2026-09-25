"""Focused production secret-reference bootstrap guards."""

from __future__ import annotations

import base64

import pytest

from graph_os.api import host_secrets


def test_configured_host_secrets_keep_bearer_reference_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "GRAPHOS_PLAN_SEAL_KEY_REF": "env://GRAPHOS_PLAN_KEY",
        "GRAPHOS_CONTEXT_BEARER_REF": "openbao://apps/graphos#CONTEXT_TOKEN",
    }
    resolved: list[str] = []

    def resolve(reference: str) -> str:
        resolved.append(reference)
        if reference == values["GRAPHOS_PLAN_SEAL_KEY_REF"]:
            return base64.b64encode(b"k" * 32).decode()
        return "bearer-secret-value"

    monkeypatch.setattr(host_secrets, "setting", values.get)
    monkeypatch.setattr(host_secrets, "resolve_secret_reference", resolve)
    ports = host_secrets.host_secret_ports_from_config()
    assert ports.plan_seal_key == b"k" * 32
    assert ports.context_bearer_ref == values["GRAPHOS_CONTEXT_BEARER_REF"]
    assert resolved == [values["GRAPHOS_PLAN_SEAL_KEY_REF"]]


@pytest.mark.asyncio
async def test_context_bearer_resolves_only_for_live_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "GRAPHOS_PLAN_SEAL_KEY_REF": "env://PLAN_KEY",
        "GRAPHOS_CONTEXT_BEARER_REF": "env://CONTEXT_TOKEN",
    }
    resolved: list[str] = []

    def resolve(reference: str) -> str:
        resolved.append(reference)
        return (
            base64.b64encode(b"k" * 32).decode()
            if reference == values["GRAPHOS_PLAN_SEAL_KEY_REF"]
            else "bearer-secret-value"
        )

    monkeypatch.setattr(host_secrets, "setting", values.get)
    monkeypatch.setattr(host_secrets, "resolve_secret_reference", resolve)
    ports = host_secrets.host_secret_ports_from_config()
    assert resolved == [values["GRAPHOS_PLAN_SEAL_KEY_REF"]]
    assert await ports.resolve_bearer(ports.context_bearer_ref) == "bearer-secret-value"
    with pytest.raises(ValueError, match="unconfigured"):
        await ports.resolve_bearer("env://OTHER_TOKEN")
    assert resolved == [
        values["GRAPHOS_PLAN_SEAL_KEY_REF"],
        values["GRAPHOS_CONTEXT_BEARER_REF"],
    ]


@pytest.mark.parametrize(
    ("plan_ref", "bearer_ref"),
    [
        (None, "env://CONTEXT_TOKEN"),
        ("plaintext-plan-key", "env://CONTEXT_TOKEN"),
        ("env://PLAN_KEY", None),
        ("env://PLAN_KEY", "plaintext-bearer"),
    ],
)
def test_missing_or_plaintext_config_ref_fails_before_secret_resolution(
    monkeypatch: pytest.MonkeyPatch, plan_ref: str | None, bearer_ref: str | None
) -> None:
    values = {
        "GRAPHOS_PLAN_SEAL_KEY_REF": plan_ref,
        "GRAPHOS_CONTEXT_BEARER_REF": bearer_ref,
    }
    monkeypatch.setattr(host_secrets, "setting", values.get)

    def should_not_resolve(reference: str) -> str:
        raise AssertionError("invalid configuration reached secret resolver")

    monkeypatch.setattr(host_secrets, "resolve_secret_reference", should_not_resolve)
    with pytest.raises(ValueError, match="requires a runtime secret reference"):
        host_secrets.host_secret_ports_from_config()


@pytest.mark.parametrize("resolved", ["raw-key", base64.b64encode(b"short").decode()])
def test_invalid_plan_seal_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch, resolved: str
) -> None:
    values = {
        "GRAPHOS_PLAN_SEAL_KEY_REF": "env://PLAN_KEY",
        "GRAPHOS_CONTEXT_BEARER_REF": "env://CONTEXT_TOKEN",
    }
    monkeypatch.setattr(host_secrets, "setting", values.get)
    monkeypatch.setattr(host_secrets, "resolve_secret_reference", lambda ref: resolved)
    with pytest.raises(ValueError, match="GraphOS plan seal key"):
        host_secrets.host_secret_ports_from_config()
