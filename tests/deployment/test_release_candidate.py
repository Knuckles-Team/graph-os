"""T-RL-01–04/10: candidate contracts and setup-config wiring, no target effects."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import sys
from types import ModuleType

import pytest
from pydantic import ValidationError

from graph_os.deployment.cli import main
from graph_os.deployment.genesis_environments import BUILTIN_ENVIRONMENTS_DIR
from graph_os.deployment.release_candidate import (
    CandidateError,
    StageReadiness,
    _canonical_digest,
    execute_candidate,
    plan_candidate,
    read_candidate,
)

_KEY = b"disposable-test-trust-key"


@pytest.fixture
def authority(monkeypatch):
    """Mock AU's public verifier boundary with independent fixture trust.

    GraphOS must pass the whole signed input to this authority, using the fixed
    configured verifier reference. These are consumer tests, not AU crypto tests.
    """
    calls = []
    module = ModuleType("agent_utilities.skills.runtime_validation")

    def verify(signed, *, verifier_reference):
        calls.append(verifier_reference)
        unsigned = {key: value for key, value in signed.items() if key != "signature"}
        expected = hmac.new(
            _KEY, json.dumps(unsigned, sort_keys=True).encode(), "sha256"
        ).hexdigest()
        if not hmac.compare_digest(str(signed.get("signature", "")), expected):
            raise RuntimeError("private endpoint/token must never escape")
        return unsigned

    module.verify_signed_evidence = verify
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


@pytest.fixture
def candidate():
    import yaml

    path = BUILTIN_ENVIRONMENTS_DIR / "prod.yaml"
    image = yaml.safe_load(path.read_bytes())["release"]["revision"]
    ids = ("epistemic-graph", "graph-os", "agent-webui", "connector.optional")
    artifacts = [
        {
            "component_id": key,
            "repository": "Knuckles-Team/" + key,
            "source_revision": "a" * 40,
            "digest": image,
            "kind": "image",
            "api": "api/v1",
            "schema": "schema/v1",
            "build_receipt": "https://github.com/Knuckles-Team/"
            + key
            + "/commit/"
            + "a" * 40,
        }
        for key in ids
    ]
    predecessors = {"graph-os": ["epistemic-graph"], "agent-webui": ["graph-os"]}
    stages = [
        {
            "component_id": key,
            "optional": key == "connector.optional",
            "enabled": key != "connector.optional",
            "probe_contract": "functional/v1",
            "predecessors": [
                {"component_id": edge, "api": "api/v1", "schema": "schema/v1"}
                for edge in predecessors.get(key, [])
            ],
        }
        for key in ids
    ]
    return {
        "schema_version": 1,
        "candidate_id": "fixture-release",
        "created_at": "2026-09-30T00:00:00Z",
        "artifacts": artifacts,
        "stages": stages,
        "profiles": [
            {
                "component_id": "graph-os",
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "artifact_digest": image,
            }
        ],
    }


def _write(tmp_path, raw, *, sign=True):
    signed = copy.deepcopy(raw)
    if sign:
        signed["signature"] = hmac.new(
            _KEY, json.dumps(raw, sort_keys=True).encode(), "sha256"
        ).hexdigest()
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(signed))
    return path, _canonical_digest(raw)


def _plan(tmp_path, raw):
    path, digest = _write(tmp_path, raw)
    return plan_candidate(path, trusted_digest=digest, profile_name="prod")


def test_signed_candidate_order_and_immutable_reader(tmp_path, candidate, authority):
    path, digest = _write(tmp_path, candidate)
    parsed, actual = read_candidate(path, trusted_digest=digest)
    assert actual == digest
    assert isinstance(parsed.artifacts, tuple)
    with pytest.raises(ValidationError):
        parsed.candidate_id = "changed"
    plan = _plan(tmp_path, candidate)
    assert plan["order"] == ["epistemic-graph", "graph-os", "agent-webui"]
    assert plan["manifest_digest"] == digest
    assert plan["executed"] is False
    assert set(authority) == {"CERT_EVIDENCE_VERIFIER_COMMAND"}


def test_deterministic_optional_order(tmp_path, candidate, authority):
    candidate["stages"][-1]["enabled"] = True
    first = _plan(tmp_path, candidate)["order"]
    candidate["stages"].reverse()
    candidate["artifacts"].reverse()
    assert _plan(tmp_path, candidate)["order"] == first
    assert first == ["connector.optional", "epistemic-graph", "graph-os", "agent-webui"]


@pytest.mark.parametrize("sign", [False, True])
def test_attacker_self_consistent_hash_never_grants_trust(
    tmp_path, candidate, authority, sign
):
    path, digest = _write(tmp_path, candidate, sign=sign)
    if sign:
        raw = json.loads(path.read_text())
        raw["signature"] = "attacker-forged"
        path.write_text(json.dumps(raw))
    with pytest.raises(CandidateError, match="^candidate_signature_unverified$"):
        read_candidate(path, trusted_digest=digest)
    assert len(authority) == 1


def test_tamper_and_digest_selection(tmp_path, candidate, authority):
    path, digest = _write(tmp_path, candidate)
    with pytest.raises(CandidateError, match="candidate_trust_mismatch"):
        read_candidate(path, trusted_digest="sha256:" + "b" * 64)
    raw = json.loads(path.read_text())
    raw["artifacts"][0]["digest"] = "sha256:" + "b" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(CandidateError, match="candidate_signature_unverified"):
        read_candidate(
            path,
            trusted_digest=_canonical_digest(
                {k: v for k, v in raw.items() if k != "signature"}
            ),
        )


@pytest.mark.parametrize("digest", ["", "latest", "sha256:123", "sha256:" + "A" * 64])
def test_trust_selection_invalid(tmp_path, digest):
    with pytest.raises(CandidateError, match="candidate_trust_required"):
        read_candidate(tmp_path / "absent", trusted_digest=digest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("digest", "latest"),
        ("digest", "sha256:123"),
        ("source_revision", "main"),
        ("kind", "tag"),
        ("repository", "https://private.invalid"),
        (
            "build_receipt",
            "https://github.com/Knuckles-Team/epistemic-graph/actions/runs/1?token=fixture",
        ),
    ],
)
def test_artifact_refusal(tmp_path, candidate, authority, field, value):
    candidate["artifacts"][0][field] = value
    with pytest.raises(CandidateError):
        _plan(tmp_path, candidate)


@pytest.mark.spec("GRAPHOS-RELEASE-R001")
def test_execute_candidate_stops_before_next_stage_on_missing_readiness(
    tmp_path, candidate, authority
):
    """T-RL-05/06 (GRAPHOS-RELEASE-R001): a missing predecessor digest/CI result
    halts the rollout before any later stage is probed or released."""
    plan = _plan(tmp_path, candidate)
    seen: list[str] = []

    def probe(component_id: str) -> StageReadiness:
        seen.append(component_id)
        if component_id == "graph-os":
            return StageReadiness(ready=False, reason="ci_result_missing")
        return StageReadiness(ready=True)

    result = execute_candidate(plan, probe=probe)
    assert result["executed"] is False
    assert result["status"] == "blocked"
    assert seen == ["epistemic-graph", "graph-os"]  # agent-webui never probed
    statuses = {r["component_id"]: r["status"] for r in result["stage_receipts"]}
    assert statuses == {"epistemic-graph": "released", "graph-os": "blocked"}


@pytest.mark.spec("GRAPHOS-RELEASE-R001")
def test_execute_candidate_releases_every_stage_when_all_ready(
    tmp_path, candidate, authority
):
    plan = _plan(tmp_path, candidate)
    result = execute_candidate(plan, probe=lambda _: StageReadiness(ready=True))
    assert result["executed"] is True
    assert result["status"] == "executed"
    assert [r["status"] for r in result["stage_receipts"]] == [
        "released",
        "released",
        "released",
    ]


@pytest.mark.parametrize("field", ["artifacts", "stages"])
def test_duplicate_components(tmp_path, candidate, authority, field):
    candidate[field].append(copy.deepcopy(candidate[field][0]))
    with pytest.raises(CandidateError, match="candidate_duplicate"):
        _plan(tmp_path, candidate)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("created_at", "2026-09-30"),
        ("secret", "fixture-token"),
    ],
)
def test_closed_schema(tmp_path, candidate, authority, field, value):
    candidate[field] = value
    with pytest.raises(CandidateError, match="candidate_schema_invalid"):
        _plan(tmp_path, candidate)


def test_duplicate_json_keys_and_bounded_regular_inputs(tmp_path):
    path = tmp_path / "candidate.json"
    path.write_text('{"schema_version":1,"schema_version":2}')
    with pytest.raises(CandidateError, match="candidate_duplicate_key"):
        read_candidate(path, trusted_digest="sha256:" + "a" * 64)
    path.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(CandidateError, match="candidate_input_too_large"):
        read_candidate(path, trusted_digest="sha256:" + "a" * 64)


@pytest.mark.parametrize("predecessor", ["unknown", "connector.optional"])
def test_missing_or_disabled_predecessor(tmp_path, candidate, authority, predecessor):
    candidate["stages"][1]["predecessors"][0]["component_id"] = predecessor
    with pytest.raises(
        CandidateError, match="candidate_missing_predecessor:graph-os:" + predecessor
    ):
        _plan(tmp_path, candidate)


def test_cycle_and_compatibility(tmp_path, candidate, authority):
    candidate["stages"][0]["predecessors"] = [
        {"component_id": "agent-webui", "api": "api/v1", "schema": "schema/v1"}
    ]
    with pytest.raises(CandidateError, match="candidate_cycle"):
        _plan(tmp_path, candidate)
    candidate["stages"][0]["predecessors"] = []
    candidate["stages"][1]["predecessors"][0]["api"] = "api/v2"
    with pytest.raises(CandidateError, match="candidate_incompatible_edge"):
        _plan(tmp_path, candidate)


@pytest.mark.parametrize("field", ["digest", "artifact_digest"])
def test_profile_binding(tmp_path, candidate, authority, field):
    candidate["profiles"][0][field] = "sha256:" + "b" * 64
    with pytest.raises(CandidateError, match="candidate_profile"):
        _plan(tmp_path, candidate)


def test_real_cli_success_and_refusal_are_redacted(
    tmp_path, candidate, authority, capsys
):
    path, digest = _write(tmp_path, candidate)
    argv = ["release-plan", str(path), "--trusted-digest", digest, "--profile", "prod"]
    assert main(argv) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["redacted"] is True
    assert receipt["executed"] is False
    assert receipt["acceptance"] == "not_audited"
    assert "source_revision" in receipt["stages"][0]
    assert "probe" not in receipt
    # Unknown raw material is refused, never reflected in public errors.
    candidate["secret"] = "fixture-token:https://private.invalid"
    path, digest = _write(tmp_path, candidate)
    argv[argv.index("--trusted-digest") + 1] = digest
    assert main(argv) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["executed"] is False
    assert "fixture-token" not in output
    assert "private.invalid" not in output
    assert str(tmp_path) not in output


def test_real_au_authority_rejects_attacker_self_hash(tmp_path, candidate, monkeypatch):
    """Exercise AU and its external verifier with independent Ed25519 trust."""
    import base64
    from pathlib import Path

    import cryptography
    from agent_utilities.skills import runtime_validation
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes_raw()
    key_id = "key:" + hashlib.sha256(public_key).hexdigest()
    verifier = tmp_path / "verify.py"
    verifier.write_text(
        "import base64, hashlib, json, sys\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey\n"
        "signed = json.load(sys.stdin)\n"
        "signature = signed.pop('signature')\n"
        "payload = json.dumps(signed, sort_keys=True, separators=(',', ':')).encode()\n"
        "public = bytes.fromhex(sys.argv[1])\n"
        "assert signature['algorithm'] == 'ed25519'\n"
        "assert signature['keyId'] == 'key:' + hashlib.sha256(public).hexdigest()\n"
        "Ed25519PublicKey.from_public_bytes(public).verify(\n"
        "    base64.urlsafe_b64decode(signature['signature'] + '=='), payload)\n"
        "print(json.dumps({'verified': True, 'keyId': signature['keyId'],\n"
        "    'subjectDigest': 'sha256:' + hashlib.sha256(payload).hexdigest()}))\n"
    )
    monkeypatch.setenv(
        "CERT_EVIDENCE_VERIFIER_COMMAND",
        json.dumps(
            [
                str(Path(sys.executable).resolve()),
                str(verifier),
                public_key.hex(),
                str(Path(cryptography.__file__).resolve().parents[1]),
            ]
        ),
    )
    assert runtime_validation.verify_signed_evidence.__module__ == (
        "agent_utilities.skills.runtime_validation"
    )
    payload = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    digest = _canonical_digest(candidate)
    signed = {
        **candidate,
        "signature": {
            "algorithm": "ed25519",
            "keyId": key_id,
            "subjectDigest": digest,
            "signature": base64.urlsafe_b64encode(key.sign(payload))
            .decode()
            .rstrip("="),
        },
    }
    path = tmp_path / "signed.json"
    path.write_text(json.dumps(signed))
    assert (
        plan_candidate(path, trusted_digest=digest, profile_name="prod")["executed"]
        is False
    )
    # Rehash and re-sign modified content with an attacker-controlled key.
    signed["candidate_id"] = "attacker"
    unsigned = {k: v for k, v in signed.items() if k != "signature"}
    digest = _canonical_digest(unsigned)
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    signed["signature"]["subjectDigest"] = digest
    signed["signature"]["signature"] = (
        base64.urlsafe_b64encode(Ed25519PrivateKey.generate().sign(payload))
        .decode()
        .rstrip("=")
    )
    path.write_text(json.dumps(signed))
    with pytest.raises(CandidateError, match="^candidate_signature_unverified$"):
        read_candidate(path, trusted_digest=digest)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_candidate_requires_regular_nonsymlink_file(tmp_path, kind):
    import os

    path = tmp_path / "input"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("{}")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(CandidateError, match="^candidate_input_invalid$"):
        read_candidate(path, trusted_digest="sha256:" + "a" * 64)
