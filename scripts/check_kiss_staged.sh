#!/usr/bin/env bash
# Diff-scoped KISS gate for graph-os.
#
# Adapted from epistemic-graph's `scripts/check_kiss_staged.sh` on branch
# `refactor/eg-f56-registry-kiss` (commit 55835df3, lane F6 — the
# diff-scoped successor to EG main's whole-file `check_kiss_staged.sh`;
# see kiss_diff_scope.py's header for why THAT behaviour was adopted here).
# Substantial simplification from the Rust source, all necessary rather than
# cosmetic:
#   - `--lang python`, not `--lang rust`.
#   - Path selection matches `list_scanner_sources.py`'s `_is_kiss_source`:
#     tracked `*.py` under `graph_os/`, not `src/*.rs|crates/*.rs`.
#   - The Rust source's "complete compiler-declared module closure"
#     validation (`rust_module_tree.py`) is DROPPED: it exists because a
#     Rust `mod` declaration can require sibling files to resolve, so KISS
#     needed the whole closure materialized before checking any one of them.
#     Python has no such compile-time module-closure requirement for KISS's
#     per-file/per-function rules — each `.py` file is independently
#     checkable. Nothing this repository has needs that machinery yet.
#
# KISS has two dangerous defaults: it can write a self-calibrated
# .kissconfig, and its config only becomes authoritative when --config is
# supplied. It also reports a false green when multiple paths are passed to
# one `check` invocation. This wrapper resolves only an already-installed,
# pinned binary, checks one staged source file per invocation, and never
# writes a baseline or config.
#
# Exit 0 = clean/no applicable source (or every finding was pre-existing and
# untouched), 1 = attributable KISS findings, 2 = cannot run.
set -uo pipefail

# A real git hook inherits repository-selector variables from git itself. The
# helper strips every GIT_* selector dynamically, keeping GIT_INDEX_FILE
# deliberately: pre-commit points it at the staged index this gate must check.
sanitized_env_args() {
  local key snapshot
  snapshot="$(env)" || return 1
  while IFS='=' read -r key _; do
    case "$key" in
      GIT_INDEX_FILE|'' ) ;;
      GIT_* ) printf '%s\n' "-u" "$key" ;;
    esac
  done <<< "$snapshot"
}

git_cmd() {
  local args=()
  local arg sanitized
  sanitized="$(sanitized_env_args)" || return 1
  while IFS= read -r arg; do
    [ -n "$arg" ] && args+=("$arg")
  done <<< "$sanitized"
  env "${args[@]}" git "$@"
}

scanner_cmd() {
  local args=()
  local arg sanitized
  sanitized="$(sanitized_env_args)" || return 1
  while IFS= read -r arg; do
    [ -n "$arg" ] && args+=("$arg")
  done <<< "$sanitized"
  env "${args[@]}" "$@"
}

die() {
  echo "kiss(staged): CANNOT RUN: $*" >&2
  exit 2
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)" || \
  die "could not resolve the hook directory"
ROOT="$(cd -- "$SCRIPT_DIR/.." 2>/dev/null && pwd -P)" || \
  die "could not resolve the repository root"
GIT_ROOT="$(git_cmd -C "$ROOT" rev-parse --show-toplevel 2>/dev/null)" || \
  die "not inside a git work tree"
[ "$GIT_ROOT" = "$ROOT" ] || die "Git resolved a different work tree"
cd "$ROOT" || die "could not enter repository root"

SCRATCH_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/graphos-kiss-staged.XXXXXX")" || \
  die "could not create staged-source directory"
STAGED_ROOT="$SCRATCH_ROOT/index"
HEAD_ROOT="$SCRATCH_ROOT/head"
CHANGED_PATHS="$SCRATCH_ROOT/changed-paths"
mkdir -- "$STAGED_ROOT" || die "could not create staged-index directory"
mkdir -- "$HEAD_ROOT" || die "could not create head-tree directory"
cleanup() {
  rm -rf -- "$SCRATCH_ROOT"
}
trap cleanup EXIT HUP INT TERM

# Materialize the complete HEAD tree once (empty tree for the first-ever
# commit, when HEAD does not resolve yet) so a finding can be compared
# against what HEAD actually contained.
HAVE_HEAD=0
if git_cmd rev-parse --verify -q HEAD >/dev/null 2>&1; then
  git_cmd archive HEAD | tar -x -C "$HEAD_ROOT" 2>/dev/null || \
    die "could not materialize the HEAD tree"
  HAVE_HEAD=1
fi

# Pre-commit normally exports GIT_INDEX_FILE. Read only that index, and keep
# path records NUL-delimited because newlines are legal Git pathname bytes.
git_cmd diff --cached --name-only --diff-filter=ACMR -z > "$CHANGED_PATHS" \
  2>/dev/null || die "git diff --cached failed"

# Matches list_scanner_sources.py's `_is_kiss_source`: tracked *.py under
# graph_os/, excluding vendor/build/cache junk (none of which exists under
# graph_os/ today, but kept for parity with the census predicate).
files=()
while IFS= read -r -d '' path; do
  case "$path" in
    graph_os/*.py|graph_os/*/*.py)
      case "/$path" in
        */__pycache__/*|*/build/*|*/dist/*|*/generated/*|*/vendor/*|*/third_party/*|*/fixtures/*|*/fixture/*)
          continue
          ;;
      esac
      files+=("$path")
      ;;
  esac
done < "$CHANGED_PATHS"

if [ "${#files[@]}" -eq 0 ]; then
  echo "kiss(staged): OK: no staged Python source under graph_os/"
  exit 0
fi

# Materialize the complete staged index once.
git_cmd checkout-index --all --prefix="$STAGED_ROOT/" 2>/dev/null || \
  die "could not materialize the staged index"

for policy_input in pyproject.toml scripts/scanner_contract.py scripts/kiss_diff_scope.py; do
  staged_policy="$STAGED_ROOT/$policy_input"
  [ -f "$staged_policy" ] && [ ! -L "$staged_policy" ] || die \
    "staged policy input is missing or not a regular file: $policy_input"
done

read_contract() {
  command -v python3 >/dev/null 2>&1 || die "python3 is required to read pyproject.toml"
  scanner_cmd python3 -I - "$STAGED_ROOT" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "scripts"))

try:
    from scanner_contract import load_contract
    value = load_contract(root / "pyproject.toml").kiss_version
except Exception as exc:
    print(f"invalid [tool.graph_os.scanners] KISS contract: {exc}", file=sys.stderr)
    raise SystemExit(2)
print(value.strip())
PY
}

VERSION="$(read_contract)" || die "could not load the pinned KISS version"
CFG="$STAGED_ROOT/.kiss/kiss.toml"
KISS="${KISS_BIN:-}"
if [ -z "$KISS" ]; then
  if [ -n "${HOME:-}" ] && [ -x "$HOME/.local/bin/kiss" ]; then
    KISS="$HOME/.local/bin/kiss"
  elif [ -x /usr/local/bin/kiss ]; then
    KISS=/usr/local/bin/kiss
  else
    KISS="$(command -v kiss 2>/dev/null || true)"
  fi
fi
[ -n "$KISS" ] && [ -x "$KISS" ] || die \
  "kiss is not installed. Install the pinned $VERSION binary before running this hook; the hook never downloads it."

GOT="$(scanner_cmd "$KISS" --version 2>/dev/null)" || die "kiss --version failed"
[ "$GOT" = "kiss $VERSION" ] || die \
  "version drift: expected 'kiss $VERSION', got '$GOT'"
[ -f "$CFG" ] && [ ! -L "$CFG" ] || die \
  "missing staged .kiss/kiss.toml (hand-authored KISS thresholds)"
[ ! -e "$STAGED_ROOT/.kissconfig" ] && [ ! -L "$STAGED_ROOT/.kissconfig" ] || die \
  "staged .kissconfig exists; remove it because bare kiss check self-calibrates and disables rules"

rc=0
total=0
for path in "${files[@]}"; do
  # Never pass more than one path to KISS. KISS 0.4.10's multi-path check
  # prints NO VIOLATIONS and exits 0 even when either input has violations.
  staged_path="$STAGED_ROOT/$path"
  [ -f "$staged_path" ] && [ ! -L "$staged_path" ] || die \
    "staged source is missing or not a regular file: $path"

  output="$(cd "$STAGED_ROOT" && \
    scanner_cmd "$KISS" check --config "$CFG" --lang python "$path" 2>&1)"
  status=$?
  if grep -q "Unknown config key" <<< "$output"; then
    printf '%s\n' "$output" >&2
    die "KISS rejected a config key and fell back to upstream defaults"
  fi
  if [ "$status" -ne 0 ] && [ "$status" -ne 1 ]; then
    printf '%s\n' "$output" >&2
    die "KISS failed on $path with exit $status"
  fi
  [ -n "$output" ] || die "KISS returned no report for $path"
  count="$(grep -c '^VIOLATION:' <<< "$output" || true)"
  if [ "$status" -eq 0 ] && ! grep -q "NO VIOLATIONS" <<< "$output"; then
    printf '%s\n' "$output" >&2
    die "KISS returned exit 0 without a clean report for $path"
  fi
  if [ "$status" -eq 1 ] && grep -q "NO VIOLATIONS" <<< "$output"; then
    printf '%s\n' "$output" >&2
    die "KISS returned findings status with a clean report for $path"
  fi
  if { [ "$status" -eq 0 ] && [ "$count" -ne 0 ]; } || \
    { [ "$status" -eq 1 ] && [ "$count" -eq 0 ]; }; then
    printf '%s\n' "$output" >&2
    die "KISS exit status and violation report disagree for $path"
  fi

  # Diff-scoping (kiss_diff_scope.py): re-run KISS on the HEAD blob of the
  # same file (when one exists) and keep only the findings attributable to
  # this diff -- a NEW or MODIFIED function/item, or a file-level/whole-type
  # count this diff newly crosses or worsens.
  attributable_count="$count"
  attributable_output="$output"
  if [ "$count" -gt 0 ]; then
    head_file="$HEAD_ROOT/$path"
    head_args=()
    if [ "$HAVE_HEAD" -eq 1 ] && [ -f "$head_file" ] && [ ! -L "$head_file" ]; then
      head_output="$(cd "$HEAD_ROOT" && \
        scanner_cmd "$KISS" check --config "$CFG" --lang python "$path" 2>&1)"
      head_status=$?
      if [ "$head_status" -ne 0 ] && [ "$head_status" -ne 1 ]; then
        printf '%s\n' "$head_output" >&2
        die "KISS failed on the HEAD version of $path with exit $head_status"
      fi
      printf '%s' "$head_output" > "$SCRATCH_ROOT/head-report"
      head_args=(--head-source "$head_file" --head-report "$SCRATCH_ROOT/head-report")
    fi
    printf '%s' "$output" > "$SCRATCH_ROOT/staged-report"
    attributable_output="$(scanner_cmd python3 -I "$STAGED_ROOT/scripts/kiss_diff_scope.py" \
      --staged-source "$staged_path" --staged-report "$SCRATCH_ROOT/staged-report" \
      "${head_args[@]}")" || die "kiss_diff_scope.py failed for $path"
    attributable_count="$(grep -c '^VIOLATION:' <<< "$attributable_output" || true)"
    [ -n "$attributable_output" ] || attributable_count=0
  fi
  total=$((total + attributable_count))
  if [ "$attributable_count" -eq "$count" ]; then
    printf 'kiss(staged): %s violation(s) in %s\n' "$attributable_count" "$path"
  else
    printf 'kiss(staged): %s attributable violation(s) in %s (%s pre-existing, untouched by this change, not counted)\n' \
      "$attributable_count" "$path" "$((count - attributable_count))"
  fi
  if [ "$attributable_count" -ne 0 ]; then
    printf '%s\n' "$attributable_output"
    rc=1
  fi
done

printf 'kiss(staged): %s attributable violation(s) across %s changed file(s)\n' "$total" "${#files[@]}"
exit "$rc"
