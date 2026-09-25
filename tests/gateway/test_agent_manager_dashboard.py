"""Tests for the Agent Manager Dashboard (CONCEPT:AU-ECO.ui.agent-manager-dashboard).

Proves the GraphOS-owned dashboard ``run()`` produces a real report — no engine required, since every check degrades to a
``warning``/``error`` row rather than raising when its target is absent.
"""

from __future__ import annotations

from pathlib import Path

from graph_os.gateway.agent_manager_dashboard import (
    AgentManagerDashboard,
    DashboardReport,
)


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


def test_skill_usage_uses_injected_read_port(tmp_path: Path) -> None:
    class SkillReader:
        def query_cypher(self, query: str) -> list[dict[str, object]]:
            assert "USED_SKILL" in query
            return [{"name": "planner", "uses": 3}]

    report = AgentManagerDashboard(workspace=tmp_path, engine=SkillReader()).run()
    skills = [item for item in report.components if item.category == "skills"]
    assert len(skills) == 1
    assert skills[0].name == "planner"
    assert skills[0].usage_count == 3


def test_skill_reader_failure_keeps_dashboard_available(tmp_path: Path, caplog) -> None:
    class FailingReader:
        def query_cypher(self, query: str) -> list[dict[str, object]]:
            raise RuntimeError("private connection detail")

    report = AgentManagerDashboard(workspace=tmp_path, engine=FailingReader()).run()
    assert report.summary["total"] == len(report.components)
    assert "private connection detail" not in caplog.text
