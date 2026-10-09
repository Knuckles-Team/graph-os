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
