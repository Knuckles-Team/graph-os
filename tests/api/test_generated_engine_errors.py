"""The checked-in GraphOS error table must match the pinned EG wheel exactly."""

from __future__ import annotations

import hashlib
import importlib.resources
import json

from graph_os.api.generated.engine_errors import (
    ENGINE_ERRORS,
    SOURCE_CONTRACT_VERSION,
    SOURCE_SHA256,
)


def test_generated_engine_errors_match_pinned_eg_contract() -> None:
    source = importlib.resources.files("epistemic_graph") / "contract/errors.json"
    raw = source.read_bytes()
    contract = json.loads(raw)
    expected = {
        row["code"]: (row["http_status_hint"], row["retryable"])
        for row in contract["errors"]
    }
    assert len(expected) == len(contract["errors"])
    assert SOURCE_CONTRACT_VERSION == contract["contract_version"]
    assert SOURCE_SHA256 == hashlib.sha256(raw).hexdigest()
    assert ENGINE_ERRORS == expected
