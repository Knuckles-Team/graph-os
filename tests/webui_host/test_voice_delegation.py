"""The GraphOS WebUI host supplies governed voice transcription."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from graph_os.webui_host.voice_delegation import webui_voice_delegation_helpers


def test_voice_helper_delegates_without_persisting_clip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = webui_voice_delegation_helpers()["transcribe_voice"]
    response = SimpleNamespace(
        available=True,
        raw={"segments": [{"text": " hello "}, {"text": "world"}]},
    )
    sidecar = types.ModuleType("agent_utilities.media.sidecar_delegate")
    delegate = Mock(return_value=response)
    sidecar.delegate_extract = delegate
    monkeypatch.setitem(sys.modules, sidecar.__name__, sidecar)
    result = asyncio.run(helper(content=b"clip", content_type="audio/webm"))

    assert result == {"text": "hello world"}
    assert delegate.call_args.args == (b"clip",)
    assert delegate.call_args.kwargs == {
        "digest": hashlib.sha256(b"clip").hexdigest(),
        "media_type": "audio/webm",
        "modality": "audio",
    }


def test_voice_helper_fails_closed_when_sidecar_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = webui_voice_delegation_helpers()["transcribe_voice"]
    response = SimpleNamespace(
        available=False,
        error="sidecar unavailable",
        raw={},
    )
    sidecar = types.ModuleType("agent_utilities.media.sidecar_delegate")
    sidecar.delegate_extract = Mock(return_value=response)
    monkeypatch.setitem(sys.modules, sidecar.__name__, sidecar)
    with pytest.raises(RuntimeError, match="sidecar unavailable"):
        asyncio.run(helper(content=b"clip", content_type="audio/webm"))
