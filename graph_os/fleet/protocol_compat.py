#!/usr/bin/python
from __future__ import annotations

"""Compatibility boundary for the fastmcp-4 / MCP SDK v2 runtime.

CONCEPT:AU-ECO.mcp.protocol-compat-bridge

GraphOS targets `fastmcp>=4.0.0b1` directly in `pyproject.toml`, which
transitively uses the MCP Python SDK v2 line. Earlier
Pydantic-AI releases read the SDK's legacy camelCase fields and assumed the
initialize handshake, so AU briefly carried process-global aliases, a copied
`MCPToolset` method body, and an explicit legacy-mode pin.

The supported contract is now `pydantic-ai-slim==2.29.0`. Its upstream
`pydantic_ai.mcp` implementation reads either SDK field spelling through its own
`_mcp_compat` helpers, handles modern `server/discover` sessions, and imports the
FastMCP protocol-error alias. The old method monkeypatch and field aliases are
therefore removed. `install_mcp_v2_bridge()` remains as a compatibility-preserving
entrypoint that validates the exact installed contract and fails closed on drift;
it never silently disables MCP or mutates third-party classes. The
`force_legacy_protocol_mode()` helper remains an explicit AU policy for call sites
that require initialize-handshake semantics while the fleet transitions.

The SDK name/exception resolvers below are still used by AU's own MCP resilience
and authorization paths, which support both SDK generations. Remove this module's
call sites only when those consumers no longer need that mixed-generation boundary.

"""

import importlib
import importlib.metadata
import warnings
from types import ModuleType
from typing import Any

_installed = False

#: Module paths that carry the MCP wire-protocol types, newest first. The MCP SDK
#: v2 line moved `mcp.types` OUT of the `mcp` distribution into a standalone
#: `mcp_types` one, so `mcp.types` simply does not exist on those installs while
#: `mcp_types` does; SDK v1 is the mirror image. Both are the SAME protocol-type
#: namespace (`Tool`, `TextContent`, `SamplingMessage`, …), so code that reads it
#: must bind whichever module the INSTALLED SDK actually ships.
_TYPES_MODULE_NAMES = ("mcp.types", "mcp_types")

#: Names `mcp.shared.exceptions` uses for the MCP protocol error, newest first.
#: SDK v2 (`mcp>=2.0.0`) renamed `McpError` -> `MCPError`; SDK v1 still ships the
#: old spelling. Both are the SAME protocol error, so code that catches it must
#: bind whichever one the INSTALLED SDK actually exposes.
_PROTOCOL_ERROR_NAMES = ("MCPError", "McpError")


def mcp_protocol_error() -> type[BaseException]:
    """Return the MCP protocol-error class for the *installed* MCP SDK line.

    `mcp.shared.exceptions.McpError` (SDK v1) was renamed `MCPError` in SDK v2.
    A hard `from mcp.shared.exceptions import MCPError` therefore raises
    `ImportError` on every SDK v1 install — and because the multiplexer imports
    the child-resilience layer at module scope, that ImportError takes the whole
    fleet loader down with it (CONCEPT:AU-ECO.mcp.protocol-compat-bridge).

    This resolves the class by name instead. It is deliberately NOT a
    `try/except ImportError` that falls back to a benign default: binding the
    name to `()` or `Exception` would silently break session-death detection for
    the entire child fleet. If neither spelling exists the SDK is unusable here,
    so this raises loudly.
    """
    from mcp.shared import exceptions as mcp_exceptions

    for name in _PROTOCOL_ERROR_NAMES:
        candidate = getattr(mcp_exceptions, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            return candidate
    raise ImportError(
        "mcp.shared.exceptions exposes neither 'MCPError' (MCP SDK v2) nor "
        "'McpError' (MCP SDK v1); the MCP child-resilience layer cannot detect a "
        "terminated child session without it."
    )


def mcp_protocol_exception(code: int, message: str, data: Any = None) -> BaseException:
    """Construct the installed SDK's protocol error without signature guessing.

    SDK v2's ``MCPError`` accepts ``(code, message, data)`` directly. SDK v1's
    ``McpError`` instead accepts one ``mcp.types.ErrorData`` model. Select from
    the same exported class name used by :func:`mcp_protocol_error`; do not
    retry on ``TypeError``, because that would hide a real constructor defect.
    """
    from mcp.shared import exceptions as mcp_exceptions

    error_type = mcp_protocol_error()
    if getattr(mcp_exceptions, "MCPError", None) is error_type:
        return error_type(code, message, data)
    error_data = mcp_types_module().ErrorData(code=code, message=message, data=data)
    return error_type(error_data)


def mcp_types_module() -> ModuleType:
    """Return the MCP wire-protocol types module for the *installed* MCP SDK line.

    `mcp.types` (SDK v1) became the standalone `mcp_types` distribution in SDK v2.
    A hard `from mcp import types` therefore raises `ImportError` on every SDK v2
    install — and because `eunomia_principal` is imported at module scope by
    `server_factory._configure_middleware`, whose Eunomia leg is deliberately
    **fail-closed** (`sys.exit(1)` — an authorization middleware that cannot load
    must not be skipped), that ImportError takes the WHOLE server down before it
    serves anything. Observed live: `aris-mcp` and `freshrss-mcp` crash-looped for
    9 days on exactly this, because their images ship `fastmcp 4.0.0a1` +
    MCP SDK v2 while the rest of the fleet is still on fastmcp 3.x / SDK v1
    (CONCEPT:AU-ECO.mcp.protocol-compat-bridge).

    This resolves the module by import path instead. Like `mcp_protocol_error()`
    it is deliberately NOT a `try/except ImportError` that falls back to a benign
    stand-in: binding this name to a stub would make every `isinstance` check
    against a protocol type silently False, which is a permission-shaped failure
    in an authorization middleware. If neither module exists the SDK is unusable
    here, so this raises loudly.
    """
    for name in _TYPES_MODULE_NAMES:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise ImportError(
        "neither 'mcp.types' (MCP SDK v1) nor 'mcp_types' (MCP SDK v2) is "
        "importable; the MCP protocol-type surface is unavailable, so tool / "
        "prompt / resource identity cannot be resolved."
    )


def install_mcp_v2_bridge() -> None:
    """Verify the exact Pydantic-AI MCP contract used by AU.

    Pydantic-AI 2.29 natively handles both MCP SDK field spellings and modern
    FastMCP sessions, so the old process-global aliases and copied method body
    are intentionally gone. The public function remains at all historical
    construction sites as a fail-closed version gate: a stale or ambient
    installation must never silently disable the MCP compatibility contract.
    It is idempotent and does not mutate third-party classes.
    """
    global _installed
    if _installed:
        return
    _install_pydantic_ai_v2_read_bridge()

    _installed = True


#: The exact Pydantic-AI release whose native MCPToolset surface AU supports.
#: This is the single contract source consumed by the fleet parity checker;
#: package extras, locks, image inputs, and runtime verification must agree.
_PYDANTIC_AI_CONTRACT_VERSION = "2.29.0"

# Backwards-compatible name for integrations that imported the old private
# sentinel while the method body was locally patched. It deliberately aliases
# the one canonical literal above rather than introducing a second version.
_PATCHED_PYDANTIC_AI_VERSION = _PYDANTIC_AI_CONTRACT_VERSION

_toolset_reads_patched = False


def _install_pydantic_ai_v2_read_bridge() -> None:
    """Verify the native Pydantic-AI MCPToolset contract without monkeypatching it.

    Pydantic-AI 2.29.0's upstream `MCPToolset` now owns the complete surface:
    `_mcp_compat` reads current snake_case and legacy camelCase model fields,
    `__aenter__` handles both initialize-era and modern sessions, and tool-call
    errors use FastMCP's SDK-neutral alias. This function deliberately does not
    assign to third-party methods or install process-global aliases. The exact
    version check is the fail-closed guard: changing the package version requires
    re-diffing those upstream methods and updating this contract source and its
    tests together, rather than silently disabling the compatibility layer.
    """
    global _toolset_reads_patched
    if _toolset_reads_patched:
        return

    try:
        installed_version = importlib.metadata.version("pydantic-ai-slim")
    except importlib.metadata.PackageNotFoundError:  # noqa: BLE001 — pydantic-ai-slim (the `[mcp]` extra) is genuinely optional; its absence means there is nothing for this bridge to patch, not a failure to surface
        return

    if installed_version != _PATCHED_PYDANTIC_AI_VERSION:
        raise RuntimeError(
            "agent-utilities: the Pydantic-AI MCP contract requires "
            f"pydantic-ai-slim=={_PYDANTIC_AI_CONTRACT_VERSION}, but "
            f"{installed_version} is installed. Refusing to construct MCP "
            "toolsets with an unverified compatibility surface; re-lock the AU "
            "contract or re-diff the upstream methods before changing the pin."
        )

    _toolset_reads_patched = True


def force_legacy_protocol_mode(toolset: Any) -> Any:
    """Pin an `MCPToolset` (or a list of toolsets) to `mode="legacy"` before first use.

    Unwraps `WrapperToolset.wrapped` (e.g. `PrefixedToolset`, which
    `pydantic_ai.mcp.load_mcp_toolsets` returns) to find the underlying
    `MCPToolset.client`. Toolsets that aren't MCP-backed (no `.client.mode`) are
    left untouched. Returns `toolset` unchanged for chaining.

    Unwrapping checks `isinstance(target, WrapperToolset)` rather than
    `hasattr(target, "wrapped")`: a bare `unittest.mock.MagicMock` (used
    throughout this package's own test suite to stand in for a toolset)
    answers `hasattr(..., "wrapped")` as `True` for EVERY attribute name and
    hands back a distinct child mock on every access, so a duck-typed unwrap
    loop never terminates against one. `isinstance` only matches the real
    toolset wrapper class.
    """
    if isinstance(toolset, (list, tuple)):
        for item in toolset:
            force_legacy_protocol_mode(item)
        return toolset

    from pydantic_ai.toolsets import WrapperToolset

    target = toolset
    while isinstance(target, WrapperToolset):
        target = target.wrapped

    client = getattr(target, "client", None)
    if client is not None and hasattr(client, "mode"):
        client.mode = "legacy"

    return toolset


_MCP_FIELD_MISSING = object()


def _read_mcp_field(value: Any, current_name: str, legacy_name: str) -> Any:
    """Read an MCP field from either SDK generation, failing closed if absent.

    MCP SDK v1 exposed camelCase model fields while SDK v2 moved them to
    snake_case. Pydantic-AI 2.29 handles this internally; this small fail-closed
    resolver remains for AU-owned SDK-neutral resilience tests and callers that
    receive a model from either generation. It never installs a process-global
    alias that could hide a malformed response. If neither field exists, the
    connection is invalid and the resulting ``AttributeError`` remains visible.
    """
    current = getattr(value, current_name, _MCP_FIELD_MISSING)
    if current is not _MCP_FIELD_MISSING:
        return current
    legacy = getattr(value, legacy_name, _MCP_FIELD_MISSING)
    if legacy is not _MCP_FIELD_MISSING:
        return legacy
    raise AttributeError(
        f"MCP object {type(value).__name__} exposes neither "
        f"{current_name!r} (current SDK) nor {legacy_name!r} (legacy SDK)"
    )


def _extra_marker_environment(extra: str) -> dict[str, str] | None:
    """Build a PEP 508 marker environment for one optional extra."""
    from packaging.markers import default_environment

    environment: dict[str, str] = {}
    for name, value in default_environment().items():
        if not isinstance(value, str):
            return None
        environment[name] = value
    environment["extra"] = extra
    return environment


def _parse_requirement(raw: str) -> Any | None:
    """Parse one metadata requirement, treating malformed metadata as absent."""
    from packaging.requirements import Requirement

    try:
        return Requirement(raw)
    except Exception:  # noqa: BLE001 - malformed installed metadata is skipped
        return None


def _declared_extra_floor(distribution: str, package: str, extra: str) -> Any | None:
    """Return the ``package`` requirement `distribution` declares under `extra`.

    Reads straight from `distribution`'s OWN installed metadata (PEP 508 extra
    markers on ``importlib.metadata.requires()``), never a separately-parsed
    ``pyproject.toml`` — so this is accurate for a dev checkout AND a deployed
    wheel, and can never drift from what the package actually declares.
    """
    try:
        reqs = importlib.metadata.requires(distribution) or []
    except importlib.metadata.PackageNotFoundError:
        return None
    env = _extra_marker_environment(extra)
    if env is None:
        return None
    for raw in reqs:
        req = _parse_requirement(raw)
        if (
            req is not None
            and req.name.lower() == package.lower()
            and (req.marker is None or req.marker.evaluate(env))
        ):
            return req
    return None


def _declared_runtime_floor(distribution: str, package: str) -> Any | None:
    """Return a base runtime requirement from installed distribution metadata."""

    try:
        reqs = importlib.metadata.requires(distribution) or []
    except importlib.metadata.PackageNotFoundError:
        return None
    env = _extra_marker_environment("")
    if env is None:
        return None
    for raw in reqs:
        req = _parse_requirement(raw)
        if (
            req is not None
            and req.name.lower() == package.lower()
            and (req.marker is None or req.marker.evaluate(env))
        ):
            return req
    return None


def _source_shadow_floor(package: str) -> tuple[Any | None, str | None]:
    """Return the ``package`` floor declared by the SOURCE tree actually imported.

    CONCEPT:AU-ECO.mcp.protocol-compat-bridge

    Closes the D-OB-18 blind spot in :func:`check_mcp_sdk_floor`. Installed
    ``.dist-info`` metadata is written once, at install time. Every graph-os
    deployment then bind-mounts a *fresher* copy of this package's source over the
    installed one (``PYTHONPATH=/au`` shadowing the image's editable install), so the
    code that actually runs can declare a different floor than the metadata records —
    which is precisely how a pod ran fastmcp-4-targeted source on a fastmcp-3 runtime
    while every metadata-only check reported green.

    So: read the floor from the ``pyproject.toml`` sitting next to the imported
    package, when there is one. Returns ``(requirement, path)``; ``(None, None)`` when
    no source manifest is reachable (a plain wheel install — the normal case, where
    installed metadata is already authoritative).
    """
    import tomllib
    from pathlib import Path

    from packaging.requirements import InvalidRequirement, Requirement

    import graph_os

    origin = getattr(graph_os, "__file__", None)
    if not origin:
        return None, None
    manifest = Path(origin).resolve().parent.parent / "pyproject.toml"
    if not manifest.is_file():
        return None, None
    try:
        declared = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        warnings.warn(
            f"graph-os: could not read the source manifest {manifest} to "
            f"cross-check the declared '{package}' floor: {type(exc).__name__}",
            RuntimeWarning,
            stacklevel=2,
        )
        return None, None
    raw_reqs = declared.get("project", {}).get("dependencies", [])
    for raw in raw_reqs:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            req = None
        if req is not None and req.name.lower() == package.lower():
            return req, str(manifest)
    return None, str(manifest)


def _requirement_problems(
    package: str, installed: str | None, requirement: Any | None
) -> list[str]:
    """Return fixed-vocabulary problems for one installed SDK requirement."""
    from packaging.version import InvalidVersion

    if installed is None:
        return [f"{package} is not installed"]
    if requirement is None:
        return []
    try:
        satisfies = requirement.specifier.contains(installed, prereleases=True)
    except InvalidVersion:
        return [f"{package} reports an invalid version {installed!r}"]
    if satisfies:
        return []
    return [
        f"{package} {installed} does not satisfy the declared floor "
        f"'{requirement.specifier}'"
    ]


def _mcp_requirement() -> Any | None:
    """Find the transitive ``mcp`` floor from fastmcp-slim metadata."""
    for extra in ("client", "server", "mcp"):
        requirement = _declared_extra_floor("fastmcp-slim", "mcp", extra)
        if requirement is not None:
            return requirement
    return None


def _source_floor_reconciliation(
    distribution: str, metadata_requirement: Any
) -> tuple[Any, str | None]:
    """Prefer an imported source manifest floor and report metadata drift."""
    shadow_requirement, manifest = _source_shadow_floor("fastmcp")
    if shadow_requirement is None or str(shadow_requirement.specifier) == str(
        metadata_requirement.specifier
    ):
        return metadata_requirement, None
    divergence = (
        f"source/installed divergence: the imported source ({manifest}) declares "
        f"fastmcp '{shadow_requirement.specifier}' while the installed "
        f"{distribution} metadata declares '{metadata_requirement.specifier}' — this "
        "environment was provisioned from a different revision than the source it "
        "now runs"
    )
    return shadow_requirement, divergence


def check_mcp_sdk_floor(distribution: str = "graph-os") -> dict[str, Any]:
    """Compare installed MCP packages against GraphOS runtime requirements.

    CONCEPT:AU-ECO.mcp.protocol-compat-bridge

    Closes D-ISR-2: nothing previously asserted that the runtime's installed
    `mcp`/`fastmcp` matched `pyproject.toml`'s declared floor, so v2-targeted
    source (this module's own bridges) could ship and only fail at import
    time in production — the exact failure `child_resilience.py` hit when a
    deployed pod resolved `mcp` 1.29.0 against `fastmcp>=4.0.0b1`-targeted
    code. This makes that mismatch fail loudly here (wired into
    `agent-utilities doctor` and a CI regression test) instead of surfacing
    only as a swallowed `ImportError` deep in a child transport.

    The `fastmcp` floor is read from `distribution`'s base runtime metadata;
    the `mcp` floor is derived transitively from
    `fastmcp-slim`'s own installed metadata (fastmcp's real runtime
    dependency) rather than a second, hand-maintained copy of the same
    constraint.

    Returns ``{"ok": bool | None, "detail": str}``. ``ok=None`` means the
    check could not run (the runtime floor is not declared at all, e.g. a
    malformed build) — distinct from a real
    mismatch.
    """
    fastmcp_requirement = _declared_runtime_floor(distribution, "fastmcp")
    if fastmcp_requirement is None:
        return {
            "ok": None,
            "detail": f"{distribution} declares no runtime 'fastmcp' floor",
        }
    try:
        installed_fastmcp = importlib.metadata.version("fastmcp")
    except importlib.metadata.PackageNotFoundError:
        return {
            "ok": False,
            "detail": "fastmcp is not installed",
        }

    fastmcp_requirement, divergence = _source_floor_reconciliation(
        distribution, fastmcp_requirement
    )
    problems = _requirement_problems("fastmcp", installed_fastmcp, fastmcp_requirement)
    try:
        installed_mcp: str | None = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        installed_mcp = None
    mcp_requirement = _mcp_requirement()
    problems.extend(_requirement_problems("mcp", installed_mcp, mcp_requirement))

    summary = (
        f"fastmcp={installed_fastmcp} (floor {fastmcp_requirement.specifier}), "
        f"mcp={installed_mcp} (floor "
        f"{mcp_requirement.specifier if mcp_requirement else 'unknown'})"
    )
    if problems:
        if divergence:
            problems.append(divergence)
        return {"ok": False, "detail": "; ".join(problems) + f" [{summary}]"}
    if divergence:
        return {"ok": True, "detail": f"{summary} (note: {divergence})"}
    return {"ok": True, "detail": summary}
