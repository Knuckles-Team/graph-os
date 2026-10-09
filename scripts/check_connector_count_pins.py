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

import argparse
import sys
from collections.abc import Sequence

from graph_os.fleet.connector_count_pins import (
    CountPinSite,
    check_connector_count_pins,
)


def _parse_site(raw: str) -> CountPinSite:
    parts = raw.split("=")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"--site must be name=location=declared_count, got {raw!r}"
        )
    name, location, count_text = parts
    if not name.strip() or not location.strip():
        raise argparse.ArgumentTypeError(
            f"--site name and location must be non-empty, got {raw!r}"
        )
    try:
        declared_count = int(count_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--site declared_count must be an integer, got {count_text!r}"
        ) from exc
    return CountPinSite(name=name, location=location, declared_count=declared_count)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-count",
        type=int,
        required=True,
        help="the current actual fleet connector count",
    )
    parser.add_argument(
        "--site",
        dest="sites",
        type=_parse_site,
        action="append",
        default=[],
        help="a count-pin site as name=location=declared_count; repeatable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mismatches = check_connector_count_pins(args.live_count, args.sites)
    if not mismatches:
        print(
            f"OK: {len(args.sites)} connector count-pin site(s) agree with "
            f"live count {args.live_count}"
        )
        return 0
    for mismatch in mismatches:
        print(
            f"MISMATCH: {mismatch.site} ({mismatch.location}) declares "
            f"{mismatch.declared_count}, live count is {mismatch.live_count}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
