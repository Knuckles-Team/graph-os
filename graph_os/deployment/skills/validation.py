"""Validate the GraphOS-owned bundled skill suite and synthetic route matrix.

The AU architecture owner manifest remains AU-owned.  Its verification and
candidate projection are consumed through a typed, fail-closed import seam.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any, Protocol

import yaml

from graph_os.deployment.skills import BUNDLED_SKILLS

SKILLS_ROOT = Path(__file__).resolve().parent
FORWARD_MATRIX = SKILLS_ROOT / "runtime_validation.yaml"
EXPECTED_SKILLS = frozenset(BUNDLED_SKILLS)

_REQUIRED_WORKFLOW_TERMS: dict[str, frozenset[str]] = {
    "agent-utilities-deployment": frozenset(
        {"migration", "persisted-format", "upgrade"}
    ),
    "graph-engine-and-modalities": frozenset(
        {"sql", "sparql", "reasoning", "consensus", "tenancy", "rbac", "administration"}
    ),
    "graph-runtime-and-governance": frozenset({"troubleshoot"}),
}
_REQUIRED_WORKFLOW_ROUTES: dict[str, frozenset[str]] = {
    "graph-engine-and-modalities": frozenset(
        {
            "engine_admin",
            "engine_consensus",
            "engine_query",
            "engine_rbac",
            "engine_rdf",
            "engine_reasoning",
            "engine_tenants",
        }
    )
}
_PRIVATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "absolute filesystem path",
        re.compile(
            r"(?:^|[\s`'\"])/(?:home|Users|mnt|root|tmp|var|srv|opt|etc|workspace)/"
        ),
    ),
    ("Windows filesystem path", re.compile(r"\b[A-Za-z]:\\\\")),
    ("UNC filesystem path", re.compile(r"\\\\[^\s\\]+\\[^\s\\]+")),
    ("home-relative filesystem path", re.compile(r"~[/\\\\]")),
    ("literal IPv4 address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    (
        "network endpoint",
        re.compile(
            r"\b(?:https?|wss?|tcp|udp|ssh|unix)://(?!ex/|example\.(?:com|net|org|test)\b|…)",
            re.IGNORECASE,
        ),
    ),
    (
        "private endpoint suffix",
        re.compile(r"\.(?:arpa|internal|local|corp|lan)\b", re.IGNORECASE),
    ),
    (
        "email address",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    ("embedded secret reference", re.compile(r"\b(?:vault|secret)://", re.IGNORECASE)),
    ("private key material", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
)
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_AUXILIARY_DOC = re.compile(r"^(?:README|INSTALL|CHANGELOG)(?:\..*)?$", re.IGNORECASE)
_IMPERATIVE_STEP = re.compile(
    r"^(?:\d+\.\s+|-\s+)(?:Add|Ask|Assign|Attach|Avoid|Bound|Capture|Change|"
    r"Check|Choose|Compare|Confirm|Create|Decide|Define|Distinguish|Execute|"
    r"Extend|Generate|Identify|Include|Inspect|Keep|Label|List|Map|Mark|Never|"
    r"Parameterize|Persist|Prefer|Preserve|Present|Preview|Put|Query|Read|Record|"
    r"Reject|Report|Require|Resolve|Re-run|Reuse|Run|Sample|Search|Select|Separate|"
    r"Specify|Start|State|Stop|Track|Treat|Update|Use|Validate|Verify)\b",
    re.MULTILINE,
)


class _SidecarMeta(Protocol):
    tier: str
    wraps: tuple[str, ...]
    errors: tuple[str, ...]


class _SidecarParser(Protocol):
    def __call__(self, path: Path, *, skill_name: str) -> _SidecarMeta: ...


class _OwnerManifestPort(Protocol):
    def load_architecture_owner_manifest(self) -> dict[str, Any]: ...
    def architecture_candidate_from_owner_manifest(
        self, manifest: dict[str, Any], *, component_id: str | None = None
    ) -> dict[str, Any]: ...


def _sidecar_parser() -> _SidecarParser:
    """Temporary AU parser seam; fail closed if its public port is unavailable."""
    module = importlib.import_module("agent_utilities.mcp.skill_coverage")
    return module.parse_graph_os_sidecar


def _owner_manifest_port() -> _OwnerManifestPort:
    """AU retains source authority for RF-021 owner declarations."""
    return importlib.import_module("agent_utilities.skills.validation")


def _relative(path: Path) -> str:
    return path.relative_to(SKILLS_ROOT).as_posix()


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    return (data if isinstance(data, dict) else {}), match.group(2)


def _validate_skill_frontmatter(name: str, frontmatter: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(frontmatter) != {"name", "description", "skill_type"}:
        errors.append(
            f"{name}: SKILL.md frontmatter must contain only name, description, and skill_type"
        )
    if frontmatter.get("name") != name:
        errors.append(f"{name}: frontmatter name must match directory")
    if frontmatter.get("skill_type") != "skill":
        errors.append(f"{name}: frontmatter skill_type must be 'skill'")
    if not str(frontmatter.get("description") or "").strip():
        errors.append(f"{name}: description is empty")
    return errors


def _validate_skill_body(name: str, skill_md: Path, body: str) -> list[str]:
    errors: list[str] = []
    if len(skill_md.read_text(encoding="utf-8").splitlines()) >= 500:
        errors.append(f"{name}: SKILL.md must remain under 500 lines")
    if "TODO" in body:
        errors.append(f"{name}: unresolved TODO in SKILL.md")
    if "## Workflow" not in body:
        errors.append(f"{name}: SKILL.md must contain a Workflow section")
    if len(_IMPERATIVE_STEP.findall(body)) < 3:
        errors.append(f"{name}: body must contain at least three imperative steps")
    if not re.search(r"\beconom(?:y|ical)\b", body, re.IGNORECASE):
        errors.append(f"{name}: missing economy-model guidance")
    if "direct" not in body.lower() or "delegat" not in body.lower():
        errors.append(f"{name}: must explain direct and delegated execution")
    return errors


def _validate_skill_workflow_terms(name: str, skill_md: Path) -> list[str]:
    lowered = skill_md.read_text(encoding="utf-8").lower()
    missing = sorted(
        term
        for term in _REQUIRED_WORKFLOW_TERMS.get(name, frozenset())
        if term not in lowered
    )
    return (
        [f"{name}: missing retained workflow coverage terms {missing}"]
        if missing
        else []
    )


def _validate_skill_openai_sidecar(name: str, path: Path) -> list[str]:
    if not path.is_file():
        return [f"{name}: missing agents/openai.yaml"]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    interface = data.get("interface") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or set(data) != {"interface"}
        or not isinstance(interface, dict)
    ):
        return [f"{name}: OpenAI sidecar must contain only interface"]
    errors: list[str] = []
    required = {"display_name", "short_description", "default_prompt"}
    if set(interface) != required:
        errors.append(f"{name}: OpenAI interface keys must be {sorted(required)}")
    short = str(interface.get("short_description") or "")
    if not 25 <= len(short) <= 64:
        errors.append(f"{name}: short_description must be 25-64 characters")
    if f"${name}" not in str(interface.get("default_prompt") or ""):
        errors.append(f"{name}: default_prompt must mention ${name}")
    return errors


def _validate_skill_graph_os_sidecar(
    name: str, path: Path, parser: _SidecarParser
) -> list[str]:
    if not path.is_file():
        return [f"{name}: missing agents/graph-os.yaml"]
    meta = parser(path, skill_name=name)
    errors = [f"{name}: {error}" for error in meta.errors]
    missing = sorted(_REQUIRED_WORKFLOW_ROUTES.get(name, frozenset()) - set(meta.wraps))
    if missing:
        errors.append(f"{name}: missing retained workflow routes {missing}")
    return errors


def _validate_skill_files(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if _AUXILIARY_DOC.fullmatch(path.name):
            errors.append(
                f"{_relative(path)}: auxiliary skill documentation is forbidden"
            )
        raw = path.read_text(encoding="utf-8")
        for label, pattern in _PRIVATE_PATTERNS:
            if pattern.search(raw):
                errors.append(f"{_relative(path)}: contains {label}")
    return errors


def _validate_skill(skill_dir: Path, parser: _SidecarParser) -> list[str]:
    name = skill_dir.name
    if not _SKILL_NAME.fullmatch(name):
        return [f"{name}: directory name must use lowercase hyphenation"]
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{name}: missing SKILL.md"]
    frontmatter, body = _frontmatter(skill_md)
    return [
        *_validate_skill_frontmatter(name, frontmatter),
        *_validate_skill_body(name, skill_md, body),
        *_validate_skill_workflow_terms(name, skill_md),
        *_validate_skill_openai_sidecar(name, skill_dir / "agents" / "openai.yaml"),
        *_validate_skill_graph_os_sidecar(
            name, skill_dir / "agents" / "graph-os.yaml", parser
        ),
        *_validate_skill_files(skill_dir),
    ]


def _forward_matrix_domain_wraps(
    parser: _SidecarParser,
) -> tuple[dict[str, set[str]], set[str]]:
    domain_wraps: dict[str, set[str]] = {}
    for skill in EXPECTED_SKILLS:
        meta = parser(
            SKILLS_ROOT / skill / "agents" / "graph-os.yaml", skill_name=skill
        )
        if meta.tier == "domain" and not meta.errors:
            domain_wraps[skill] = set(meta.wraps)
    all_wraps = set().union(*domain_wraps.values()) if domain_wraps else set()
    return domain_wraps, all_wraps


def _validate_forward_matrix_defaults(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 2:
        errors.append("forward matrix: schema_version must be 2")
    defaults = data.get("runtime_defaults")
    if not isinstance(defaults, dict):
        return [*errors, "forward matrix: runtime_defaults must be a mapping"]
    for field, lower, upper in (
        ("max_steps", 1, 8),
        ("token_budget", 256, 16384),
        ("trace_timeout_seconds", 1, 60),
    ):
        value = defaults.get(field)
        if type(value) is not int or not lower <= value <= upper:
            errors.append(
                f"forward matrix: {field} must be between {lower} and {upper}"
            )
    if defaults.get("sequential") is not True:
        errors.append("forward matrix: runtime validation must be sequential")
    return errors


def _validate_architecture_owner_manifest(data: dict[str, Any]) -> list[str]:
    """Bind development cases to AU's source-owned declaration."""
    try:
        owner = _owner_manifest_port()
        manifest = owner.load_architecture_owner_manifest()
    except (ImportError, AttributeError, ValueError) as exc:
        return [
            f"architecture owner manifest: unavailable ({type(exc).__name__}: {exc})"
        ]
    errors: list[str] = []
    cases = data.get("cases")
    if not isinstance(cases, list):
        return errors
    for case in cases:
        if (
            not isinstance(case, dict)
            or case.get("skill") != "agent-utilities-development"
        ):
            continue
        candidate = case.get("architecture_candidate")
        if not isinstance(candidate, dict):
            errors.append(f"{case.get('id')}: architecture_candidate must be a mapping")
            continue
        try:
            expected = owner.architecture_candidate_from_owner_manifest(
                manifest, component_id=str(candidate.get("component_id") or "")
            )
        except (AttributeError, ValueError) as exc:
            errors.append(
                f"{case.get('id')}: architecture_candidate component is not declared ({type(exc).__name__})"
            )
            continue
        if candidate != expected:
            errors.append(
                f"{case.get('id')}: architecture_candidate is not bound to owner manifest"
            )
    return errors


def _validate_case(
    case: Any,
    ids: set[str],
    seen: set[tuple[str, str]],
    domain_wraps: dict[str, set[str]],
    all_wraps: set[str],
) -> list[str]:
    if not isinstance(case, dict):
        return ["forward matrix: every case must be a mapping"]
    case_id = str(case.get("id") or "")
    skill = str(case.get("skill") or "")
    mode = str(case.get("mode") or "")
    errors: list[str] = []
    if not case_id or case_id in ids:
        errors.append(f"forward matrix: duplicate or empty id {case_id!r}")
    ids.add(case_id)
    if skill not in EXPECTED_SKILLS:
        errors.append(f"{case_id}: unknown skill {skill!r}")
    if mode not in {"direct", "delegated"}:
        errors.append(f"{case_id}: mode must be direct or delegated")
    seen.add((skill, mode))
    if "skill_path" in case:
        errors.append(f"{case_id}: filesystem skill_path is forbidden")
    task = str(case.get("task") or "")
    if f"${skill}" not in task or f"skill://{skill}" not in task:
        errors.append(
            f"{case_id}: task must identify the skill by neutral skill:// reference"
        )
    if case.get("model_class") not in {"economy", "standard"}:
        errors.append(f"{case_id}: unsupported model_class")
    if case.get("read_only") is not True:
        errors.append(f"{case_id}: validation cases must be read-only")
    if skill == "agent-utilities-development":
        if not isinstance(case.get("architecture_candidate"), dict):
            errors.append(f"{case_id}: architecture_candidate must be a mapping")
    elif "architecture_candidate" in case:
        errors.append(f"{case_id}: architecture_candidate is development-only")
    routes = case.get("expected_routes")
    if (
        not isinstance(routes, list)
        or not routes
        or any(not isinstance(route, str) for route in routes)
    ):
        errors.append(f"{case_id}: expected_routes must be non-empty")
    elif skill in domain_wraps:
        invalid = set(routes) - domain_wraps[skill] - {"graph_orchestrate"}
        if invalid:
            errors.append(
                f"{case_id}: routes not owned by the domain skill: {sorted(invalid)}"
            )
        if mode == "direct" and "graph_orchestrate" in routes:
            errors.append(f"{case_id}: a direct case cannot use graph_orchestrate")
        if mode == "delegated" and "graph_orchestrate" not in routes:
            errors.append(
                f"{case_id}: a delegated domain case must use graph_orchestrate"
            )
    elif mode == "delegated" and "graph_orchestrate" not in routes:
        errors.append(f"{case_id}: delegated cases must use graph_orchestrate")
    allowed = case.get("allowed_tools")
    if not isinstance(allowed, list):
        errors.append(f"{case_id}: allowed_tools must be a list")
    elif mode == "direct":
        if allowed:
            errors.append(f"{case_id}: direct semantic cases cannot receive tools")
    else:
        if not allowed or any(
            not isinstance(tool, str) or not tool for tool in allowed
        ):
            errors.append(f"{case_id}: delegated allowed_tools must be non-empty")
        else:
            if len(allowed) != len(set(allowed)):
                errors.append(f"{case_id}: allowed_tools must not contain duplicates")
            if allowed != sorted(allowed):
                errors.append(f"{case_id}: allowed_tools must be sorted")
            unknown = set(allowed) - all_wraps
            if unknown:
                errors.append(
                    f"{case_id}: allowed_tools contain unknown Graph-OS verbs: {sorted(unknown)}"
                )
            if "graph_orchestrate" in allowed:
                errors.append(
                    f"{case_id}: delegated child cannot recursively orchestrate"
                )
    return errors


def _validate_forward_matrix(parser: _SidecarParser) -> list[str]:
    try:
        raw = FORWARD_MATRIX.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return [f"forward matrix: unreadable ({type(exc).__name__})"]
    if not isinstance(data, dict):
        return ["forward matrix: must be a mapping"]
    domain_wraps, all_wraps = _forward_matrix_domain_wraps(parser)
    errors = [
        *_validate_forward_matrix_defaults(data),
        *_validate_architecture_owner_manifest(data),
    ]
    cases = data.get("cases")
    if not isinstance(cases, list):
        return [*errors, "forward matrix: cases must be a list"]
    seen: set[tuple[str, str]] = set()
    ids: set[str] = set()
    for case in cases:
        errors.extend(_validate_case(case, ids, seen, domain_wraps, all_wraps))
    expected_pairs = {
        (skill, mode) for skill in EXPECTED_SKILLS for mode in ("direct", "delegated")
    }
    if seen != expected_pairs:
        errors.append(
            "forward matrix: each skill needs one direct and one delegated case"
        )
    privacy = data.get("privacy_assertions") or {}
    if not isinstance(privacy, dict):
        privacy = {}
    required = {
        "credential",
        "internal_endpoint",
        "local_filesystem_path",
        "personal_name",
        "raw_model_output",
        "raw_trace_id",
    }
    if set(privacy.get("forbid_persisted") or []) != required:
        errors.append("forward matrix: privacy assertions are incomplete")
    if privacy.get("require_reference_scheme") != "skill://":
        errors.append("forward matrix: skill:// references must be required")
    if privacy.get("require_synthetic_inputs") is not True:
        errors.append("forward matrix: synthetic inputs must be required")
    if privacy.get("require_metadata_only_observability") is not True:
        errors.append("forward matrix: metadata-only observability must be required")
    for label, pattern in _PRIVATE_PATTERNS:
        if pattern.search(raw):
            errors.append(f"forward matrix: contains {label}")
    return errors


def validate() -> list[str]:
    """Return deterministic errors for this repository's bundled skill suite."""
    actual = {
        path.parent.name for path in SKILLS_ROOT.glob("*/SKILL.md") if path.is_file()
    }
    errors: list[str] = []
    if len(EXPECTED_SKILLS) != 12:
        errors.append("canonical taxonomy must contain exactly 12 workflow skills")
    if actual != EXPECTED_SKILLS:
        errors.append(
            f"skill inventory mismatch: missing={sorted(EXPECTED_SKILLS - actual)} unexpected={sorted(actual - EXPECTED_SKILLS)}"
        )
    nested = [
        path
        for path in SKILLS_ROOT.rglob("SKILL.md")
        if path.parent.parent != SKILLS_ROOT
        and path.relative_to(SKILLS_ROOT).parts[0] in EXPECTED_SKILLS
    ]
    if nested:
        errors.append(
            "nested bundled skills are not allowed: "
            + ", ".join(_relative(path) for path in nested)
        )
    try:
        parser = _sidecar_parser()
    except (ImportError, AttributeError) as exc:
        return [
            *errors,
            f"GraphOS sidecar parser: unavailable ({type(exc).__name__}: {exc})",
        ]
    for name in sorted(actual):
        errors.extend(_validate_skill(SKILLS_ROOT / name, parser))
    errors.extend(_validate_forward_matrix(parser))
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("GraphOS bundled skill validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        f"GraphOS bundled skill validation OK — {len(EXPECTED_SKILLS)} skills, {2 * len(EXPECTED_SKILLS)} synthetic forward cases."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
