"""Focused tests for the GraphOS-owned co-service supervisor."""

from __future__ import annotations

import contextvars
import threading

import pytest

from graph_os.mcp_server.composition import CoServiceSupervisor


def test_native_supervisor_preserves_ambient_context() -> None:
    authority = contextvars.ContextVar("test_co_service_authority")
    authority.set("verified")
    observed: list[str] = []
    started = threading.Event()

    def run(stop_event: threading.Event) -> None:
        observed.append(authority.get())
        started.set()
        stop_event.wait()

    supervisor = CoServiceSupervisor()
    supervisor.start_service("test", run, session=object())

    assert started.wait(timeout=1.0)
    assert supervisor.running() == ("test",)
    assert supervisor.stop_all(timeout=1.0) is True
    assert observed == ["verified"]


def test_native_supervisor_rejects_missing_session() -> None:
    supervisor = CoServiceSupervisor()

    with pytest.raises(PermissionError, match="verified co-service session"):
        supervisor.start_service("test", lambda _stop: None, session=None)
