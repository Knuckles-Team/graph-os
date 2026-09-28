#!/usr/bin/env bash
# Repository-local pre-commit hooks that need prerequisites a fresh clone may
# lack (the sibling source checkouts under .uv-workspace-siblings/ and the
# synced test environment that scripts/bootstrap.sh provisions).
#
#   scripts/local_hook.sh pytest [pytest args...]   full test suite
#   scripts/local_hook.sh dependency-readiness      release-tag index readiness
#
# A missing prerequisite prints "SKIPPED (<gate>): <reason>" and exits 0
# locally; with $CI set it prints "<gate>: CANNOT RUN: <reason>" and exits 2,
# so hosted CI never passes a gate that did not run.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

gate="${1:-}"
shift || true

unavailable() {
  case "${CI:-}" in
    "" | 0 | false | no)
      echo "SKIPPED ($gate): $1; run ${2:-scripts/bootstrap.sh}" >&2
      exit 0
      ;;
    *)
      echo "$gate: CANNOT RUN: $1" >&2
      exit 2
      ;;
  esac
}

# The path sources pyproject.toml declares under [tool.uv.sources].
declared_siblings() {
  python3 - <<'PY'
import tomllib
sources = tomllib.load(open("pyproject.toml", "rb"))["tool"]["uv"]["sources"]
for source in sources.values():
    if "path" in source:
        print(source["path"])
PY
}

case "$gate" in
  pytest)
    while read -r sibling; do
      [ -e "$sibling/pyproject.toml" ] || unavailable "sibling checkout $sibling is missing"
    done < <(declared_siblings)
    [ -x .venv/bin/python ] || unavailable "the locked test environment (.venv) is not synced"
    # epistemic-graph is overlaid from source (uv sync --no-install-package
    # epistemic-graph), exactly as the release workflow does.
    if ! .venv/bin/python -c "import epistemic_graph" 2>/dev/null; then
      eg=.uv-workspace-siblings/agent-utilities/.uv-workspace-siblings/epistemic-graph
      [ -e "$eg/epistemic_graph/__init__.py" ] || unavailable "epistemic-graph source $eg is missing"
      PYTHONPATH="$(cd "$eg" && pwd)${PYTHONPATH:+:$PYTHONPATH}"
      export PYTHONPATH
    fi
    .venv/bin/python -c "import epistemic_graph.numeric" 2>/dev/null \
      || unavailable "the epistemic-graph numeric kernel is not built" "scripts/bootstrap.sh --kernel"
    exec uv run --no-sync python -m pytest "$@"
    ;;
  dependency-readiness)
    source=""
    for candidate in .uv-workspace-siblings/repository-manager ../repository-manager; do
      if [ -e "$candidate/pyproject.toml" ]; then
        source="$candidate"
        break
      fi
    done
    [ -n "$source" ] || unavailable "no repository-manager checkout at .uv-workspace-siblings/repository-manager or ../repository-manager"
    exec uv run --no-project --python 3.12 --prerelease=allow --with "$source" \
      python -m repository_manager.dependency_readiness .
    ;;
  *)
    echo "usage: scripts/local_hook.sh {pytest|dependency-readiness} [args...]" >&2
    exit 2
    ;;
esac
