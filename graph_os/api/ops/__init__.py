"""Declared GraphOS operation modules (GRAPHOS-FLEET-R015/R018).

Each sibling module is a self-contained source of typed :class:`OpSpec`
operations for one domain, built directly against
:mod:`graph_os.api.registry` (and, where noted, an injected service bound at
``context.services``). This package does not yet compose a single
all-domain registry: the full cross-repository operation set named by
``graph_os.api.ops.get_registry()`` in the wider API program spans domains
(analytics, capacity, decide, finance, ingest, ...) that are not implemented
in this repository yet, and publishing a composed registry ahead of those
domains would advertise operations that do not exist. Each domain module's
own ``operations()``/``specs()`` factory is independently importable and
tested; a future lane wires the full composition once enough domains land.
"""

from __future__ import annotations

__all__: list[str] = []
