#!/usr/bin/env bash
# Install the pinned native scanners the shared quality hooks verify (the
# versions the pinned Knuckles-Team/pipelines scanner-versions hook expects).
# Used by the release workflow's scanner job and by scripts/bootstrap.sh
# --scanners, so both provision the same versions.
#
#   scripts/install_scanners.sh <root>
#
# Each scanner goes to its own prefix under <root>; the bin directories are
# printed one per line (append them to PATH / $GITHUB_PATH). Idempotent:
# cargo install skips installed crates; the provider revalidates cached jscpd.
set -euo pipefail

root="${1:?usage: scripts/install_scanners.sh <root>}"
mkdir -p "$root"

# cccc 1.6.0 is not published to crates.io (cccc-cli stops at 1.0.0);
# install the upstream v1.6.0 tag by its immutable commit.
cargo install --quiet --locked --git https://github.com/moznion/cccc \
  --rev d728759323be5d9977b7390a27133e8eaf481f26 --root "$root/cccc" cccc-cli >&2
# kiss: the fleet's own fork build (upstream 0.4.12 + the inline-module
# resolution fix); crates.io 0.4.12 aborts the census (see
# pipelines_hooks/core/kiss_fork.py in Knuckles-Team/pipelines).
cargo install --quiet --locked --git https://github.com/Knucklessg1/kiss \
  --rev 7f1c6785697d3fe9a41ceb8b8e5d0f615fb1f3d9 --root "$root/kiss" kiss-ai >&2
cargo install --quiet --locked --version 0.1.2 --root "$root/dupehound" dupehound >&2
# The shared provider owns jscpd's source, patch, compiler and receipt checks.
# The source installer is bound to the reviewed immutable provider. Shared
# hook references keep their separately required main convention.
provider="$(mktemp -d "$root/pipelines-provider.XXXXXX")"
trap 'rm -rf -- "$provider"' EXIT
provider_git() (
  # Hooks can export repository selectors; never let them redirect this clone.
  for git_name in ${!GIT_@}; do unset "$git_name"; done
  git "$@"
)
provider_commit="c0a089c83eea9d0d08f48e6c00681eb989268d9a"
provider_git -C "$provider" init --quiet
provider_git -C "$provider" fetch --depth 1 \
  https://github.com/Knuckles-Team/pipelines.git "$provider_commit" >&2
provider_git -C "$provider" checkout --quiet --detach FETCH_HEAD
provider_revision="$(provider_git -C "$provider" rev-parse HEAD)"
if [[ "$provider_revision" != "$provider_commit" ]]; then
  echo "Scanner provider revision mismatch" >&2
  exit 1
fi
printf 'Scanner provider revision: %s\n' "$provider_revision" >&2
toolchain="$(python3 - "$provider" <<'PYTHON'
import sys
sys.path.insert(0, sys.argv[1])
from pipelines_hooks.core.jscpd_build import RUST_TOOLCHAIN
print(RUST_TOOLCHAIN)
PYTHON
)"
rustup toolchain install "$toolchain" --profile minimal >&2
jscpd_bin_dir="$(python3 "$provider/scripts/install_jscpd.py" --root "$root/jscpd")"

for bin_dir in cccc/bin kiss/bin dupehound/bin; do
  echo "$root/$bin_dir"
done
echo "$jscpd_bin_dir"
