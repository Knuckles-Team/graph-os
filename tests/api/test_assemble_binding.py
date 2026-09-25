"""One canonical direct EG assembly binding in the curated registry."""

from graph_os.api.ops import decide, swarm
from graph_os.api.registry import EgMethod


def test_agent_assemble_has_one_direct_owner() -> None:
    direct = [
        op.id
        for op in (*decide.specs(), *swarm.specs())
        if isinstance(op.binding, EgMethod) and op.binding.service == "AgentAssemble"
    ]
    assert direct == ["decide.assemble"]
    assert swarm.specs() == ()
