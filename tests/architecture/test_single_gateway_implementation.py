"""GRAPHOS-HOST-R004: a single gateway implementation under graph_os/gateway/.

A package-layout test that fails closed if a second, duplicate gateway
package is ever added outside ``graph_os/gateway/``. The retired
``graph-os-daemon`` console script and ``gateway-widgets`` package extra are
tracked separately (status.json records them as still-present, staged
removals) and are out of scope for this structural layout check.
"""

from __future__ import annotations

import pytest

from pathlib import Path

GRAPH_OS_ROOT = Path(__file__).resolve().parents[2] / "graph_os"
GATEWAY_ROOT = GRAPH_OS_ROOT / "gateway"


def _top_level_packages_named_like_gateway() -> set[Path]:
    return {
        path
        for path in GRAPH_OS_ROOT.iterdir()
        if path.is_dir() and "gateway" in path.name.lower()
    }


@pytest.mark.spec('GRAPHOS-HOST-R004')
def test_graph_os_gateway_package_exists() -> None:
    assert GATEWAY_ROOT.is_dir()
    assert (GATEWAY_ROOT / "__init__.py").is_file()


@pytest.mark.spec('GRAPHOS-HOST-R004')
def test_no_duplicate_top_level_gateway_package_exists() -> None:
    found = _top_level_packages_named_like_gateway()
    assert found == {GATEWAY_ROOT}, (
        f"expected graph_os/gateway/ to be the sole gateway package, found: {found}"
    )
