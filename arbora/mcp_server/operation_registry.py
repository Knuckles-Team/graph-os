"""arbora's published operation registry: names, features and verbs as data.

CONCEPT:ARBORA-HOST.registry.published-operation-vocabulary

arbora is the operation authority. Consumers that only need the *vocabulary*
of served operations (the agent harness's skill validation, which checks that
a skill names real arbora operations) read this versioned document instead
of importing arbora's serving code or keeping a hand-copied snapshot.

The document is derived from :data:`arbora.mcp_server.backing.tool_specs.TOOL_SPECS`
(itself generated from the action manifest), so it cannot name an operation
arbora does not serve. The packaged copy (``arbora_operations.json`` next
to this module) is regenerated with ``python -m
arbora.mcp_server.operation_registry --write`` and checked by the test suite.
Its shape is the one ``agent_utilities.skills.arbora_operations`` reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

#: Bumped only when the document's shape changes (never for new operations).
SCHEMA_VERSION = 1

PUBLISHED_PATH = Path(__file__).with_name("arbora_operations.json")


def _registry_body() -> dict[str, Any]:
    from arbora.mcp_server.backing.tool_specs import (
        INTENT_VERBS,
        SUPPORTED_FEATURES,
        TOOL_SPECS,
        TOOL_VERBS,
    )

    return {
        "intent_verbs": list(INTENT_VERBS),
        "operations": [
            {
                "actions": list(spec.actions),
                "feature": spec.feature,
                "name": spec.name,
                "surface": spec.surface,
            }
            for spec in TOOL_SPECS
        ],
        "supported_features": sorted(SUPPORTED_FEATURES),
        "tool_verbs": {name: list(verbs) for name, verbs in sorted(TOOL_VERBS.items())},
    }


def registry_digest(body: dict[str, Any]) -> str:
    """Content digest of the vocabulary (stable across key order)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def operation_registry_document() -> dict[str, Any]:
    """The versioned operation-registry document arbora publishes."""
    body = _registry_body()
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "arbora operation registry (arbora.mcp_server.backing.tool_specs)",
        "registry_digest": registry_digest(body),
        **body,
    }


def render() -> str:
    return json.dumps(operation_registry_document(), indent=1, sort_keys=True) + "\n"


def published_operation_registry() -> dict[str, Any]:
    """The packaged document, as consumers read it."""
    return json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = render()
    if args.write:
        PUBLISHED_PATH.write_text(rendered, encoding="utf-8")
        return 0
    current = (
        PUBLISHED_PATH.read_text(encoding="utf-8") if PUBLISHED_PATH.exists() else ""
    )
    if current != rendered:
        print(
            f"{PUBLISHED_PATH.name} is stale; run python -m "
            "arbora.mcp_server.operation_registry --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
