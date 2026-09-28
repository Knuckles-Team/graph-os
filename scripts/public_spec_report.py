#!/usr/bin/env python3
"""Build a public, evidence-aware HTML index from owner-native specs.

Only committed files in six public GitHub checkouts are read. No workspace
manifest, private draft repository, deployment inventory, or live service is
needed. The output is a point-in-time snapshot, not a live CI status feed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

REPOS = (
    "epistemic-graph",
    "agent-connector-sdk",
    "agent-utilities",
    "graph-os",
    "agent-webui",
    "repository-manager",
)
DELIVERY = frozenset(
    {
        "UNKNOWN",
        "SPECIFIED",
        "BUILDING",
        "BUILT",
        "LANDED",
        "CLOSED",
        "DEFERRED",
        "REJECTED",
    }
)
ACCEPTANCE = frozenset({"NOT_AUDITED", "PENDING", "ACCEPTED", "FAILED"})
SHA = re.compile(r"[0-9a-f]{40}\Z")
PUBLIC_URL = re.compile(
    r"https://github\.com/Knuckles-Team/[A-Za-z0-9_.-]+/(?:commit|pull|actions|issues)/[^\s]+\Z"
)


def git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout


def committed_specs(repo: Path, owner_repo: str) -> tuple[str, list[dict[str, Any]]]:
    """Read HEAD blobs only, so dirty worktrees cannot create false claims."""
    revision = git(repo, "rev-parse", "HEAD").strip()
    paths = set(
        git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", "specs").splitlines()
    )
    rows: list[dict[str, Any]] = []
    for spec_path in sorted(
        p for p in paths if re.fullmatch(r"specs/[^/]+/spec\.md", p)
    ):
        slug = Path(spec_path).parent.name
        if slug.startswith("_"):
            continue
        manifest_path = f"specs/{slug}/status.json"
        manifest: dict[str, Any] | None = None
        problem = ""
        if manifest_path in paths:
            try:
                raw = json.loads(git(repo, "show", f"HEAD:{manifest_path}"))
                if not isinstance(raw, dict):
                    raise ValueError("status.json is not an object")
                manifest = raw
            except (json.JSONDecodeError, ValueError) as error:
                problem = f"Invalid status.json: {error}"
        else:
            problem = "No committed status.json"
        row = evaluate_status(owner_repo, slug, spec_path, manifest, problem)
        if row["delivery"] in {"LANDED", "CLOSED"}:
            merged = next(
                item for item in row["evidence"] if item["kind"] == "merged_head"
            )
            on_main = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "merge-base",
                        "--is-ancestor",
                        merged["commit"],
                        "refs/remotes/origin/main",
                    ],
                    check=False,
                    capture_output=True,
                ).returncode
                == 0
            )
            if not on_main:
                row["delivery"] = "UNKNOWN"
                row["acceptance"] = "NOT_AUDITED"
                row["problem"] = "Claimed merged head is not reachable from origin/main"
        rows.append(row)
    return revision, rows


def valid_evidence(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("kind")
        in {
            "merged_head",
            "implementation",
            "branch",
            "pr",
            "test",
            "consumer",
            "release",
            "decision",
        }
        and item.get("result") in {"passed", "failed"}
        and isinstance(item.get("commit"), str)
        and SHA.fullmatch(item["commit"]) is not None
        and isinstance(item.get("url"), str)
        and PUBLIC_URL.fullmatch(item["url"]) is not None
    )


def evaluate_status(
    repo: str,
    slug: str,
    spec_path: str,
    manifest: dict[str, Any] | None,
    problem: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "repo": repo,
        "slug": slug,
        "spec_path": spec_path,
        "spec_id": slug,
        "requirement_ids": [],
        "delivery": "UNKNOWN",
        "acceptance": "NOT_AUDITED",
        "evidence": [],
        "problem": problem,
    }
    if manifest is None:
        return row
    if manifest.get("schema_version") != 1 or manifest.get("owner_repo") != repo:
        row["problem"] = "Unsupported schema version or owner mismatch"
        return row
    spec_id = manifest.get("spec_id")
    requirement_ids = manifest.get("requirement_ids")
    delivery = manifest.get("delivery_state")
    acceptance = manifest.get("acceptance_state")
    evidence = manifest.get("evidence")
    if not isinstance(spec_id, str) or not spec_id.strip():
        row["problem"] = "Missing spec_id"
        return row
    row["spec_id"] = spec_id
    if not isinstance(requirement_ids, list) or not all(
        isinstance(v, str) for v in requirement_ids
    ):
        row["problem"] = "Invalid requirement_ids"
        return row
    row["requirement_ids"] = requirement_ids
    if (
        delivery not in DELIVERY
        or acceptance not in ACCEPTANCE
        or not isinstance(evidence, list)
    ):
        row["problem"] = "Unknown state or malformed evidence array"
        return row
    if not all(valid_evidence(item) for item in evidence):
        row["problem"] = "Evidence needs a public URL, full commit SHA, kind and result"
        return row
    row["evidence"] = evidence
    passed = [item for item in evidence if item["result"] == "passed"]
    merged = [
        item
        for item in passed
        if item["kind"] == "merged_head"
        and item["url"]
        == f"https://github.com/Knuckles-Team/{repo}/commit/{item['commit']}"
    ]
    merged_shas = {item["commit"] for item in merged}
    if delivery in {"LANDED", "CLOSED"} and not merged:
        row["problem"] = (
            "Landed or closed claim lacks an exact public merged-head commit"
        )
        delivery = "UNKNOWN"
    elif delivery in {"BUILDING", "BUILT"} and not any(
        item["kind"] in {"implementation", "branch", "pr", "merged_head"}
        for item in passed
    ):
        row["problem"] = "Build claim lacks public implementation evidence"
        delivery = "UNKNOWN"
    elif delivery in {"DEFERRED", "REJECTED"} and not any(
        item["kind"] == "decision" for item in passed
    ):
        row["problem"] = "Disposition lacks a public decision record"
        delivery = "UNKNOWN"
    row["delivery"] = delivery
    if acceptance == "ACCEPTED":
        kinds = {item["kind"] for item in passed if item["commit"] in merged_shas}
        if (
            delivery not in {"LANDED", "CLOSED"}
            or "test" not in kinds
            or not ({"consumer", "release"} & kinds)
        ):
            row["problem"] = (
                "Acceptance lacks merged-head test and consumer or release proof"
            )
            acceptance = "NOT_AUDITED"
    elif acceptance == "FAILED" and not any(
        item["kind"] == "test" and item["result"] == "failed" for item in evidence
    ):
        row["problem"] = "Failed acceptance lacks a public failed test result"
        acceptance = "NOT_AUDITED"
    row["acceptance"] = acceptance
    return row


def render(
    revisions: dict[str, str], rows: list[dict[str, Any]], source_time: str = "unknown"
) -> str:
    delivery_counts = Counter(row["delivery"] for row in rows)
    acceptance_counts = Counter(row["acceptance"] for row in rows)

    def badge(state: str) -> str:
        return f'<span class="badge {escape(state.lower())}">{escape(state.replace("_", " "))}</span>'

    body: list[str] = []
    for row in sorted(rows, key=lambda value: (value["repo"], value["spec_id"])):
        repo = escape(row["repo"])
        spec_url = f"https://github.com/Knuckles-Team/{repo}/blob/main/{escape(row['spec_path'])}"
        requirements = ", ".join(escape(value) for value in row["requirement_ids"])
        proof = " ".join(
            f'<a href="{escape(item["url"], quote=True)}">{escape(item["kind"])}</a>'
            for item in row["evidence"]
        )
        issue = (
            f'<span class="issue">{escape(row["problem"])}</span>'
            if row["problem"]
            else ""
        )
        body.append(
            f'<tr data-repo="{repo}" data-delivery="{escape(row["delivery"])}" data-acceptance="{escape(row["acceptance"])}">'
            f'<td><a href="{spec_url}">{escape(row["spec_id"])}</a></td><td>{repo}</td>'
            f"<td>{badge(row['delivery'])}</td><td>{badge(row['acceptance'])}</td>"
            f"<td>{requirements}</td><td>{proof}{issue}</td></tr>"
        )
    source_rows = "".join(
        f'<li><a href="https://github.com/Knuckles-Team/{escape(repo)}/commit/{escape(sha)}">'
        f"{escape(repo)} @ {escape(sha[:12])}</a></li>"
        for repo, sha in revisions.items()
    )
    count_text = ", ".join(
        f"{state}: {delivery_counts[state]}"
        for state in sorted(DELIVERY)
        if delivery_counts[state]
    )
    accept_text = ", ".join(
        f"{state}: {acceptance_counts[state]}"
        for state in sorted(ACCEPTANCE)
        if acceptance_counts[state]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Graph OS public specification status</title>
<style>
:root {{ color-scheme: light dark; font: 16px system-ui, sans-serif; }} body {{ max-width: 100rem; margin: auto; padding: 1.5rem; }}
table {{ width: 100%; border-collapse: collapse; }} th,td {{ padding: .5rem; border-bottom: 1px solid #8886; text-align: left; vertical-align: top; }}
th {{ position: sticky; top: 0; background: Canvas; }} .badge {{ border: 1px solid #888; border-radius: .4rem; padding: .12rem .35rem; white-space: nowrap; }}
.accepted,.landed,.closed {{ border-color: #18964a; }} .failed,.rejected {{ border-color: #c33; }} .issue {{ display: block; color: #b33; }}
label {{ display: inline-block; margin: .3rem 1rem .3rem 0; }} input,select {{ font: inherit; padding: .3rem; }}
</style></head><body>
<main><h1>Graph OS public specification status</h1>
<p>Snapshot through {escape(source_time)} from the committed <code>specs/</code> revisions listed below in six public repositories. The repository specs and linked CI results are the evidence; source presence alone cannot establish completion. Missing or unsupported status metadata is shown as UNKNOWN / NOT AUDITED.</p>
<p><strong>{len(rows)} specs</strong> · Delivery: {escape(count_text)} · Acceptance: {escape(accept_text)}</p>
<details><summary>State legend and evidence rules</summary>
<p><strong>Delivery:</strong> UNKNOWN = no supported status claim; SPECIFIED = build contract is published; BUILDING = public implementation work exists; BUILT = implementation exists with public commit evidence; LANDED = exact implementation commit is on default branch; CLOSED = work closed with a merged-head record; DEFERRED/REJECTED = public disposition decision.</p>
<p><strong>Acceptance:</strong> NOT AUDITED = no verified acceptance decision; PENDING = review is open; ACCEPTED = merged-head tests plus consumer or release proof at the same commit; FAILED = linked failed test. Delivery and acceptance are independent.</p>
<p>This report validates the shape of public evidence and exact commit references. Human reviewers must still evaluate whether linked tests satisfy every requirement. The report lists published specs; it does not claim that every program obligation has a spec.</p></details>
<label>Search <input id="search" type="search" placeholder="Spec, repo, requirement"></label>
<label>Repository <select id="repo"><option value="">All</option>{"".join(f'<option value="{escape(repo)}">{escape(repo)}</option>' for repo in REPOS)}</select></label>
<label>Delivery <select id="delivery"><option value="">All</option>{"".join(f"<option>{escape(s)}</option>" for s in sorted(DELIVERY))}</select></label>
<label>Acceptance <select id="acceptance"><option value="">All</option>{"".join(f"<option>{escape(s)}</option>" for s in sorted(ACCEPTANCE))}</select></label>
<p id="visible" aria-live="polite"></p>
<table><thead><tr><th>Specification</th><th>Owner</th><th>Delivery</th><th>Acceptance</th><th>Requirement IDs</th><th>Public evidence / issue</th></tr></thead><tbody>{"".join(body)}</tbody></table>
<details><summary>Source revisions</summary><ul>{source_rows}</ul></details></main>
<script>
const controls = ['search','repo','delivery','acceptance'].map(id => document.getElementById(id));
function filter() {{ let count = 0; for (const row of document.querySelectorAll('tbody tr')) {{
  const show = row.textContent.toLowerCase().includes(controls[0].value.toLowerCase()) &&
    (!controls[1].value || row.dataset.repo === controls[1].value) &&
    (!controls[2].value || row.dataset.delivery === controls[2].value) &&
    (!controls[3].value || row.dataset.acceptance === controls[3].value);
  row.hidden = !show; if (show) count++;
}} document.getElementById('visible').textContent = `${{count}} matching specs`; }}
controls.forEach(control => control.addEventListener('input', filter)); filter();
</script></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkouts-root",
        type=Path,
        help="Directory containing the six public repository checkouts",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Explicit public checkout; repeat for all six",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="HTML output path (for GraphOS Pages: docs/spec-status.html)",
    )
    args = parser.parse_args()
    paths: dict[str, Path] = {}
    if args.checkouts_root:
        paths = {name: args.checkouts_root / name for name in REPOS}
    for item in args.repo:
        if "=" not in item:
            parser.error("--repo must be NAME=PATH")
        name, path = item.split("=", 1)
        if name not in REPOS:
            parser.error(f"unknown repository {name}")
        paths[name] = Path(path)
    if set(paths) != set(REPOS):
        parser.error("provide --checkouts-root or all six --repo NAME=PATH values")
    revisions: dict[str, str] = {}
    source_times: list[datetime] = []
    rows: list[dict[str, Any]] = []
    for name in REPOS:
        path = paths[name]
        if not (path / ".git").exists():
            parser.error(f"not a Git checkout: {path}")
        revision, found = committed_specs(path, name)
        revisions[name] = revision
        source_times.append(
            datetime.fromisoformat(
                git(path, "show", "-s", "--format=%cI", "HEAD").strip()
            )
        )
        rows.extend(found)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_time = max(source_times).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    args.output.write_text(render(revisions, rows, source_time), encoding="utf-8")
    print(f"Wrote {args.output}: {len(rows)} committed public specs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
