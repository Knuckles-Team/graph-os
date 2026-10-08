# Terms of Acceptance — the cccc and KISS gates

This repository has **no accepted complexity exceptions**. Every measured
Python function is subject to the configured cccc and KISS limits. Any later
exception must be a documented rule about a defensible class of code, never a
file allowlist, frozen count, or inline suppression.

## The rule about rules

The project rule is **NO RATCHETS — expose tech debt**, exactly as stated in
epistemic-graph's and agent-utilities' own `docs/quality-gate-terms.md`
pages (read those for the full worked examples — the Rust exhaustive-dispatch
cyclomatic exemption on EG, the KISS threshold recalibration method on
both). The test is mechanical, and identical here:

* An exception is a **RULE about a class of code**, with a stated reason and
  the measurement behind it — recomputed from the source under measurement
  on every run.
* There is **no list of accepted files**, **no list of accepted functions**,
  **no frozen count**, **no `--update-baseline`**, and **no in-line
  suppression comment**.
* A finding that is accepted is still **counted and printed**.
* `.kissconfig` must never exist. Bare `kiss check` writes one,
  self-calibrated from what the repo currently passes, and silently
  disables four global rules. The shared KISS hooks fail if the file is
  present.

## CCCC — the shared terms of acceptance

Caps are cyclomatic 10 and cognitive 15, enforced on the diff by the shared
`complexity-staged` hook and over the whole package by `complexity-census`
(both from Knuckles-Team/pipelines).

The shared hooks carry exactly one rule about a class of code, ported from
epistemic-graph: a Rust exhaustive `match` (no catch-all arm, residual
cyclomatic complexity within the cap) may exceed the cyclomatic cap, never the
cognitive cap. It can only apply to `.rs` files, and this repository has none,
so every function here is judged on the caps alone, with no discount.

If a genuinely irreducible complexity case ever arises here (some class of
Python code where the cyclomatic/cognitive caps measure the wrong thing —
the same shape of argument EG makes for Rust `match` dispatch), argue for a
documented rule about that CLASS of code, with the measurement attached, add
it to the shared hook and to this page. Never raise a threshold, never
suppress inline.

## KISS — this repository's own thresholds

`.config/kiss.toml`'s `[python]` table records the enforced KISS limits (kiss
0.4.12, the fleet's own fork build). Recalibrate a threshold only from a
reviewed measurement that explains why the metric misrepresents a class of
code. Record the method beside the value; never tune a threshold merely
to make the current tree pass.

### Orphan-module rule — enforced

`kiss-census` enforces every KISS finding over the package. Orphan modules are
no longer part of that pass: kiss 0.4.11 moved orphan detection to the
coverage-linked `kiss test`, a much heavier and differently-scoped operation
than this repository's wiring gate needs. The `check-orphan-modules`
pre-commit hook (`scripts/check_wiring.py orphans`) enforces it instead,
restoring the original structural semantics — see AGENTS.md "Orphan-module
wiring gate (Python)".

### Reachability report — informational, manual stage only

A module with nonzero fan-in/fan-out passes the orphan check above even when
no running process ever reaches it (it only imports, and is imported by,
other equally unserved modules). `scripts/check_wiring.py unreachable` (the
`check-unreachable-modules` pre-commit hook) reports every `graph_os` module
that walking every import edge — static and dynamic/name-based — from the
declared roots (`pyproject.toml`'s `[project.scripts]` / `[project.entry-points]`
and the top-level package) never reaches. It runs at the `manual` stage
only, so it never blocks `pre-commit` or `pre-push` and CI's blocking gate
set is unchanged; it exists to make staged, not-yet-served code visible and
trackable, not to force an immediate fix. See AGENTS.md "Reachability report
(Python) — staged, not-yet-served code".

## Running the scanners

```bash
pre-commit run complexity-staged                        # cccc, on the staged diff
pre-commit run kiss-staged                              # KISS, on the staged diff
pre-commit run complexity-census --hook-stage manual --all-files
pre-commit run kiss-census --hook-stage manual --all-files    # whole package
pre-commit run check-orphan-modules --all-files                # Python wiring gate
pre-commit run check-unreachable-modules --hook-stage manual --all-files  # reachability report
pre-commit run dupehound-changed
pre-commit run jscpd-differential --hook-stage manual --all-files
pre-commit run jscpd-census --hook-stage manual --all-files
```

Never run bare `kiss check` — it writes the self-calibrating `.kissconfig`.
