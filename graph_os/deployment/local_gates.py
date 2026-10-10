"""Local gate enforcement for release qualification (GRAPHOS-RELEASE-R002).

GraphOS refuses to push or deploy a release candidate until every local
qualification gate -- including a local Kubernetes validation run in a
dedicated test namespace -- reports green. This module is the enforcement
primitive: it takes the gate results that other deployment tooling already
produces (pre-commit hooks, the local Kubernetes test-namespace probe) and
raises before any push/deploy call-site proceeds when one of them is red.

It intentionally does not reach into a live cluster itself; callers supply a
probe (for example a read-only ``kubectl`` call scoped to the dedicated test
namespace, in the style of :mod:`graph_os.deployment.kubernetes_first_boot`)
so this module stays testable without a real cluster.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

_NAMESPACE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class GateBlockedError(RuntimeError):
    """Raised when one or more local gates are red at push/deploy time."""

    def __init__(self, failing: Sequence[str]) -> None:
        self.failing = tuple(failing)
        names = ", ".join(self.failing)
        super().__init__(f"local gate(s) not green, push blocked: {names}")


class GateReport(BaseModel):
    """One local qualification gate's observed result."""

    model_config = ConfigDict(frozen=True)

    name: str
    green: bool
    detail: str = ""


def require_green_local_gates(reports: Sequence[GateReport]) -> None:
    """Raise :class:`GateBlockedError` if any report is not green.

    Returns ``None`` (push/deploy may proceed) once every report is green.
    An empty sequence is treated as "nothing qualified yet" and blocks,
    since a release must never promote on the absence of evidence.
    """
    failing = [report.name for report in reports if not report.green]
    if failing or not reports:
        raise GateBlockedError(failing or ["no-local-gates-reported"])


def validate_test_namespace(
    namespace: str,
    probe: Callable[[], Any],
    *,
    gate_name: str = "kubernetes-test-namespace",
) -> GateReport:
    """Run ``probe`` against a dedicated local Kubernetes test namespace.

    ``namespace`` is validated against the same RFC 1123 label pattern used
    by :func:`graph_os.deployment.kubernetes_first_boot.collect_observations`
    so a malformed value fails before any subprocess call is attempted.
    ``probe`` performs the actual read-only validation (real callers pass a
    bound ``collect_observations``-style call); its return value is only
    used to decide green/red, never trusted as a push authorization itself.
    """
    if not _NAMESPACE_RE.fullmatch(namespace):
        return GateReport(
            name=gate_name,
            green=False,
            detail=f"invalid namespace: {namespace!r}",
        )
    try:
        probe()
    except Exception as exc:  # noqa: BLE001 - surfaced as a failing gate, not raised
        return GateReport(name=gate_name, green=False, detail=str(exc))
    return GateReport(name=gate_name, green=True, detail=f"namespace={namespace}")
