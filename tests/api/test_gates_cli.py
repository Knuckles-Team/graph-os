"""GRAPHOS-FLEET-R016: the API-surface and backward-compat CLI gates
fail closed against a drifted fixture registry and pass against a
compliant one. Each script is a thin CLI over
``graph_os.api.registry.surface_gate``; these tests exercise the CLI
layer (baseline load/write, exit codes) rather than re-testing the pure
diff functions, which `tests/api/test_surface_drift_gate.py` and
`tests/api/test_backward_compat_drift_gate.py` already cover.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from graph_os.api.registry import (
    Composite,
    Effect,
    HttpShape,
    OpSpec,
    PrincipalRule,
    Verb,
)

check_api_surface = importlib.import_module("scripts.check_api_surface")
check_api_compat = importlib.import_module("scripts.check_api_compat")


class _Input(BaseModel):
    user_id: str


class _Output(BaseModel):
    ok: bool


def _op(op_id: str = "identity.users.read", **changes: object) -> OpSpec:
    values: dict[str, object] = {
        "id": op_id,
        "verb": Verb.FIND,
        "summary": "Read a user",
        "examples": ("Look up this user",),
        "params": _Input,
        "result": _Output,
        "binding": Composite(handler="graph_os.identity.admin_service.read_user"),
        "principals": PrincipalRule.ANY,
        "effect": Effect.READ,
        "http": HttpShape(method="GET", path="/v1/identity/users/{user_id}"),
    }
    values.update(changes)
    return OpSpec(**values)  # type: ignore[arg-type]


@pytest.mark.spec("GRAPHOS-FLEET-R016")
def test_surface_gate_writes_initial_baseline_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "surface_baseline.json"
    monkeypatch.setattr(check_api_surface, "BASELINE", baseline)
    monkeypatch.setattr(check_api_surface, "_load_current", lambda: {"a": {"x": 1}})
    assert check_api_surface.main() == 0
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"a": {"x": 1}}
    assert check_api_surface.main() == 0


@pytest.mark.spec("GRAPHOS-FLEET-R016")
def test_surface_gate_fails_closed_on_drifted_fixture_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "surface_baseline.json"
    monkeypatch.setattr(check_api_surface, "BASELINE", baseline)
    monkeypatch.setattr(
        check_api_surface,
        "_load_current",
        lambda: check_api_surface.public_surface([_op()]),
    )
    assert check_api_surface.main() == 0  # bootstraps the baseline
    monkeypatch.setattr(
        check_api_surface,
        "_load_current",
        lambda: check_api_surface.public_surface(
            [_op(), _op("identity.users.disable")]
        ),
    )
    assert check_api_surface.main() == 1


@pytest.mark.spec("GRAPHOS-FLEET-R016")
def test_compat_gate_writes_initial_baseline_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "compat_baseline.json"
    monkeypatch.setattr(check_api_compat, "BASELINE", baseline)
    monkeypatch.setattr(
        check_api_compat, "_current_ops", lambda: {"identity.users.read": _op()}
    )
    assert check_api_compat.main() == 0
    written = json.loads(baseline.read_text(encoding="utf-8"))
    assert written["identity.users.read"]["principals"] == "any"
    assert check_api_compat.main() == 0


@pytest.mark.spec("GRAPHOS-FLEET-R016")
def test_compat_gate_fails_closed_on_narrowed_fixture_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "compat_baseline.json"
    monkeypatch.setattr(check_api_compat, "BASELINE", baseline)
    wide = _op(scopes=frozenset({"identity:read"}), principals=PrincipalRule.ANY)
    monkeypatch.setattr(check_api_compat, "_current_ops", lambda: {wide.id: wide})
    assert check_api_compat.main() == 0  # bootstraps the baseline

    narrowed = _op(
        scopes=frozenset({"identity:read", "identity:admin"}),
        principals=PrincipalRule.HUMAN,
    )
    monkeypatch.setattr(
        check_api_compat, "_current_ops", lambda: {narrowed.id: narrowed}
    )
    assert check_api_compat.main() == 1
