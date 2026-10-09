"""Shared capacity partition/config fixtures for ``tests/fleet/`` AIMD tests.

``CAPACITY_PARTITION`` and ``CAPACITY_CONFIG`` are the one tenant/config pair
every ``graph_os.fleet.error_budget`` / ``graph_os.fleet.throttle_service``
test in this directory decides against; duplicating the same
``Partition``/``AimdConfig`` literals in each module just lets them drift
apart under edits.
"""

from __future__ import annotations

from graph_os.fleet.error_budget import AimdConfig, Partition

CAPACITY_PARTITION = Partition(
    tenant="t1", child="search", operation_class="read", policy_revision="p1"
)
CAPACITY_CONFIG = AimdConfig(
    version="v1",
    alpha=2,
    beta=0.5,
    floor=1,
    min_sample_count=4,
    error_budget_fraction=0.2,
)
