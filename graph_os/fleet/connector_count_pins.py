"""Pure connector count-pin consistency checking.

Producer slice for ``GRAPHOS-FLEET-R023.1`` (see
``specs/fleet-catalog-and-tools``). This module holds only the typed model
and a pure comparison: given the live fleet connector count and a list of
count-pin sites, which sites disagree. It has no file I/O, no network, and no
knowledge of where any real site lives -- wiring it to the fleet's actual
count-pin sites (the compatibility matrix, the bundle-catalog schema,
``ontology.lock``, the federated IRI, ``genesis.yaml``) is tracked as
follow-up under ``GRAPHOS-FLEET-R023``, not done here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["CountPinSite", "ConnectorCountMismatch", "check_connector_count_pins"]


@dataclass(frozen=True, slots=True)
class CountPinSite:
    """One place in the fleet that pins a connector count."""

    name: str
    location: str
    declared_count: int


@dataclass(frozen=True, slots=True)
class ConnectorCountMismatch:
    """A count-pin site whose declared count disagrees with the live count."""

    site: str
    location: str
    declared_count: int
    live_count: int


def check_connector_count_pins(
    live_count: int, sites: Sequence[CountPinSite]
) -> list[ConnectorCountMismatch]:
    """Return a mismatch for every site whose declared count differs.

    An empty result means every site agrees with ``live_count``.
    """

    return [
        ConnectorCountMismatch(
            site=site.name,
            location=site.location,
            declared_count=site.declared_count,
            live_count=live_count,
        )
        for site in sites
        if site.declared_count != live_count
    ]


# Command line (console script `graph-os-connector-count-pins`).


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
    parser = argparse.ArgumentParser(
        description="Check that every connector count-pin site agrees with the live fleet count."
    )
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
