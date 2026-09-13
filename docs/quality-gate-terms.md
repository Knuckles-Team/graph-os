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
  disables four global rules. The `kiss-census` hook fails if the file is
  present.

## CCCC — no exemption here (yet)

Caps are `scanner_contract.CCCC_MAX_CYCLOMATIC = 10` and
`CCCC_MAX_COGNITIVE = 15`, enforced on the diff by `complexity-staged` and
reported over the whole tree by `cccc-census`.

epistemic-graph carries a Rust-only exhaustive-dispatch exemption for the
cyclomatic cap (`scripts/rust_exhaustive_match.py`) that this repository
deliberately does **not** port — see `check_complexity_staged.py`'s module
docstring for why (zero `.rs` files here; the exemption would be
permanently-dead machinery). Every function in this repository is judged on
the caps alone, with no discount.

If a genuinely irreducible complexity case ever arises here (some class of
Python code where the cyclomatic/cognitive caps measure the wrong thing —
the same shape of argument EG makes for Rust `match` dispatch), the fix is
the same one `check_complexity_staged.py`'s own advice text names: argue for
a documented rule about that CLASS of code, with the measurement attached,
and add it to this page. Never raise a threshold, never suppress inline.

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

### Orphan-module rule — enforced, not advisory

Unlike every other KISS finding `kiss-census` reports (which are advisory),
`orphan_module` is a hard failure here — see AGENTS.md "Orphan-module wiring
gate (Python)" for the full rationale and the plant-and-fire proof.

## Running the scanners

```bash
pre-commit run complexity-staged --all-files        # cccc, on the diff
pre-commit run kiss-changed-python --all-files       # KISS, on the diff
pre-commit run cccc-census --hook-stage manual        # whole tree
pre-commit run kiss-census --hook-stage manual        # whole tree + orphan_module
pre-commit run dupehound-changed-functions --all-files
pre-commit run jscpd-differential --hook-stage manual
pre-commit run jscpd-census --hook-stage manual
```

Never run bare `kiss check` — it writes the self-calibrating `.kissconfig`.
Always pass `--config .kiss/kiss.toml`; a directory target is required for
the `orphan_module` rule to see the whole package's import graph (a
single-file target reports every other rule normally but never
`orphan_module` — verified empirically, see AGENTS.md).
