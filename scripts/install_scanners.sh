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
# cargo install and npm install skip what is already present.
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
npm install --prefix "$root/npm" --no-package-lock --ignore-scripts --no-save \
  --no-audit --no-fund "jscpd@5.0.16" >&2

for bin_dir in cccc/bin kiss/bin dupehound/bin npm/node_modules/.bin; do
  echo "$root/$bin_dir"
done
