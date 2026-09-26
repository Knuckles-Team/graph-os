"""Served-listener preparation: seed, refuse unsafe ``none``, warn (IDM-07)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from graph_os.identity.modes import NoneModeExposureRefused
from graph_os.identity.serving import prepare_identity, webui_session_boundary
from graph_os.identity.setup_gate import SetupGate

from .gate_harness import SETUP_CODE, serve
from .store_double import StoreDouble


def test_tiny_profile_seeds_none_on_loopback_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    served = serve(StoreDouble())
    with caplog.at_level(logging.WARNING):
        mode = asyncio.run(prepare_identity(served.runtime, ["127.0.0.1"]))
    assert mode == "none"
    assert "graph-os-identity claim" in caplog.text


def test_none_mode_off_loopback_is_refused_before_anything_is_seeded() -> None:
    served = serve(StoreDouble())
    with pytest.raises(NoneModeExposureRefused):
        asyncio.run(prepare_identity(served.runtime, ["0.0.0.0"]))
    assert served.store.config is None


def test_stored_none_mode_is_refused_off_loopback_even_when_the_seed_differs() -> None:
    store = StoreDouble()
    served = serve(store)
    asyncio.run(prepare_identity(served.runtime, ["127.0.0.1"]))
    production = serve(store, profile="single-node-prod")
    with pytest.raises(NoneModeExposureRefused):
        asyncio.run(prepare_identity(production.runtime, ["0.0.0.0"]))


def test_production_profile_waits_for_the_first_administrator(
    caplog: pytest.LogCaptureFixture,
) -> None:
    served = serve(StoreDouble(), profile="enterprise")
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(prepare_identity(served.runtime, ["0.0.0.0"])) is None
    assert served.store.config is None
    assert SETUP_CODE not in caplog.text, "an operator-supplied code is never logged"


def test_a_generated_setup_code_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = SetupGate()
    with caplog.at_level(logging.WARNING):
        gate.announce()
        gate.announce()
    assert caplog.text.count("setup code") == 1
    code = caplog.records[0].args[0] if caplog.records[0].args else ""
    assert gate.accepts(str(code)) and not gate.accepts("guess")


def test_webui_boundary_installs_the_gate_with_the_server_role_ladder() -> None:
    served = serve(StoreDouble())
    installed: list[object] = []

    class App:
        def add_middleware(self, middleware: object, **kwargs: object) -> None:
            installed.append((middleware, kwargs))

    webui_session_boundary(served.runtime)(App())
    assert len(installed) == 1
