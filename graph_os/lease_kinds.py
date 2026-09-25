"""Every EG control-lease kind graph-os-hosted code writes, by signing identity.

EG restricts graph-os's own principal to an allowlist of control-lease kinds
(``EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON``, operator ruling
2026-09-24). That list must be exactly the kinds graph-os writes under ITS OWN
process identity. Kinds written under a verified caller's identity (a request's
task-local session) do not belong on it.

``tests/test_lease_kinds.py`` scans graph-os and the agent-utilities code it
hosts for control-lease issue sites. It fails when a kind is not classified
here, or when the documented deploy allowlist (``docs/deployment.md``) differs
from :data:`PROCESS_IDENTITY_LEASE_KINDS`. The evidence for each
classification is in ``docs/deployment.md``.
"""

from __future__ import annotations

__all__ = ["CALLER_IDENTITY_LEASE_KINDS", "PROCESS_IDENTITY_LEASE_KINDS"]

#: Written under graph-os's process identity: its background loops and the
#: finance executor. This is the graph-os allowlist EG enforces.
PROCESS_IDENTITY_LEASE_KINDS: frozenset[str] = frozenset(
    {
        # graph_os.finance.orders.propose_order on the FinanceService client.
        "finance.order-proposal",
        # agent_utilities.orchestration.action_policy queue_approval from the
        # autonomous loops (fleet reconciler, auto-merge, remediation
        # playbooks, guardrail evolution, spec proposals), and their drains.
        "action.approval",
    }
)

#: Written only under the verified caller's own identity, never graph-os's.
CALLER_IDENTITY_LEASE_KINDS: frozenset[str] = frozenset(
    {
        # graph_os.browser_control: the human's browser channel session.
        "browser.control",
        "browser.registration",
        "browser.document",
        "browser.attended_arm",
        "browser.recent_auth",
    }
)
