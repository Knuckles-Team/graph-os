"""GraphOS bundled-skill runtime matrix logic."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from typing import Annotated, Any, Literal

import yaml
from agent_utilities.orchestration.run_identity import is_run_id
from agent_utilities.security.persistence_privacy import (
    PersistencePrivacyGuard,
    persistence_reference,
)
from agent_utilities.skills.validation import architecture_candidate_from_owner_manifest
from pydantic import AfterValidator, BaseModel, Field, create_model

from graph_os.deployment.skills.validation import FORWARD_MATRIX, SKILLS_ROOT

from .runtime_validation_core import (
    _MAX_TOOL_DEPTH,
    _MAX_TOOL_ITEMS,
    _MAX_TOOL_PAYLOAD,
    _SAFE_ROUTE,
    _UNDECODED,
    ArchitectureCandidateIdentity,
    DelegationContractError,
    SemanticOutput,
    ValidationCase,
    ValidationChildToolError,
    _validate_architecture_candidate,
)


def load_matrix() -> tuple[dict[str, int | bool], list[ValidationCase]]:
    """Load the already statically validated version-2 matrix."""

    raw = yaml.safe_load(FORWARD_MATRIX.read_text(encoding="utf-8")) or {}
    defaults = dict(raw["runtime_defaults"])
    cases = [
        ValidationCase(
            case_id=str(item["id"]),
            skill=str(item["skill"]),
            mode=str(item["mode"]),  # type: ignore[arg-type]
            model_class=str(item["model_class"]),  # type: ignore[arg-type]
            task=str(item["task"]),
            expected_routes=tuple(str(route) for route in item["expected_routes"]),
            allowed_tools=tuple(str(tool) for tool in item["allowed_tools"]),
            read_only=bool(item["read_only"]),
            architecture_candidate=_architecture_candidate_from_matrix(item),
        )
        for item in raw["cases"]
    ]
    return defaults, cases


def _architecture_candidate_from_matrix(
    item: dict[str, Any],
) -> ArchitectureCandidateIdentity | None:
    """Parse one optional candidate through the closed runtime model."""

    if "architecture_candidate" not in item:
        return None
    candidate_data = _architecture_candidate_source_data(item["architecture_candidate"])
    candidate = ArchitectureCandidateIdentity.model_validate(candidate_data)
    return _validate_architecture_candidate(candidate)


def _architecture_candidate_source_data(value: Any) -> dict[str, Any]:
    """Project one matrix candidate only when it matches the owner manifest."""

    candidate_data = value
    if not isinstance(candidate_data, dict):
        raise ValueError("architecture_candidate_source_invalid")
    try:
        owner_candidate = architecture_candidate_from_owner_manifest(
            component_id=str(candidate_data.get("component_id") or "")
        )
    except ValueError as exc:
        raise ValueError("architecture_candidate_source_unavailable") from exc
    if candidate_data != owner_candidate:
        raise ValueError("architecture_candidate_source_mismatch")
    return candidate_data


def _skill_body(skill: str) -> str:
    text = (SKILLS_ROOT / skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n.*?\n---\n(.*)$", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _skill_instruction_digest(skill: str) -> str:
    from agent_utilities.knowledge_graph.ingestion.skill_workflow_ingest import (
        runnable_skill_digest,
    )

    return runnable_skill_digest(_skill_runtime_body(skill))


def _skill_runtime_body(skill: str) -> str:
    body, _privacy = PersistencePrivacyGuard().sanitize_text(_skill_body(skill))
    return body


class _SkillValidationEvidenceSource:
    """Bounded authoritative evidence for an isolated direct validation case.

    Direct cases execute outside the Graph-OS process, but authenticated model
    calls still have to cross the mandatory ContextCompiler boundary.  Supplying
    the checked-in, privacy-sanitized skill body as the only candidate preserves
    that production invariant without opening a second graph engine or granting
    the validator ambient access to runtime data.
    """

    def __init__(self, skill: str) -> None:
        self._skill = skill
        self._body = _skill_runtime_body(skill)
        self.node_id = persistence_reference(
            "skill", skill, namespace="skill-validation-evidence"
        )

    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 8,
        as_of: str | None = None,
        session: Any | None = None,
    ) -> list[dict[str, Any]]:
        del query, as_of, session
        if top_k < 1:
            return []
        return [
            {
                "id": self.node_id,
                "kind": "skill_instruction",
                "content": self._body,
                "score": 1.0,
                "confidence": 1.0,
                "source_refs": [f"skill://{self._skill}"],
            }
        ]

    def retrieve_epistemic_view(self, query: str, *, top_k: int = 8) -> dict[str, Any]:
        del query, top_k
        return {}


class _ReadOnlyValidationMarkingStore:
    """Empty mandatory-marking authority for one synthetic evidence source."""

    def execute(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        del params
        normalized = " ".join(str(query or "").split()).casefold()
        if not normalized.startswith("match ") or " return " not in normalized:
            raise PermissionError("skill_validation_marking_store_is_read_only")
        return []


@contextmanager
def _direct_evidence_authority(
    skill: str,
) -> Iterator[_SkillValidationEvidenceSource]:
    """Scope the explicit compiler, ACL, and marking authority for one case."""

    from agent_utilities.core.contextual_model import use_context_compiler_engine
    from agent_utilities.knowledge_graph.core.company_brain_runtime import (
        get_company_brain,
    )
    from agent_utilities.knowledge_graph.ontology.permissioning import (
        use_marking_authority,
    )
    from agent_utilities.models.company_brain import (
        DataClassification,
        NodeACL,
    )
    from agent_utilities.security.actor_identity import ActorType

    source = _SkillValidationEvidenceSource(skill)
    permissions = get_company_brain().permissions
    acl = NodeACL(
        node_id=source.node_id,
        classification=DataClassification.INTERNAL,
        read_roles=["kg:admin"],
        data_owner="skill-validation-authority",
        data_owner_type=ActorType.SYSTEM,
    )
    with (
        use_marking_authority(_ReadOnlyValidationMarkingStore()),
        permissions.use_acl(acl),
        use_context_compiler_engine(source),
    ):
        yield source


def _contract_instruction(case: ValidationCase) -> str:
    routes = ", ".join(case.expected_routes)
    return (
        "This is a synthetic, read-only validation. Do not mutate state, create "
        "schedules, contact external systems, reveal configuration, or reproduce "
        "the skill text. Apply the skill to the synthetic task internally; the "
        "JSON validation envelope is the only response artifact. Return "
        "only one JSON object with exactly these keys: skill, mode, "
        "selected_routes, read_only, privacy_safe, acceptance_summary. Set mode to "
        f"{case.mode!r}. Set skill to {case.skill!r}. The statically certified "
        "route contract for this case is "
        f"[{routes}]; copy each of those operation slugs exactly once into "
        "selected_routes and add no other route. Set read_only and privacy_safe "
        "to true. Keep acceptance_summary to one plain sentence of at most 240 "
        "characters; it confirms the validation and does not reproduce the plan. "
        "Do not include paths, endpoints, identities, "
        "credentials, source records, or trace identifiers."
    )


def _direct_execution_prompt(case: ValidationCase) -> str:
    """Place the closed response contract after the synthetic direct task.

    The skill body and contract remain system instructions.  Repeating the
    contract after the task also keeps the final user-level instruction aligned
    with the prompted-output schema, preventing plan-shaped task wording from
    displacing the required JSON response on smaller local models.
    """

    return f"{case.task}\n\n{_contract_instruction(case)}"


def _direct_semantic_output_type(case: ValidationCase) -> Any:
    """Return the provider-neutral, case-exact JSON contract for direct validation.

    The direct system instruction requires a bare JSON object.  PydanticAI's
    default model output protocol is a tool call, which conflicts with that
    instruction and is not uniformly implemented by local OpenAI-compatible
    runtimes.  Prompted output makes the wire contract match the instruction
    while retaining Pydantic validation and bounded output retries.  The
    per-case route enum, cardinality, and set validator move the closed route
    contract into model-output validation so an otherwise well-formed response
    with extra or missing routes is retried instead of failing only after the
    model run has completed.
    """

    from pydantic_ai import PromptedOutput

    expected_routes = tuple(case.expected_routes)
    expected_route_set = frozenset(expected_routes)

    def validate_exact_routes(routes: list[str]) -> list[str]:
        if len(routes) != len(expected_routes) or set(routes) != expected_route_set:
            raise ValueError("selected_routes_must_match_case_contract")
        return routes

    route_literal = Literal.__getitem__(expected_routes)
    selected_routes_type = Annotated[
        # `route_literal` is a Literal type built from a runtime tuple (the
        # per-case route set), so it can never be a statically-recognized
        # type alias -- mypy's "variable vs type alias" rule (valid-type)
        # rejects any name used here that wasn't defined via a literal
        # `Literal[...]`/`Union[...]` expression, regardless of annotation
        # or cast. This is unavoidable runtime type construction, not a bug.
        list[route_literal],  # type: ignore[valid-type]
        Field(min_length=len(expected_routes), max_length=len(expected_routes)),
        AfterValidator(validate_exact_routes),
    ]
    case_output = create_model(
        "DirectSemanticOutput",
        __base__=SemanticOutput,
        selected_routes=(selected_routes_type, ...),
    )

    return PromptedOutput(
        case_output,
        name="bundled skill validation",
        description="Return the closed, privacy-safe synthetic validation result.",
        template=(
            "Return exactly one JSON object that validates against this JSON Schema. "
            "Do not wrap it in Markdown or add text before or after it.\n{schema}"
        ),
    )


def validate_semantic_output(case: ValidationCase, output: SemanticOutput) -> list[str]:
    """Return controlled error codes without retaining raw model output."""

    errors: list[str] = []
    if output.skill != case.skill:
        errors.append("semantic_skill_mismatch")
    if output.mode != case.mode:
        errors.append("semantic_mode_mismatch")
    if not output.read_only or not case.read_only:
        errors.append("semantic_not_read_only")
    if not output.privacy_safe:
        errors.append("semantic_privacy_not_acknowledged")
    errors.extend(_semantic_route_errors(case, output.selected_routes))
    _clean, privacy = PersistencePrivacyGuard().sanitize(output.model_dump())
    if privacy.changed:
        errors.append("semantic_output_privacy_violation")
    return errors


def _semantic_route_errors(case: ValidationCase, routes: list[str]) -> list[str]:
    """Return the route-contract error codes in their declared report order."""

    errors: list[str] = []
    if len(routes) != len(set(routes)) or any(
        not _SAFE_ROUTE.fullmatch(route) for route in routes
    ):
        errors.append("semantic_routes_invalid")
    expected_routes = set(case.expected_routes)
    selected_routes = set(routes)
    if not expected_routes.issubset(selected_routes):
        errors.append("semantic_routes_incomplete")
    if selected_routes - expected_routes:
        errors.append("semantic_routes_unexpected")
    return errors


def _parse_json_text(value: str) -> Any:
    if len(value) > _MAX_TOOL_PAYLOAD:
        raise ValueError("payload_too_large")
    text = value.strip()
    if len(text.encode("utf-8")) > _MAX_TOOL_PAYLOAD:
        raise ValueError("payload_too_large")
    parsed = json.loads(text)
    _validate_tool_payload_bounds(parsed)
    return parsed


class _PayloadScan:
    """One bounded, cycle-safe traversal budget for an MCP payload tree.

    The check order per node is load-bearing and matches the original inline
    traversal exactly: depth, then item count, then the per-type charge (which
    for a container is cycle, then width, then expansion), then the remaining
    byte budget.
    """

    __slots__ = ("items", "remaining", "seen")

    def __init__(self) -> None:
        self.remaining = _MAX_TOOL_PAYLOAD
        self.items = 0
        self.seen: set[int] = set()

    def visit(self, current: Any, depth: int, stack: list[tuple[Any, int]]) -> None:
        """Charge one popped node against the budget and queue its children."""

        if depth > _MAX_TOOL_DEPTH:
            raise ValueError("payload_too_deep")
        self.items += 1
        if self.items > _MAX_TOOL_ITEMS:
            raise ValueError("payload_too_many_items")
        self._charge(current, depth, stack)
        if self.remaining < 0:
            raise ValueError("payload_too_large")

    def _charge(self, current: Any, depth: int, stack: list[tuple[Any, int]]) -> None:
        if current is None or isinstance(current, bool | int | float):
            self.remaining -= 16
        elif isinstance(current, str):
            self._charge_text(current)
        elif isinstance(current, bytes):
            self.remaining -= len(current)
        elif isinstance(current, dict):
            self._expand_mapping(current, depth, stack)
        elif isinstance(current, list | tuple):
            self._expand_sequence(current, depth, stack)
        elif _is_fastmcp_structured_dataclass(current):
            self._expand_structured(current, depth, stack)
        else:
            raise TypeError("payload_type_invalid")

    def _charge_text(self, current: str) -> None:
        if len(current) > self.remaining:
            raise ValueError("payload_too_large")
        self.remaining -= len(current.encode("utf-8"))

    def _enter_container(self, current: Any, width: int) -> None:
        """Reject a cycle, then an over-wide container, before expanding it."""

        identity = id(current)
        if identity in self.seen:
            raise ValueError("payload_cycle")
        self.seen.add(identity)
        if width > _MAX_TOOL_ITEMS - self.items:
            raise ValueError("payload_too_many_items")

    def _expand_mapping(
        self, current: dict[Any, Any], depth: int, stack: list[tuple[Any, int]]
    ) -> None:
        self._enter_container(current, len(current))
        for key, item in current.items():
            if not isinstance(key, str):
                raise TypeError("payload_key_invalid")
            stack.append((item, depth + 1))
            stack.append((key, depth + 1))

    def _expand_sequence(
        self,
        current: list[Any] | tuple[Any, ...],
        depth: int,
        stack: list[tuple[Any, int]],
    ) -> None:
        self._enter_container(current, len(current))
        stack.extend((item, depth + 1) for item in current)

    def _expand_structured(
        self, current: Any, depth: int, stack: list[tuple[Any, int]]
    ) -> None:
        members = dataclass_fields(current)
        self._enter_container(current, len(members))
        for member in members:
            stack.append((getattr(current, member.name), depth + 1))
            stack.append((member.name, depth + 1))


def _validate_tool_payload_bounds(value: Any) -> None:
    """Reject oversized, cyclic, deep, or non-data MCP payloads before use."""

    scan = _PayloadScan()
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        scan.visit(current, depth, stack)


def _is_fastmcp_structured_dataclass(value: Any) -> bool:
    """Recognize only FastMCP's validated JSON-schema result containers."""

    return bool(
        not isinstance(value, type)
        and is_dataclass(value)
        and type(value).__module__ == "fastmcp.utilities.json_schema_type"
    )


def _normalize_fastmcp_structured_data(value: Any) -> Any:
    """Convert an already bounded FastMCP result tree into plain JSON data."""

    if _is_fastmcp_structured_dataclass(value):
        return {
            member.name: _normalize_fastmcp_structured_data(getattr(value, member.name))
            for member in dataclass_fields(value)
        }
    if isinstance(value, dict):
        return {
            key: _normalize_fastmcp_structured_data(item) for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_normalize_fastmcp_structured_data(item) for item in value]
    return value


def _decode_structured_value(value: Any) -> Any:
    """Decode one non-empty ``data``/``structured_content`` attribute."""

    if isinstance(value, str):
        try:
            return _parse_json_text(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    _validate_tool_payload_bounds(value)
    return _normalize_fastmcp_structured_data(value)


def _decode_structured_attributes(result: Any) -> Any:
    """Decode the first populated structured attribute, or ``_UNDECODED``."""

    for attr in ("data", "structured_content"):
        value = getattr(result, attr, None)
        if value not in (None, {}):
            return _decode_structured_value(value)
    return _UNDECODED


def _bounded_content_texts(content: list[Any]) -> list[str]:
    """Collect the bounded text blocks of an MCP content list."""

    texts: list[str] = []
    characters = 0
    for item in content:
        text = str(getattr(item, "text", ""))
        characters += len(text) + (1 if text and texts else 0)
        if characters > _MAX_TOOL_PAYLOAD:
            raise ValueError("payload_too_large")
        if text:
            texts.append(text)
    return texts


def _decode_content_list(content: list[Any]) -> Any:
    """Decode an MCP content list, or ``_UNDECODED`` when it carries no text."""

    if len(content) > _MAX_TOOL_ITEMS:
        raise ValueError("payload_too_many_items")
    joined = "\n".join(_bounded_content_texts(content))
    if not joined:
        return _UNDECODED
    try:
        return _parse_json_text(joined)
    except json.JSONDecodeError:
        return joined


def _decode_text_result(result: str) -> Any:
    """Decode a bare string result as JSON, or as bounded text."""

    try:
        return _parse_json_text(result)
    except json.JSONDecodeError:
        if len(result) > _MAX_TOOL_PAYLOAD:
            raise ValueError("payload_too_large") from None
        return result


def _decode_tool_result(result: Any) -> Any:
    decoded = _decode_structured_attributes(result)
    if decoded is not _UNDECODED:
        return decoded
    content = getattr(result, "content", None)
    if isinstance(content, list):
        decoded = _decode_content_list(content)
        if decoded is not _UNDECODED:
            return decoded
    if isinstance(result, str):
        return _decode_text_result(result)
    _validate_tool_payload_bounds(result)
    return result


def _extract_delegation_envelope(value: Any) -> tuple[Any, str]:
    """Validate the current outer ``graph_orchestrate`` contract exactly."""

    # Depending on the negotiated MCP result schema, a string-returning tool's
    # structured payload may itself be the JSON string rather than the
    # ``{"result": ...}`` object below.
    if isinstance(value, str):
        value = _parse_json_text(value)
    # FastMCP exposes a tool annotated as returning ``str`` through the current
    # structured-result envelope.  Unwrap that wire-level representation once;
    # the contained GraphOS object is still validated against the sole strict
    # delegation contract below.
    if (
        isinstance(value, dict)
        and set(value) == {"result"}
        and isinstance(value["result"], str)
    ):
        value = _parse_json_text(value["result"])
    if not isinstance(value, dict):
        raise DelegationContractError("delegation_response_not_object")
    allowed = {"output", "run_id", "mermaid"}
    if not {"output", "run_id"}.issubset(value) or set(value) - allowed:
        raise DelegationContractError("delegation_response_schema_invalid")
    run_id = str(value["run_id"] or "")
    if not is_run_id(run_id):
        raise DelegationContractError("delegation_run_id_invalid")
    return value["output"], run_id


def _semantic_from_delegation_output(output: Any) -> SemanticOutput:
    if isinstance(output, str):
        try:
            output = _parse_json_text(output)
        except json.JSONDecodeError as exc:
            raise DelegationContractError("delegation_output_not_json") from exc
    return SemanticOutput.model_validate(output)


def _extract_semantic_payload(value: Any) -> tuple[SemanticOutput, str]:
    """Validate the outer envelope and closed semantic response contract."""

    output, run_id = _extract_delegation_envelope(value)
    return _semantic_from_delegation_output(output), run_id


def _usage_counts(run_result: Any) -> dict[str, int]:
    usage_fn = getattr(run_result, "usage", None)
    usage = usage_fn() if callable(usage_fn) else usage_fn
    if usage is None:
        return {}

    def count(*names: str) -> int:
        for name in names:
            raw = getattr(usage, name, None)
            if raw is not None:
                try:
                    return max(0, int(raw))
                except (TypeError, ValueError):
                    return 0
        return 0

    prompt = count("input_tokens", "request_tokens", "prompt_tokens")
    response = count("output_tokens", "response_tokens", "completion_tokens")
    total = count("total_tokens") or prompt + response
    return {"prompt": prompt, "response": response, "total": total}


async def _call_tool(
    client: Any, name: str, arguments: dict[str, Any], timeout: float
) -> Any:
    result = await asyncio.wait_for(
        client.call_tool(name, arguments), timeout=max(1.0, timeout)
    )
    # MCP's wire/model field is ``isError``. Some client adapters expose the
    # snake-case convenience alias; honor both so a child failure can never be
    # decoded as a successful (usually empty) payload.
    if bool(getattr(result, "isError", False)) or bool(
        getattr(result, "is_error", False)
    ):
        raise ValidationChildToolError("mcp_tool_error")
    return _decode_tool_result(result)
