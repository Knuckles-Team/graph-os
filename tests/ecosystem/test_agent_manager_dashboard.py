"""Tests for the Agent Manager Dashboard (CONCEPT:AU-ECO.ui.agent-manager-dashboard).

Ported from the agent runtime's
``tests/unit/ecosystem/test_agent_manager_dashboard.py`` (GRAPHOS-HOST-R008).
Proves :class:`graph_os.ecosystem.AgentManagerDashboard` is reachable from the
package's public surface (``graph_os.ecosystem``, not only its own submodule) and
that ``run()`` produces a real report — no engine required, since every check degrades to a
``warning``/``error`` row rather than raising when its target is absent.
"""

from __future__ import annotations

from pathlib import Path

from graph_os.ecosystem import AgentManagerDashboard, DashboardReport


def test_dashboard_importable_from_package_root() -> None:
    """Reachable via ``graph_os.ecosystem``, matching every sibling module."""
    from graph_os import ecosystem

    assert ecosystem.AgentManagerDashboard is AgentManagerDashboard
    assert ecosystem.DashboardReport is DashboardReport


def test_run_against_empty_workspace_reports_missing_agents_md(tmp_path: Path) -> None:
    dashboard = AgentManagerDashboard(workspace=tmp_path, engine=None)
    report = dashboard.run()

    assert isinstance(report, DashboardReport)
    assert report.components
    assert 0.0 <= report.health_score <= 1.0
    assert report.summary["total"] == len(report.components)

    agents_md_rows = [c for c in report.components if c.name == "AGENTS.md"]
    assert agents_md_rows and agents_md_rows[0].status == "error"


def test_run_against_workspace_with_agents_md_is_ok(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    dashboard = AgentManagerDashboard(workspace=tmp_path)
    report = dashboard.run()

    agents_md_rows = [c for c in report.components if c.name == "AGENTS.md"]
    assert agents_md_rows and agents_md_rows[0].status == "ok"


def test_report_renders_markdown_and_dict(tmp_path: Path) -> None:
    dashboard = AgentManagerDashboard(workspace=tmp_path)
    report = dashboard.run()

    md = report.to_markdown()
    assert "Agent Manager Dashboard" in md
    assert "Health Score" in md

    d = report.to_dict()
    assert d["health_score"] == report.health_score
    assert len(d["components"]) == len(report.components)


def test_skills_usage_check_queries_the_engine_by_duck_type(tmp_path: Path) -> None:
    """The engine parameter is structurally typed: any ``query_cypher`` works."""

    class _FakeEngine:
        def query_cypher(self, query: str) -> list[dict[str, object]]:
            assert "skill" in query
            return [{"name": "fixture-skill", "uses": 3}]

    dashboard = AgentManagerDashboard(workspace=tmp_path, engine=_FakeEngine())
    report = dashboard.run()

    skill_rows = [c for c in report.components if c.category == "skills"]
    assert skill_rows and skill_rows[0].name == "fixture-skill"
    assert skill_rows[0].usage_count == 3
