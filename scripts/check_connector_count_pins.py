#!/usr/bin/env python3
"""Fail when a fleet connector count-pin site disagrees with the live count.

Real CLI entry point for ``GRAPHOS-FLEET-R023.1`` (producer slice, see
``specs/fleet-catalog-and-tools``). The comparison itself is the pure
``graph_os.fleet.connector_count_pins.check_connector_count_pins``; this
script parses ``--site`` arguments into ``CountPinSite`` values, runs the
comparison, and exits non-zero on any mismatch.

Each ``--site`` is one count-pin site as ``name=location=declared_count``.

This script does not yet hardcode the fleet's real count-pin sites.
``deploy/release/compatibility-matrix.yml`` and its check script are
AU-owned (see ``graph_os/deployment/doctor_certification.py``, which already
notes "Production release/certification assets remain AU-owned"); the
bundle-catalog schema, ``ontology.lock`` and the federated IRI do not exist
anywhere in the fleet today; and the live connector count itself is only
known to ``agent_connector_sdk`` / the cross-repo fleet registry, not to
graph-os in isolation. Wiring real sites in is cross-repo follow-up tracked
under ``GRAPHOS-FLEET-R023``.
"""

from __future__ import annotations

from graph_os.fleet.connector_count_pins import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
