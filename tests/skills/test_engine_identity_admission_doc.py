"""Contract of the engine-identity admission procedure shipped with graphos-genesis.

The admission credential (``EPISTEMIC_GRAPH_SIGNER_KEYS_JSON``) is easy to
misprovision and fails invisibly, so the procedure must name the real chain,
never contain a credential-shaped value, and be reachable from the skill's
cross-cutting phase and exit gates — a document nobody is routed to is dead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[2] / "graph_os" / "skills" / "graphos-genesis"
)
REFERENCE = SKILL_DIR / "references" / "engine-identity-admission.md"
SKILL_MD = SKILL_DIR / "SKILL.md"
SECURITY_OPS = SKILL_DIR / "references" / "security-and-operations.md"


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def test_reference_doc_exists_and_is_substantial() -> None:
    assert len(REFERENCE.read_text(encoding="utf-8").splitlines()) > 100


@pytest.mark.parametrize(
    "must_contain",
    [
        "EPISTEMIC_GRAPH_SIGNER_KEYS_JSON",
        "control:system",
        "__control__",
        "CypherEngineError",
        "resolve_admission_authority",
        "register_identity",
        "RegisterIdentity",
        "SIGNER_TRUST_DENIED",
        "signer to be the calling principal",
        "unconstrained authority over identity",
        "shared symmetric secret",
        "No rotation path",
        "## Rotation and revocation",
        "openssl rand -hex 32",
        'Pattern("tenant__<slug>__*")',
        "AdmissionAuthorityError",
        "## Which topology needs this",
    ],
)
def test_reference_doc_covers_required_content(must_contain: str) -> None:
    assert must_contain in REFERENCE.read_text(encoding="utf-8")


def test_reference_doc_never_contains_a_credential_shaped_value() -> None:
    text = REFERENCE.read_text(encoding="utf-8")

    assert re.search(r"\b[0-9a-fA-F]{32,}\b", text) is None
    assert "<principal>" in text
    assert "<hex-key>" in text


def test_skill_routes_phase5_and_the_exit_gate_to_the_reference() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    phase5 = _section(text, "### Phase 5", "### Phase 6")
    phase8 = _section(text, "### Phase 8", "## Output")

    assert "engine-identity-admission.md" in phase5
    assert "engine-identity-admission.md" in phase8
    assert "CypherEngineError" in phase8


def test_security_and_operations_distinguishes_the_two_signer_concepts() -> None:
    text = SECURITY_OPS.read_text(encoding="utf-8")

    assert "engine-identity-admission.md" in text
    assert "Signing keys specifically" in text
