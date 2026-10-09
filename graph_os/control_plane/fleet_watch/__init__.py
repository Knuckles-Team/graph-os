"""Fleet deploy-watch verdict logic (GRAPHOS-FLEET-R024.1).

See :mod:`graph_os.control_plane.fleet_watch.service` for the ported
decision rule and :mod:`graph_os.api.ops.fleet_deploy_watch` for the typed
operation that serves it.
"""

from __future__ import annotations

from .service import (
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    OUTCOME_UNOBSERVED,
    DeployProbe,
    DeployWatchVerdict,
    evaluate_deploy_watch,
)

__all__ = [
    "OUTCOME_FAILED",
    "OUTCOME_SUCCESS",
    "OUTCOME_UNOBSERVED",
    "DeployProbe",
    "DeployWatchVerdict",
    "evaluate_deploy_watch",
]
