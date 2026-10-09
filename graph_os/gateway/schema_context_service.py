"""Schema-context application service (GRAPHOS-DATA-MARKET-R005, GDM-02).

One owning service between a validated :class:`SchemaContextRequest` and a
published graph-engine schema-context answer. GraphOS composes and projects;
it never derives a catalog fact, join, or ontology mapping itself.

epistemic-graph has not published a schema-context capability yet
(``specs/data-and-market-projections/plan.md``, "Schema context" section).
GraphOS fails closed rather than inventing one: :func:`get_schema_context`
refuses every request with ``UNAVAILABLE`` until a real engine client
exposes ``schema_context`` and this service's dispatch path is proven
against it (GDM-03, test IDs SC-02/SC-03 in
``specs/data-and-market-projections/test-spec.md``).
"""

from __future__ import annotations

from typing import Any

from graph_os.api.errors import GraphOSErrorCode, GraphOSRefusal
from graph_os.gateway.schemas.schema_context import SchemaContextRequest

__all__ = ["get_schema_context"]


def _schema_context_capability(engine: Any) -> Any | None:
    """Return the engine's published schema-context client, or ``None``.

    A plain attribute probe, not an import of an engine implementation:
    GraphOS consumes only the public client its composition root supplies.
    """

    return getattr(engine, "schema_context", None)


def get_schema_context(engine: Any, request: SchemaContextRequest) -> Any:
    """Answer one schema-context request through the public engine client.

    Refuses with ``GraphOSErrorCode.UNAVAILABLE`` before any engine dispatch
    when the capability is absent, satisfying SC-04 without a fabricated
    fact. The caller supplies an already-validated ``request``; a malformed
    request never reaches this function (SC-01 is enforced by
    :class:`SchemaContextRequest` itself).
    """

    capability = _schema_context_capability(engine)
    if capability is None:
        raise GraphOSRefusal(
            GraphOSErrorCode.UNAVAILABLE,
            "graph engine has no schema-context capability",
            details={"source_id": request.source_id, "intent": request.intent},
        )
    return capability.schema_context(request)
