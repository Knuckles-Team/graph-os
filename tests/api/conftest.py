"""Shared pytest fixtures for ``tests/api/``.

``op_by_id`` looks up one real ``OpSpec`` from a module's ``operations()``
tuple by its id, so the ops-port tests exercising a ``handle_*`` function's
third positional argument (the bound ``OpSpec``) pass the genuine spec
instead of ``None``, without each test module minting its own ad hoc lookup.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pytest

from graph_os.api.registry import OpSpec


@pytest.fixture
def op_by_id() -> Callable[[Iterable[OpSpec], str], OpSpec]:
    def _lookup(operations: Iterable[OpSpec], op_id: str) -> OpSpec:
        return next(op for op in operations if op.id == op_id)

    return _lookup
