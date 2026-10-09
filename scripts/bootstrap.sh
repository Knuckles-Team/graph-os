#!/usr/bin/env bash
# One-command contributor setup from a fresh clone (local or Claude Code on the web).
#
#   scripts/bootstrap.sh              uv, pinned Python, sibling sources, locked deps, git hooks
#   scripts/bootstrap.sh --kernel     also build epistemic-graph's numeric kernel (Rust, slow)
#   scripts/bootstrap.sh --scanners   also the pinned native scanners (cargo/npm, ~15 min cold)
#   scripts/bootstrap.sh --siblings-only
#                                     sibling sources + `uv sync` only; skips git
#                                     hooks/kernel/scanners. For a fresh `git worktree
#                                     add` lane that has no ../<repo> checkouts next to
#                                     it: symlinks each sibling from the canonical
#                                     checkout under /home/apps/workspace/agent-packages
#                                     (never writes into it) and falls back to cloning
#                                     origin/main when no local or canonical copy
#                                     exists. Fresh worktree: `scripts/bootstrap.sh
#                                     --siblings-only && uv run pytest tests/<file>`.
#
# Idempotent and non-interactive. Sibling sources are linked from ../<repo>
# checkouts when present (the public layout in
# graph_os/skills/graph-os-development/references/bootstrap.md), else from the
# canonical checkouts, else cloned from origin/main; without any of those the
# dependency sync is skipped and the hooks that need it report SKIPPED.
# Afterwards:
#   uvx pre-commit run --all-files
#   uvx pre-commit run pytest --hook-stage manual --all-files
set -euo pipefail

cd "$(dirname "$0")/.."

kernel=0
scanners=0
siblings_only=0
for arg in "$@"; do
  case "$arg" in
    --kernel) kernel=1 ;;
    --scanners) scanners=1 ;;
    --siblings-only) siblings_only=1 ;;
    -h | --help)
      sed -n '2,21p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done
if [[ "$siblings_only" == 1 ]]; then
  kernel=0
  scanners=0
fi

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# uv: releases before 0.9 cannot download current CPython patch releases.
uv_minor() { uv --version 2>/dev/null | awk '{split($2, v, "."); print v[1] * 1000 + v[2]}'; }
if ! command -v uv >/dev/null 2>&1 || [[ "$(uv_minor)" -lt 9 ]]; then
  echo "bootstrap: installing a current uv"
  # Fallbacks: an externally-managed (PEP 668) interpreter, then Astral's own
  # self-updater for a standalone uv. Never pipe a downloaded installer to sh.
  python3 -m pip install --quiet --user --upgrade "uv>=0.9" \
    || python3 -m pip install --quiet --user --upgrade --break-system-packages "uv>=0.9" \
    || uv self update
fi

# The one Python pin: the release workflow's setup-python version.
python_version="$(sed -n "s/^ *python-version: '\(.*\)'$/\1/p" .github/workflows/release.yml | head -n 1)"
echo "bootstrap: Python $python_version"
uv python install "$python_version"

# Link every [tool.uv.sources] path (here and, transitively, in each linked
# sibling) to a ../<repo> checkout when present, else to the canonical
# checkout under /home/apps/workspace/agent-packages (symlink only; that
# checkout is a live mount and is never written to), else clone origin/main.
# Never replaces an existing link or directory.
echo "bootstrap: sibling sources"
export GRAPH_OS_CANONICAL_SIBLINGS="${GRAPH_OS_CANONICAL_SIBLINGS:-/home/apps/workspace/agent-packages}"
python3 - <<'PY'
import os
import subprocess
import tomllib
from pathlib import Path

CANONICAL_ROOT = Path(os.environ["GRAPH_OS_CANONICAL_SIBLINGS"]).resolve()

def resolve_checkout(project: Path, name: str) -> Path | None:
    for candidate in (project.resolve().parent / name, CANONICAL_ROOT / name):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None

def link_sources(project: Path, depth: int = 0) -> None:
    pyproject = project / "pyproject.toml"
    if depth > 3 or not pyproject.is_file():
        return
    # A project resolved from the canonical read-only checkouts manages its
    # own siblings already; never write into it.
    if depth > 0 and CANONICAL_ROOT in project.resolve().parents:
        return
    sources = tomllib.loads(pyproject.read_text()).get("tool", {}).get("uv", {}).get("sources", {})
    for source in sources.values():
        path = source.get("path") if isinstance(source, dict) else None
        if not path or not path.startswith(".uv-workspace-siblings/"):
            continue
        target = project / path
        if target.exists() or target.is_symlink():
            link_sources(target, depth + 1)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        checkout = resolve_checkout(project, target.name)
        if checkout is not None:
            target.symlink_to(checkout)
            print(f"  linked {target} -> {checkout}")
        else:
            url = f"https://github.com/Knuckles-Team/{target.name}.git"
            print(f"  cloning {target.name} from {url} (origin/main; no local or canonical checkout)")
            subprocess.run(["git", "clone", "--depth", "1", url, str(target)], check=True)
        link_sources(target, depth + 1)

link_sources(Path.cwd())
PY

eg=.uv-workspace-siblings/agent-utilities/.uv-workspace-siblings/epistemic-graph
au_real="$(realpath -m .uv-workspace-siblings/agent-utilities 2>/dev/null || true)"
canonical_real="$(realpath -m "$GRAPH_OS_CANONICAL_SIBLINGS" 2>/dev/null || true)"
if [[ ! -e "$eg" && -e .uv-workspace-siblings/agent-utilities \
      && "$au_real" != "$canonical_real"/* ]]; then
  # agent-utilities overlays epistemic-graph from source (not a declared path source).
  mkdir -p "$(dirname "$eg")"
  if [[ -e ../epistemic-graph/pyproject.toml ]]; then
    ln -s "$(cd ../epistemic-graph && pwd)" "$eg"
    echo "  linked $eg -> ../epistemic-graph"
  elif [[ -e "${GRAPH_OS_CANONICAL_SIBLINGS}/epistemic-graph/pyproject.toml" ]]; then
    ln -s "${GRAPH_OS_CANONICAL_SIBLINGS}/epistemic-graph" "$eg"
    echo "  linked $eg -> ${GRAPH_OS_CANONICAL_SIBLINGS}/epistemic-graph"
  else
    echo "  cloning epistemic-graph from origin/main (no local or canonical checkout)"
    git clone --depth 1 "https://github.com/Knuckles-Team/epistemic-graph.git" "$eg"
  fi
fi

missing=0
while read -r sibling; do
  [[ -e "$sibling/pyproject.toml" ]] || { echo "  missing $sibling (clone it next to this checkout)"; missing=1; }
done < <(python3 -c 'import tomllib; [print(s["path"]) for s in tomllib.load(open("pyproject.toml", "rb"))["tool"]["uv"]["sources"].values() if "path" in s]')

if [[ "$missing" == 0 ]]; then
  echo "bootstrap: locked dependencies (epistemic-graph overlaid from source)"
  uv sync --frozen --python "$python_version" --extra test --extra webui --no-install-package epistemic-graph
else
  echo "SKIPPED (dependency sync): sibling sources are missing; the pytest and mypy-env hooks will report SKIPPED"
fi

if [[ "$kernel" == 1 ]]; then
  [[ -e "$eg/scripts/build_numeric_kernel.py" ]] || { echo "bootstrap: --kernel needs $eg" >&2; exit 2; }
  echo "bootstrap: epistemic-graph numeric kernel"
  export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-3}" CARGO_PROFILE_RELEASE_DEBUG=0
  uv run --no-project --python "$python_version" --with 'maturin>=1,<2' \
    python "$eg/scripts/build_numeric_kernel.py"
fi

if [[ "$siblings_only" == 1 ]]; then
  echo "bootstrap: --siblings-only, skipping git hooks (shared .git/hooks across worktrees)"
else
  echo "bootstrap: git hooks"
  uvx pre-commit install --hook-type pre-commit --hook-type pre-push
fi

if [[ "$scanners" == 1 ]]; then
  echo "bootstrap: native scanners (cargo/npm builds; ~15 min cold)"
  scanner_root="$HOME/.local/share/graph-os-scanners"
  bins="$(bash scripts/install_scanners.sh "$scanner_root")"
  echo "bootstrap: add the scanners to PATH:"
  echo "$bins" | sed 's/^/  export PATH="/; s/$/:$PATH"/'
fi

echo "bootstrap: done"
