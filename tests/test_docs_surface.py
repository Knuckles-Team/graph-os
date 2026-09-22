"""Public Graph OS documentation and shared-theme contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
PIPELINES_REVISION = "444b232c7975e125a24b17d53ff615f5ad26a4cd"


def test_readme_uses_the_public_title_and_exact_section_order() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    headings = [line for line in readme.splitlines() if line.startswith("## ")]

    # Title-case section headings are the standardized public front door;
    # the shared `public-surface` pre-commit gate accepts this README as is.
    assert readme.startswith("# Graph OS\n")
    assert headings == [
        "## Overview",
        "## Key Capabilities",
        "## Documentation",
        "## Architecture",
        "## Quick Start",
        "## Contributing",
        "## License",
    ]
    assert "docs/assets/runtime-architecture.svg" in readme
    assert "runtime-architecture.mmd" not in readme


def test_architecture_names_each_supported_ecosystem_entrypoint() -> None:
    architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

    for entrypoint in (
        "Agent Web UI",
        "Agent Terminal UI",
        "Geniusbot",
        "Messaging channels",
        "MCP",
        "REST",
        "A2A",
    ):
        assert entrypoint in architecture
    assert "conversational ACP chat remains an explicit contract gap" in architecture
    assert (
        "adapters and the inbound router remain owned by agent-utilities"
        in architecture
    )


def test_pages_uses_the_immutable_shared_brand_revision() -> None:
    pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

    assert (
        "Knuckles-Team/pipelines/.github/workflows/pages_pipeline.yml@"
        f"{PIPELINES_REVISION}"
    ) in pages
    assert "shared_theme_enabled: true" in pages
