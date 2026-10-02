"""Implementation package behind ``scripts/check_wiring.py``.

Split out of the single-file script (see that module's docstring) to respect
this repository's own per-file function/line caps: ``discovery`` (shared
module universe and declared roots), ``static_imports`` (the orphan check's
unchanged edge definition), ``dynamic_imports`` (name-based edges the
reachability report also follows), ``orphans`` (the blocking isolated-module
check), and ``reachability`` (the manual-stage, non-blocking report).
"""

from __future__ import annotations
