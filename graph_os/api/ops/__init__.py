"""Compose the declared GraphOS operation set into one immutable registry."""

from __future__ import annotations

from importlib import import_module

from graph_os.api.registry import OpSpec, Registry
from graph_os.api.registry.domain_scopes import domain_scopes

# Each module is an explicit source of public operations. Missing declarations
# are a build error, not a reason to publish an incomplete registry.
# Identity administration joins this list with the Train 7 broker and its
# scoped operation modules; Train 5 cannot advertise those operations early.
CURATED_SOURCES = (
    ("action_verify", "specs"),
    ("agents", "operations"),
    ("analytics", "specs"),
    ("approvals", "specs"),
    ("browser", "operations"),
    ("capacity", "specs"),
    ("decide", "specs"),
    ("decisions", "specs"),
    ("evolution", "specs"),
    ("federation", "specs"),
    ("finance", "specs"),
    ("fleet", "operations"),
    ("fleet_observability", "operations"),
    ("graph", "specs"),
    ("harness", "specs"),
    ("ingest", "specs"),
    ("markets", "specs"),
    ("memory", "specs"),
    ("ontology", "specs"),
    ("object_sets", "served_specs"),
    ("ops", "specs"),
    ("plan", "specs"),
    ("policy", "specs"),
    ("query", "specs"),
    ("retrieval", "specs"),
    ("search", "specs"),
    ("security", "specs"),
    ("swarm", "specs"),
    ("telemetry", "specs"),
    ("usage", "specs"),
    ("work", "specs"),
)


def get_registry() -> Registry:
    # IDM-05 is a startup dependency: an old or incomplete EG wheel must not
    # expose a GraphOS API that advertises unregistered domain scopes.
    domain_scopes()
    curated: list[OpSpec] = []
    for name, factory in CURATED_SOURCES:
        module = import_module(f"graph_os.api.ops.{name}")
        curated.extend(getattr(module, factory)())
    binding = import_module("graph_os.api.registry.eg_binding")
    operations = (*curated, *binding.load_eg_bindings(curated_ops=curated))
    return Registry(operations)
