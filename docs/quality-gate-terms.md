# Terms of Acceptance — the cccc and KISS gates

This repository has **no exceptions yet** — zero measured functions exist
over either gate's caps (see `AGENTS.md` "Status": the package is six
docstring-only placeholders plus a console entry point). This page exists
now, at scaffold size, so the rule about rules is written down before the
first real exception is ever needed, not invented under pressure later.

## The rule about rules

The project rule is **NO RATCHETS — expose tech debt**, exactly as stated in
epistemic-graph's and agent-utilities' own `docs/quality-gate-terms.md`
pages (read those for the full worked examples — the Rust exhaustive-dispatch
cyclomatic exemption on EG, the KISS threshold recalibration methodology on
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

`.kiss/kiss.toml`'s `[python]` table is **KISS 0.4.10's own shipped
defaults** (`kiss rules`, no `--config`), written down explicitly because
the tool requires an explicit `[python]` table the moment it scans any
Python file — not because any of them has been recalibrated. There is no
measured distribution to calibrate against yet (see "Status" in
AGENTS.md). When this repository has enough real code to measure one,
follow epistemic-graph's own KISS section as the worked methodology:
recalibrate a threshold only against a measured percentile, with the
measurement written down next to the number, never against "what currently
passes."

### Orphan-module rule — enforced

`kiss-census` enforces every KISS finding over the package, and with
`orphan_module_enabled = true` it also runs one whole-package orphan-module
pass — see AGENTS.md "Orphan-module wiring gate (Python)".

## Running the scanners

```bash
pre-commit run complexity-staged                        # cccc, on the staged diff
pre-commit run kiss-staged                              # KISS, on the staged diff
pre-commit run complexity-census --hook-stage manual --all-files
pre-commit run kiss-census --hook-stage manual --all-files    # whole package + orphan_module
pre-commit run dupehound-changed
pre-commit run jscpd-differential --hook-stage manual --all-files
pre-commit run jscpd-census --hook-stage manual --all-files
```

Never run bare `kiss check` — it writes the self-calibrating `.kissconfig`.
