#!/usr/bin/env python3
"""The reviewed register of function pairs dupehound reports that are NOT clones.

Copied verbatim from epistemic-graph's `scripts/dupehound_ledger.py`
(commit ceec541457209235ce3d0e0526506bf2428dc845), with one thing worth
flagging rather than silently carrying: `normalized_function_text`'s
declaration regex (`\\bfn\\s+{name}\\b`) matches Rust's `fn` keyword, not
Python's `def`. It is DORMANT here, not broken: this repository ships
`dupehound-distinct.toml` EMPTY (no reviewed pairs yet — see that file), so
`load_register()` returns `[]` and neither this regex nor
`function_exists`/`delegates_to` is ever exercised. The day a genuine
reviewed-distinct pair needs recording for THIS repository's Python source,
this regex must be generalized (or dual-matched) for `def`/`async def`
before that entry can be trusted — do that then, with a real pair to test
against, rather than guessing at the fix now with nothing to verify it.

Dupehound answers a structural question: after identifiers and literals are
normalized away, does one function have the same shape as another?  That is
the right question for finding copy-paste, and it is why this repository
runs it fail-closed with no baseline.  But shape is not meaning, and a small
number of pairs can be structurally identical while being semantically
unrelated.

Those cannot be "fixed" in the source, and silently lowering the scanner's
sensitivity would hide real clones.  So they are RECORDED here instead, and
the distinction between this and a baseline matters:

* A baseline is machine-written, unexplained, and grows by default.  It
  converts "we have debt" into "we have no debt", which is the ratchet this
  repository's own rules forbid.
* This register is HAND-WRITTEN, carries a stated reason per entry, and is
  ROT-DETECTING: every entry pins the normalized text of BOTH functions.
  Change either one and its entry stops matching, the finding comes back,
  and a human re-reviews it.  An entry that matches no current finding is
  reported as stale and must be deleted.  Nothing here updates itself.

An entry is therefore a claim with an expiry, not a suppression.

Normalization for the pinned digest collapses runs of whitespace, so
reformatting a signature does not force a re-review, while any token change
does.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
REGISTER_PATH = ROOT / "dupehound-distinct.toml"


class LedgerError(ValueError):
    """The register is absent, malformed, or disagrees with the source tree."""


@dataclass(frozen=True)
class DistinctPair:
    left_file: str
    left_name: str
    left_digest: str
    right_file: str
    right_name: str
    right_digest: str
    reason: str
    reviewed_on: str

    def key(self) -> tuple[str, str, str, str]:
        return (self.left_file, self.left_name, self.right_file, self.right_name)


def _find_declaration_start(
    lines: list[str], line: int, declaration: re.Pattern[str]
) -> int | None:
    """Anchor on the DECLARATION, not the reported line.

    Dupehound reports the `original_*` position against the HEAD blob, so on
    a branch that has since edited the file those numbers no longer address
    the worktree -- the line is a hint, never the identity. Prefer a hit near
    the hint, then fall back to the whole file, so the pin follows the
    function rather than the offset.
    """
    for candidate in range(max(0, line - 3), min(len(lines), line + 3)):
        if declaration.search(lines[candidate]):
            return candidate
    for candidate, text in enumerate(lines):
        if declaration.search(text):
            return candidate
    return None


def _collect_brace_balanced_block(lines: list[str], start: int) -> str:
    """Join lines from `start` until the braces opened on/after it balance."""
    depth = 0
    seen_brace = False
    collected: list[str] = []
    for current in range(start, len(lines)):
        text = lines[current]
        collected.append(text)
        for char in text:
            if char == "{":
                depth += 1
                seen_brace = True
            elif char == "}":
                depth -= 1
        if seen_brace and depth <= 0:
            break
    return re.sub(r"\s+", " ", "\n".join(collected)).strip()


def normalized_function_text(path: Path, line: int, name: str) -> str | None:
    """Return the brace-balanced source of `name` at (or just after) `line`.

    Dupehound reports a 1-indexed line for the function it matched.  The
    signature may wrap, so the opening brace is found by scanning forward
    rather than assumed to be on that line.  Returns `None` when the named
    function is not there, which the caller treats as a stale register entry
    rather than a silent pass.
    """

    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = source.splitlines()
    if not 1 <= line <= len(lines):
        # A HEAD-relative line can point past the end of the worktree file.
        # That makes the hint useless, not the lookup impossible.
        line = 1
    declaration = re.compile(rf"\bfn\s+{re.escape(name)}\b")
    start = _find_declaration_start(lines, line, declaration)
    if start is None:
        return None
    return _collect_brace_balanced_block(lines, start)


def digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _string_field(entry: dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"register entry {index} has no {field}")
    return value


def load_register(path: Path = REGISTER_PATH) -> list[DistinctPair]:
    if not path.exists():
        return []
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LedgerError(f"register is unreadable: {error}") from error
    entries = document.get("pair", [])
    if not isinstance(entries, list):
        raise LedgerError("register's `pair` is not an array of tables")
    pairs: list[DistinctPair] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise LedgerError(f"register entry {index} is not a table")
        reason = _string_field(entry, "reason", index)
        if len(reason.split()) < 12:
            raise LedgerError(
                f"register entry {index} has no real justification: an entry is a"
                " reviewed claim, so it must say WHY the two are distinct"
            )
        pairs.append(
            DistinctPair(
                left_file=_string_field(entry, "left_file", index),
                left_name=_string_field(entry, "left_name", index),
                left_digest=_string_field(entry, "left_digest", index),
                right_file=_string_field(entry, "right_file", index),
                right_name=_string_field(entry, "right_name", index),
                right_digest=_string_field(entry, "right_digest", index),
                reason=reason,
                reviewed_on=_string_field(entry, "reviewed_on", index),
            )
        )
    return pairs


def function_exists(path: Path, name: str) -> bool:
    """Is `name` still declared in `path`?"""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(rf"\bfn\s+{re.escape(name)}\b", source) is not None


def delegates_to(path: Path, line: int, name: str, other: str) -> bool:
    """Does `name` merely CALL `other` rather than reimplement it?

    A one-line forwarder has the same normalized shape as its target and is
    reported as a clone, but there is only ONE implementation and nothing to
    consolidate.  Recognising delegation is a statement about the code, not
    a waiver.
    """

    if name == other:
        return False
    body = normalized_function_text(path, line, name)
    if body is None:
        return False
    return re.search(rf"\b{re.escape(other)}\s*\(", body) is not None


def resolved_reason(finding: dict[str, Any], root: Path = ROOT) -> str | None:
    """Why this finding names no duplication that exists, or `None` if it does.

    Dupehound compares the index against HEAD, so on a branch that renames,
    splits or deletes a file, it faithfully reports the OLD copy -- which is
    already gone -- as the thing being reimplemented.  That is not debt
    anyone can pay down. The same holds when one of the pair simply forwards
    to the other.
    """

    file = root / str(finding["file"])
    original_file = root / str(finding["original_file"])
    name = str(finding["name"])
    original_name = str(finding["original_name"])
    if not function_exists(original_file, original_name):
        return (
            f"{finding['original_file']}::{original_name} is no longer in the tree"
            " -- the other implementation was already removed"
        )
    if not function_exists(file, name):
        return f"{finding['file']}::{name} is no longer in the tree"
    if delegates_to(file, int(finding["line"]), name, original_name):
        return f"{name} delegates to {original_name} rather than reimplementing it"
    if delegates_to(
        original_file, int(finding["original_line"]), original_name, name
    ):
        return f"{original_name} delegates to {name} rather than reimplementing it"
    return None


def _pair_still_matches(pair: DistinctPair, root: Path) -> tuple[bool, str | None]:
    """Does `pair`'s pinned digest still match its source? `(ok, rot_note)`."""
    left = normalized_function_text(root / pair.left_file, 1, pair.left_name)
    right = normalized_function_text(root / pair.right_file, 1, pair.right_name)
    if left is None or right is None:
        return False, (
            f"{pair.left_file}::{pair.left_name} / {pair.right_file}::"
            f"{pair.right_name}: a reviewed function no longer exists --"
            " delete this entry"
        )
    if digest_of(left) != pair.left_digest or digest_of(right) != pair.right_digest:
        return False, (
            f"{pair.left_file}::{pair.left_name} / {pair.right_file}::"
            f"{pair.right_name}: reviewed {pair.reviewed_on}, but the source has"
            " changed since -- re-review and re-pin, or consolidate"
        )
    return True, None


def _classify_finding(
    finding: dict[str, Any],
    by_key: dict[tuple[str, str, str, str], DistinctPair],
    root: Path,
    notes: list[str],
) -> str:
    """Classify one finding, appending any note. Returns 'resolved',
    'unregistered', 'changed', or 'matched'."""
    resolved = resolved_reason(finding, root)
    if resolved is not None:
        notes.append(
            f"resolved: {finding['file']}:{finding['line']} {finding['name']}"
            f" -- {resolved}"
        )
        return "resolved"
    key = (
        str(finding["file"]),
        str(finding["name"]),
        str(finding["original_file"]),
        str(finding["original_name"]),
    )
    reverse = (key[2], key[3], key[0], key[1])
    pair = by_key.get(key) or by_key.get(reverse)
    if pair is None:
        return "unregistered"
    ok, rot_note = _pair_still_matches(pair, root)
    if not ok:
        # `_pair_still_matches`'s note is phrased for the register-wide rot
        # sweep below; reuse it here too, since the two conditions
        # (not-located / changed-since-review) are identical.
        notes.append(rot_note.replace("delete this entry", "re-review and re-pin"))
        return "changed"
    return "matched"


def _rotted_register_entries(
    pairs: list[DistinctPair], root: Path, notes: list[str]
) -> list[DistinctPair]:
    """Every entry whose pinned digest no longer matches its source."""
    rotted: list[DistinctPair] = []
    for pair in pairs:
        ok, rot_note = _pair_still_matches(pair, root)
        if not ok:
            rotted.append(pair)
            notes.append(rot_note)
    return rotted


def partition(
    findings: Iterable[dict[str, Any]],
    pairs: list[DistinctPair],
    root: Path = ROOT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[DistinctPair]]:
    """Split findings into (unregistered, changed, notes, unused-register-entries).

    `unregistered` are real gate failures.  `changed` are findings whose pair
    IS registered but whose source no longer matches the reviewed text.
    """

    by_key = {pair.key(): pair for pair in pairs}
    unregistered: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    notes: list[str] = []
    for finding in findings:
        kind = _classify_finding(finding, by_key, root, notes)
        if kind == "unregistered":
            unregistered.append(finding)
        elif kind == "changed":
            changed.append(finding)
    rotted = _rotted_register_entries(pairs, root, notes)
    return unregistered, changed, notes, rotted


def main() -> int:
    """Print the digests for a pair, to author or re-pin a register entry."""

    if len(sys.argv) != 5:
        print(
            "usage: dupehound_ledger.py <left_file> <left_name> <right_file> <right_name>",
            file=sys.stderr,
        )
        return 2
    left_file, left_name, right_file, right_name = sys.argv[1:5]
    for relative, name in ((left_file, left_name), (right_file, right_name)):
        path = ROOT / relative
        source = path.read_text(encoding="utf-8") if path.exists() else ""
        found = None
        for index, text in enumerate(source.splitlines(), start=1):
            if re.search(rf"\bfn\s+{re.escape(name)}\b", text):
                found = normalized_function_text(path, index, name)
                break
        if found is None:
            print(f"{relative}::{name}: NOT FOUND", file=sys.stderr)
            return 1
        print(f"{relative}::{name} = {digest_of(found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
