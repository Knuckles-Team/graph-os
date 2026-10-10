"""GRAPHOS-MESSAGING-R005: a typed DEGRADED state for a missing channel adapter.

``MessagingRegistry.create_all_enabled`` must not only log and omit a
configured channel whose adapter fails to load — it must also record that
channel ``DEGRADED`` with a typed reason (``AdapterMissing`` /
``AdapterImportFailed``), while every other configured channel is still
created normally.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from graph_os.messaging.registry import MessagingRegistry
from graph_os.messaging.router import InboundRouter
from graph_os.messaging.supervision import (
    AdapterImportFailed,
    AdapterMissing,
    ChannelSupervisionState,
)

from ._fakes import FakeMessagingBackend, synthetic_token


class _PresentBackend(FakeMessagingBackend):
    """A backend whose adapter loads fine, constructed with ``config=``."""

    def __init__(self, config: Any) -> None:
        super().__init__(platform=config.platform)


class _FakeEntryPoint:
    """Minimal stand-in for an ``importlib.metadata.EntryPoint``."""

    def __init__(self, name: str, load: Any) -> None:
        self.name = name
        self.value = f"fake:{name}"
        self._load = load

    def load(self) -> Any:
        return self._load()


def _registry_with(entry_points: dict[str, _FakeEntryPoint]) -> MessagingRegistry:
    """A ``MessagingRegistry`` seeded with fake entry-points, bypassing the
    real ``importlib.metadata`` scan in ``_discover()``."""
    registry = MessagingRegistry.__new__(MessagingRegistry)
    registry._entry_points = dict(entry_points)
    registry._instances = {}
    registry._degraded = {}
    return registry


def test_create_backend_raises_typed_adapter_missing_when_not_installed() -> None:
    registry = _registry_with({})
    with pytest.raises(AdapterMissing):
        registry.create_backend("not-a-real-backend")


@pytest.mark.spec("GRAPHOS-MESSAGING-R005")
def test_create_all_enabled_degrades_missing_adapter_and_creates_present_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESSAGING_MISSING_TOKEN", synthetic_token("x", "y", "z"))
    monkeypatch.setenv("MESSAGING_PRESENT_TOKEN", synthetic_token("a", "b", "c"))

    def _fail_import() -> Any:
        raise ImportError("no module named fake_missing_sdk")

    registry = _registry_with(
        {
            "missing": _FakeEntryPoint("missing", _fail_import),
            "present": _FakeEntryPoint("present", lambda: _PresentBackend),
        }
    )

    created = registry.create_all_enabled()

    assert set(created) == {"present"}
    assert isinstance(created["present"], _PresentBackend)

    assert registry.degraded_backends() == {"missing": ChannelSupervisionState.DEGRADED}
    reason = registry.degraded_reason("missing")
    assert isinstance(reason, AdapterImportFailed)
    assert registry.degraded_reason("present") is None


@pytest.mark.asyncio
@pytest.mark.spec("GRAPHOS-MESSAGING-R005")
async def test_present_channel_still_reaches_running_while_missing_one_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESSAGING_MISSING_TOKEN", synthetic_token("x", "y", "z"))
    monkeypatch.setenv("MESSAGING_PRESENT_TOKEN", synthetic_token("a", "b", "c"))

    def _fail_import() -> Any:
        raise ImportError("no module named fake_missing_sdk")

    registry = _registry_with(
        {
            "missing": _FakeEntryPoint("missing", _fail_import),
            "present": _FakeEntryPoint("present", lambda: _PresentBackend),
        }
    )

    created = registry.create_all_enabled()
    assert registry.degraded_backends() == {"missing": ChannelSupervisionState.DEGRADED}

    router = InboundRouter()
    router.register_backend(created["present"])

    start_task = asyncio.create_task(router.start())
    await asyncio.sleep(0)
    assert router.state_of("present") is ChannelSupervisionState.RUNNING

    await router.stop()
    await asyncio.wait_for(start_task, timeout=1)
