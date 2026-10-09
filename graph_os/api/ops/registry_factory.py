"""Offline declaration assembly; serving authority validation belongs to bootstrap."""

from __future__ import annotations

from inspect import iscoroutinefunction

from graph_os.api.registry import Composite, OpSpec, Registry
from graph_os.api.registry.eg_binding import (
    EgContractError,
    _contract_root,
    _load_scopes,
    load_eg_bindings,
)


def get_registry() -> Registry:
    """Compose supported declarations and the complete installed provider contract.

    This requires no live session or service bindings, so canonical generation
    can use the same registry as serving. Bootstrap must separately validate
    live authorities before exposing these declarations as usable operations.
    Provider failures propagate; there is no partial-registry fallback.
    """
    from . import access, agents, browser, capacity, decide, fleet, ingest, work

    curated: list[OpSpec] = []
    for module in (access, agents, browser, capacity, decide, fleet, ingest, work):
        for op in module.operations():
            if isinstance(op.binding, Composite):
                namespace, _, name = op.binding.handler.rpartition(".")
                handler = getattr(module, name, None)
                if namespace != module.__name__ or not iscoroutinefunction(handler):
                    raise ValueError(
                        f"{op.id}: unsupported or unbound handler {op.binding.handler}"
                    )
            curated.append(op)
    root = _contract_root(None)
    scopes = _load_scopes(root)
    for op in curated:
        missing = (op.scopes | op.executor_scopes) - scopes.keys()
        if missing:
            raise EgContractError(
                f"{op.id}: unregistered curated EG scopes: {', '.join(sorted(missing))}"
            )
    generated = load_eg_bindings(contract_root=root, curated_ops=curated)
    return Registry((*curated, *generated))
