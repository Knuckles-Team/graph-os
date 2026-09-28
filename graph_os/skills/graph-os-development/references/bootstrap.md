# Public source bootstrap

This recipe mirrors the editable paths in the six repositories' `pyproject.toml`
files and Graph OS's public `.github/workflows/release.yml`. Use Python 3.12,
`uv`, Rust/Cargo for engine work, and the Node/pnpm versions required by WebUI.
All source URLs below are public GitHub HTTPS URLs.

Run from a new directory of your choice:

```bash
mkdir graphos-src
cd graphos-src
for repo in epistemic-graph agent-connector-sdk agent-utilities graph-os agent-webui; do
  git clone "https://github.com/Knuckles-Team/${repo}.git"
done

mkdir -p agent-utilities/.uv-workspace-siblings graph-os/.uv-workspace-siblings agent-webui/.uv-workspace-siblings
ln -s ../../epistemic-graph agent-utilities/.uv-workspace-siblings/epistemic-graph
ln -s ../../agent-connector-sdk agent-utilities/.uv-workspace-siblings/agent-connector-sdk
ln -s ../../agent-utilities graph-os/.uv-workspace-siblings/agent-utilities
ln -s ../../agent-connector-sdk graph-os/.uv-workspace-siblings/agent-connector-sdk
ln -s ../../agent-webui graph-os/.uv-workspace-siblings/agent-webui
ln -s ../../agent-utilities agent-webui/.uv-workspace-siblings/agent-utilities
```

For repository-manager work, also run:

```bash
git clone https://github.com/Knuckles-Team/repository-manager.git
mkdir -p repository-manager/.uv-workspace-siblings
ln -s ../../agent-utilities repository-manager/.uv-workspace-siblings/agent-utilities
```

These symlinks are ignored local source bindings, not files to commit. If one
already exists, inspect where it points rather than replacing it blindly.
The Graph OS release workflow pins exact sibling commits; check its current
`actions/checkout` `ref` values and align the clones to those revisions when
reproducing CI or a specific PR. Using all current `main` branches is useful
for integration development, but may not match the frozen `uv.lock` or a
published package version. Do not resolve around a mismatch by silently
substituting an older index wheel. Report the incompatible revision pair and
update the intended lock/source contract in the owning repository.

Graph OS's source test setup follows `.github/workflows/release.yml`: it builds
the local EG numeric kernel, sets `PYTHONPATH` to that EG checkout, and runs
`uv sync --frozen --extra test --extra webui --no-install-package epistemic-graph`
before tests. From `graphos-src/graph-os`, the current local equivalent is:

```bash
python -m pip install 'maturin>=1,<2'
python ../epistemic-graph/scripts/build_numeric_kernel.py
PYTHONPATH="$(realpath ../epistemic-graph)" uv sync --frozen --extra test --extra webui --no-install-package epistemic-graph
PYTHONPATH="$(realpath ../epistemic-graph)" uv run --no-sync pytest
```

The `--no-install-package` flag is intentional: current EG source can be ahead
of published wheel metadata. Use the workflow's current commands and exact
pinned refs rather than treating a local `uv sync --extra test` against the
public index as CI parity. AU's release workflow likewise checks out a pinned
SDK and skips EG wheel installation. Its supported test environment uses
`uv sync --frozen --extra test --group guardrails --no-install-package
epistemic-graph --no-install-package langfuse-agent`, then the tests in its
workflow. The SDK's public workflow runs
`scripts/bootstrap_epistemic_graph_contract.sh`,
`uv sync --frozen --no-install-package epistemic-graph`, and
`uv run --frozen python -m pytest -q` with `PYTHONPATH=.ci/epistemic-graph`.
For direct engine work, its `CONTRIBUTING.md` gives the Cargo, Python, and hook
commands. WebUI and repository-manager have their own `AGENTS.md` bootstrap
and test commands; use their declared local source bindings above when a
published dependency lags.

For one repository's focused work, use its `AGENTS.md` commands and test
fixtures. A normal PR should be provable with local/generated fixtures and
disposable dependencies provisioned by its workflow. A release tag, production
deployment, or credentialed integration has separate prerequisites; record that
boundary in the test evidence. No private service is needed to read or author
the repository-owned specs.
