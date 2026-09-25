"""Compose the declared GraphOS operation set into one immutable registry."""

from __future__ import annotations

from importlib import import_module

from graph_os.api.registry import OpSpec, Registry

# Each module is an explicit source of public operations. Missing declarations
# are a build error, not a reason to publish an incomplete registry.
CURATED_MODULES = (
    "graph_os.api.ops.identity_admin",
    "graph_os.api.ops.identity_config",
)


def get_registry() -> Registry:
    curated: list[OpSpec] = []
    for name in CURATED_MODULES:
        module = import_module(name)
        specs = module.specs
        curated.extend(specs())
    binding = import_module("graph_os.api.registry.eg_binding")
    operations = (*curated, *binding.load_eg_bindings(curated_ops=curated))
    return Registry(operations)
