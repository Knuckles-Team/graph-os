"""Compose the declared GraphOS operation set into one immutable registry."""

from __future__ import annotations

from importlib import import_module

from graph_os.api.registry import OpSpec, Registry

# Each module is an explicit source of public operations. Missing declarations
# are a build error, not a reason to publish an incomplete registry.
# Identity administration joins this list with the Train 7 broker and its
# scoped operation modules; Train 5 cannot advertise those operations early.
CURATED_SOURCES = (
    ("agents", "operations"),
    ("analytics", "specs"),
    ("atlas", "specs"),
    ("browser", "operations"),
    ("capacity", "specs"),
    ("decide", "specs"),
    ("decisions", "specs"),
    ("evolution", "specs"),
    ("federation", "specs"),
    ("finance", "specs"),
    ("fleet", "operations"),
    ("graph", "specs"),
    ("ingest", "specs"),
    ("markets", "specs"),
    ("memory", "specs"),
    ("ontology", "specs"),
    ("object_sets", "specs"),
    ("ops", "specs"),
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
    curated: list[OpSpec] = []
    for name, factory in CURATED_SOURCES:
        module = import_module(f"graph_os.api.ops.{name}")
        curated.extend(getattr(module, factory)())
    binding = import_module("graph_os.api.registry.eg_binding")
    operations = (*curated, *binding.load_eg_bindings(curated_ops=curated))
    return Registry(operations)
