"""Report claims must be derived from committed public spec metadata."""

import subprocess
from pathlib import Path

from scripts.public_spec_report import committed_specs, evaluate_status, render

SHA = "a" * 40
REPO = "graph-os"


def status(**changes):
    base = {
        "schema_version": 1,
        "spec_id": "GRAPHOS-001",
        "owner_repo": REPO,
        "requirement_ids": ["EH-001"],
        "delivery_state": "SPECIFIED",
        "acceptance_state": "NOT_AUDITED",
        "evidence": [],
    }
    base.update(changes)
    return base


def proof(kind, commit=SHA):
    return {
        "kind": kind,
        "url": f"https://github.com/Knuckles-Team/{REPO}/commit/{commit}",
        "commit": commit,
        "result": "passed",
    }


def test_no_manifest_or_unproven_landing_is_unknown():
    row = evaluate_status(REPO, "one", "specs/one/spec.md", None)
    assert (row["delivery"], row["acceptance"]) == ("UNKNOWN", "NOT_AUDITED")
    row = evaluate_status(
        REPO, "one", "specs/one/spec.md", status(delivery_state="LANDED")
    )
    assert row["delivery"] == "UNKNOWN"
    assert "merged-head" in row["problem"]


def test_acceptance_requires_matching_merged_head_test_and_consumer_or_release():
    row = evaluate_status(
        REPO,
        "one",
        "specs/one/spec.md",
        status(
            delivery_state="LANDED",
            acceptance_state="ACCEPTED",
            evidence=[proof("merged_head"), proof("test")],
        ),
    )
    assert (row["delivery"], row["acceptance"]) == ("LANDED", "NOT_AUDITED")
    row = evaluate_status(
        REPO,
        "one",
        "specs/one/spec.md",
        status(
            delivery_state="LANDED",
            acceptance_state="ACCEPTED",
            evidence=[proof("merged_head"), proof("test"), proof("consumer")],
        ),
    )
    assert (row["delivery"], row["acceptance"]) == ("LANDED", "ACCEPTED")


def test_report_escapes_spec_metadata():
    row = evaluate_status(
        REPO, "one", "specs/one/spec.md", status(spec_id="<script>alert(1)</script>")
    )
    page = render({REPO: SHA}, [row])
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_uncommitted_manifest_cannot_advance_status(tmp_path: Path):
    repo = tmp_path / REPO
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    spec = repo / "specs" / "one"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("# one\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "specs/one/spec.md"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add spec"], check=True)
    (spec / "status.json").write_text('{"delivery_state":"LANDED"}', encoding="utf-8")
    _, rows = committed_specs(repo, REPO)
    assert len(rows) == 1
    assert (rows[0]["delivery"], rows[0]["acceptance"]) == ("UNKNOWN", "NOT_AUDITED")
