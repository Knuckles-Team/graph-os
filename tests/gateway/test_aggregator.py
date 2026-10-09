"""Tests for graph_os.gateway.aggregator — migrated from service-dashboard-core."""

from types import SimpleNamespace

import pytest
from agent_utilities.security.brain_context import ActorContext

import graph_os.gateway.aggregator as aggregator_module
from graph_os.gateway.aggregator import Aggregator
from graph_os.gateway.config import ConfigManager
from graph_os.gateway.models import (
    DashboardLayout,
    ServiceCategory,
    ServiceConfig,
    ServiceGroup,
    WidgetData,
)
from graph_os.gateway.registry import Registry
from graph_os.gateway.widgets.base import BaseWidget

# GRAPHOS-FLEET-R032: these tests never want a real process-identity token
# minted (that needs live credential config this suite does not have); they
# stand in a fake, already-verified service session the same shape
# `_service_authority` caches, so `_run_as_service_actor` has a real actor to
# bind without touching `mint_process_identity`.
_TEST_SERVICE_ACTOR = ActorContext(
    actor_id="test-gateway-service",
    roles=("service",),
    tenant_id="test-tenant",
    authenticated=True,
)


@pytest.fixture(autouse=True)
def _fake_service_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SimpleNamespace(
        actor=_TEST_SERVICE_ACTOR, ensure_authority_current=lambda **_kw: None
    )
    monkeypatch.setattr(aggregator_module, "_service_authority", lambda: session)


class MockWidget(BaseWidget):
    service_type = "portainer"
    display_name = "Portainer"

    def get_fields(self):
        return []

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        return WidgetData(
            status="healthy",
            fields={"cpu": 12.5, "memory": 45.2},
            raw={"summary": "Portainer healthy with 2 running containers."},
        )


@pytest.mark.asyncio
async def test_aggregator_fetch_all(tmp_path):
    # Setup registry with MockWidget
    registry = Registry()
    registry._widgets["portainer"] = MockWidget

    # Setup ConfigManager with one service
    config_file = tmp_path / "services.yaml"
    config_manager = ConfigManager(config_path=config_file)

    services = [
        ServiceConfig(
            id="portainer-test",
            name="Portainer Test",
            widget_type="portainer",
            url="http://localhost:9000",
            category=ServiceCategory.INFRASTRUCTURE,
        )
    ]
    group = ServiceGroup(name="Infrastructure", services=services)
    layout = DashboardLayout(groups=[group])
    config_manager.save(layout)

    # Run aggregator
    agg = Aggregator(registry=registry, config_manager=config_manager)
    results = await agg.fetch_all()

    assert "portainer-test" in results
    assert results["portainer-test"].status == "healthy"
    assert results["portainer-test"].fields["cpu"] == 12.5
    assert results["portainer-test"].raw is not None
    assert (
        results["portainer-test"].raw.get("summary")
        == "Portainer healthy with 2 running containers."
    )


@pytest.mark.asyncio
async def test_aggregator_fetch_one(tmp_path):
    registry = Registry()
    registry._widgets["portainer"] = MockWidget

    config_file = tmp_path / "services.yaml"
    config_manager = ConfigManager(config_path=config_file)

    services = [
        ServiceConfig(
            id="portainer-test",
            name="Portainer Test",
            widget_type="portainer",
            url="http://localhost:9000",
            category=ServiceCategory.INFRASTRUCTURE,
        )
    ]
    group = ServiceGroup(name="Infrastructure", services=services)
    layout = DashboardLayout(groups=[group])
    config_manager.save(layout)

    agg = Aggregator(registry=registry, config_manager=config_manager)
    result = await agg.fetch_one("portainer-test")
    assert result.status == "healthy"

    # Fetch non-existent
    result_fail = await agg.fetch_one("non-existent")
    assert result_fail.status == "error"
    assert result_fail.error is not None
    assert "not found" in result_fail.error


class _ActorObservingWidget(BaseWidget):
    """Records the actor bound at fetch time (GRAPHOS-FLEET-R032).

    Before the fix, `widget._safe_fetch` ran on a plain `ThreadPoolExecutor`
    thread with no contextvars copied from the submitting coroutine, so
    `current_actor()` found nothing bound and raised — the same shape as the
    `Authenticated local process context required` failure every real fleet
    widget hit. This widget surfaces that same call so the Aggregator's fix
    is observed directly rather than inferred from a fleet widget's error.
    """

    service_type = "actor-observer"
    display_name = "Actor Observer"
    observed_actor_id: str | None = None

    def get_fields(self):
        return []

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        from agent_utilities.security.brain_context import current_actor

        actor = current_actor()
        type(self).observed_actor_id = str(actor.actor_id)
        return WidgetData(status="healthy")


@pytest.mark.asyncio
@pytest.mark.spec("GRAPHOS-FLEET-R032")
async def test_aggregator_fetch_binds_service_actor(tmp_path):
    """A widget fetch through the Aggregator observes the bound service actor."""
    registry = Registry()
    registry._widgets["actor-observer"] = _ActorObservingWidget
    _ActorObservingWidget.observed_actor_id = None

    config_file = tmp_path / "services.yaml"
    config_manager = ConfigManager(config_path=config_file)
    services = [
        ServiceConfig(
            id="actor-observer-test",
            name="Actor Observer Test",
            widget_type="actor-observer",
            url="http://localhost:9999",
            category=ServiceCategory.INFRASTRUCTURE,
        )
    ]
    group = ServiceGroup(name="Infrastructure", services=services)
    config_manager.save(DashboardLayout(groups=[group]))

    agg = Aggregator(registry=registry, config_manager=config_manager)
    result = await agg.fetch_one("actor-observer-test")

    assert result.status == "healthy"
    assert _ActorObservingWidget.observed_actor_id == _TEST_SERVICE_ACTOR.actor_id
