"""Pin the two wiring gates' behavior against small fixture packages.

``scripts/check_wiring.py`` has no third-party dependencies, so these tests
run it exactly as the ``check-orphan-modules`` and ``check-unreachable-modules``
pre-commit hooks do: as a subprocess, against a throwaway ``graph_os/``
package built under ``tmp_path``, never the real repository tree.

The fixture package covers every shape the two gates must tell apart:

* a reachable chain from a declared root (``graph_os`` itself and a
  ``[project.scripts]`` entry) through a static import, a function-local
  import, and a dynamic import-by-string;
* an isolated module (zero fan-in, zero fan-out) -- an orphan AND
  unreachable;
* a mutually-importing cluster reachable from no root -- NOT an orphan
  (nonzero fan-in/fan-out) but unreachable.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_wiring.py"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _build_fixture(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        """
        [project]
        name = "fixture"
        [project.scripts]
        fixture-entry = "graph_os.reached.mid:entry_fn"
        """,
    )
    _write(root / "graph_os" / "__init__.py", '"""Fixture root package."""\n')
    _write(root / "graph_os" / "reached" / "__init__.py", "")
    _write(
        root / "graph_os" / "reached" / "mid.py",
        """
        \"\"\"Reachable root (declared console script target).\"\"\"

        import graph_os.reached.leaf

        # A plugin/registration-style map, resolved by VALUE, never a static
        # import -- mirrors graph_os.gateway.registry's widget loader.
        _DYNAMIC_TARGETS = {"d": "graph_os.dynamic_target"}


        def entry_fn() -> None:
            # Import only reached when this function actually runs --
            # the gate must still see it statically.
            import graph_os.func_local

            graph_os.func_local.noop()
        """,
    )
    _write(root / "graph_os" / "reached" / "leaf.py", '"""Reached transitively."""\n')
    _write(
        root / "graph_os" / "func_local.py",
        """
        \"\"\"Reached only through a function-local import in mid.py.\"\"\"


        def noop() -> None:
            return None
        """,
    )
    _write(
        root / "graph_os" / "dynamic_target.py",
        """
        \"\"\"Reached only through mid.py's dynamic string-keyed map.

        Imports something from the package itself so the (unchanged, static-only)
        orphan check does not flag it -- matching how every real widget module
        imports its shared base module.
        \"\"\"

        import graph_os.reached.leaf
        """,
    )
    _write(
        root / "graph_os" / "isolated.py",
        '"""Zero fan-in, zero fan-out: an orphan, and unreachable."""\n',
    )
    _write(
        root / "graph_os" / "cluster_a.py",
        '"""Half of a mutually-importing, unreachable cluster."""\n\nimport graph_os.cluster_b\n',
    )
    _write(
        root / "graph_os" / "cluster_b.py",
        '"""Half of a mutually-importing, unreachable cluster."""\n\nimport graph_os.cluster_a\n',
    )


def _run(check: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), check, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _findings(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {
        line.strip().removeprefix("- ").strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("- ")
    }


def test_orphans_flags_only_the_isolated_module(tmp_path: Path) -> None:
    _build_fixture(tmp_path)

    result = _run("orphans", tmp_path)

    assert result.returncode == 1
    assert _findings(result) == {"graph_os.isolated"}


def test_unreachable_flags_isolated_and_the_mutual_cluster(tmp_path: Path) -> None:
    _build_fixture(tmp_path)

    result = _run("unreachable", tmp_path)

    assert result.returncode == 1
    assert _findings(result) == {
        "graph_os.isolated",
        "graph_os.cluster_a",
        "graph_os.cluster_b",
    }


def test_unreachable_follows_the_static_chain_the_function_local_import_and_the_dynamic_string(
    tmp_path: Path,
) -> None:
    _build_fixture(tmp_path)

    result = _run("unreachable", tmp_path)

    reached = {
        "graph_os.reached.leaf",
        "graph_os.func_local",
        "graph_os.dynamic_target",
    }
    assert reached.isdisjoint(_findings(result))


def test_orphans_on_a_clean_tree_is_zero_findings(tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    (tmp_path / "graph_os" / "isolated.py").unlink()
    (tmp_path / "graph_os" / "cluster_a.py").unlink()
    (tmp_path / "graph_os" / "cluster_b.py").unlink()

    result = _run("orphans", tmp_path)

    assert result.returncode == 0
    assert _findings(result) == set()


def test_unreachable_on_a_fully_wired_tree_is_zero_findings(tmp_path: Path) -> None:
    _build_fixture(tmp_path)
    (tmp_path / "graph_os" / "isolated.py").unlink()
    (tmp_path / "graph_os" / "cluster_a.py").unlink()
    (tmp_path / "graph_os" / "cluster_b.py").unlink()

    result = _run("unreachable", tmp_path)

    assert result.returncode == 0
    assert _findings(result) == set()
