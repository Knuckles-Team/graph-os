#!/usr/bin/env bash
# One-command contributor setup from a fresh clone (local or Claude Code on the web).
#
#   scripts/bootstrap.sh              uv, pinned Python, sibling sources, locked deps, git hooks
#   scripts/bootstrap.sh --kernel     also build epistemic-graph's numeric kernel (Rust, slow)
#   scripts/bootstrap.sh --scanners   also the pinned native scanners (cargo/npm, ~15 min cold)
#
# Idempotent and non-interactive. Sibling sources are linked from ../<repo>
# checkouts when present (the public layout in
# graph_os/skills/graph-os-development/references/bootstrap.md); without them
# the dependency sync is skipped and the hooks that need it report SKIPPED.
# Afterwards:
#   uvx pre-commit run --all-files
#   uvx pre-commit run pytest --hook-stage manual --all-files
set -euo pipefail

cd "$(dirname "$0")/.."

kernel=0
scanners=0
for arg in "$@"; do
  case "$arg" in
    --kernel) kernel=1 ;;
    --scanners) scanners=1 ;;
    -h | --help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

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
# sibling) to a ../<repo> checkout when it is missing. Never replaces a link.
echo "bootstrap: sibling sources"
python3 - <<'PY'
import tomllib
from pathlib import Path

def link_sources(project: Path, depth: int = 0) -> None:
    pyproject = project / "pyproject.toml"
    if depth > 3 or not pyproject.is_file():
        return
    sources = tomllib.loads(pyproject.read_text()).get("tool", {}).get("uv", {}).get("sources", {})
    for source in sources.values():
        path = source.get("path") if isinstance(source, dict) else None
        if not path or not path.startswith(".uv-workspace-siblings/"):
            continue
        target = project / path
        checkout = Path.cwd().resolve().parent / target.name
        if not target.exists() and not target.is_symlink() and (checkout / "pyproject.toml").is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(checkout)
            print(f"  linked {target} -> {checkout}")
        link_sources(target, depth + 1)

link_sources(Path.cwd())
PY

eg=.uv-workspace-siblings/agent-utilities/.uv-workspace-siblings/epistemic-graph
if [[ ! -e "$eg" && -e .uv-workspace-siblings/agent-utilities && -e ../epistemic-graph/pyproject.toml ]]; then
  # agent-utilities overlays epistemic-graph from source (not a declared path source).
  mkdir -p "$(dirname "$eg")"
  ln -s "$(cd ../epistemic-graph && pwd)" "$eg"
  echo "  linked $eg -> ../epistemic-graph"
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

echo "bootstrap: git hooks"
uvx pre-commit install --hook-type pre-commit --hook-type pre-push

if [[ "$scanners" == 1 ]]; then
  echo "bootstrap: native scanners (cargo/npm builds; ~15 min cold)"
  scanner_root="$HOME/.local/share/graph-os-scanners"
  bins="$(bash scripts/install_scanners.sh "$scanner_root")"
  echo "bootstrap: add the scanners to PATH:"
  echo "$bins" | sed 's/^/  export PATH="/; s/$/:$PATH"/'
fi

echo "bootstrap: done"
