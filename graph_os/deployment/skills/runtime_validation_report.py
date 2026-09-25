"""GraphOS bundled-skill runtime report logic."""

from __future__ import annotations

import errno
import os
import secrets
import stat
from pathlib import Path
from typing import NoReturn

from agent_utilities.security.persistence_privacy import PersistencePrivacyGuard

from .runtime_validation_core import (
    _FAIL,
    _MAX_REPORT_BYTES,
    _PASS,
    CaseResult,
)
from .runtime_validation_matrix import (
    load_matrix,
)


def _report_payload(content: str) -> bytes:
    """Encode one already-controlled report after a final privacy gate."""

    _clean, privacy = PersistencePrivacyGuard().sanitize_text(content)
    if privacy.changed:
        raise RuntimeError("report_privacy_gate_failed")
    payload = content.encode("utf-8")
    if not 1 <= len(payload) <= _MAX_REPORT_BYTES:
        raise RuntimeError("report_size_invalid")
    return payload


def _raise_report_directory_error(
    part: str, directory_fd: int, exc: OSError
) -> NoReturn:
    """Classify a failed component open without ever following the component."""

    try:
        metadata = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        raise RuntimeError("report_directory_invalid") from None
    code = (
        "report_directory_symlink"
        if stat.S_ISLNK(metadata.st_mode)
        else "report_directory_invalid"
    )
    raise RuntimeError(code) from exc


def _create_report_component(directory_fd: int, part: str, flags: int) -> int:
    """Create one missing component 0700 and reopen it no-follow."""

    try:
        os.mkdir(part, mode=0o700, dir_fd=directory_fd)
        _fsync_report_directory(directory_fd)
    except FileExistsError:
        pass
    try:
        return os.open(part, flags, dir_fd=directory_fd)
    except OSError as exc:
        _raise_report_directory_error(part, directory_fd, exc)


def _open_report_component(directory_fd: int, part: str, flags: int) -> int:
    """Open one path component no-follow, creating it when it is absent."""

    if part in {"", ".", ".."}:
        raise RuntimeError("report_directory_invalid")
    try:
        return os.open(part, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return _create_report_component(directory_fd, part, flags)
    except OSError as exc:
        _raise_report_directory_error(part, directory_fd, exc)


def _open_report_directory(path: Path) -> int:
    """Open or create a POSIX directory by traversing every component no-follow."""

    absolute = path.absolute()
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    current_fd = os.open(absolute.anchor, directory_flags)
    try:
        for part in absolute.parts[1:]:
            next_fd = _open_report_component(current_fd, part, directory_flags)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _check_report_destination(directory_fd: int, filename: str) -> None:
    """Reject a symlink or non-regular destination without following it."""

    try:
        metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("report_destination_symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("report_destination_invalid")


def _fsync_report_directory(directory_fd: int) -> None:
    """Persist a directory update when the host filesystem supports it."""

    try:
        os.fsync(directory_fd)
    except OSError as exc:
        unsupported = {
            errno.EBADF,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", -1),
            getattr(errno, "EOPNOTSUPP", -1),
        }
        if exc.errno not in unsupported:
            raise


def _publish_report_posix(destination: Path, payload: bytes) -> None:
    """Publish through a no-follow directory descriptor on POSIX."""

    directory_fd = _open_report_directory(destination.parent)
    temporary_name = ""
    try:
        _check_report_destination(directory_fd, destination.name)
        create_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _attempt in range(8):
            candidate = f".{destination.name}.{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    create_flags,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        else:
            raise RuntimeError("report_temporary_unavailable")
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _check_report_destination(directory_fd, destination.name)
        os.replace(
            temporary_name,
            destination.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = ""
        _fsync_report_directory(directory_fd)
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def publish_report(destination: Path, content: str) -> None:
    """Publish a bounded report only where descriptor-safe privacy is available."""

    if destination.name in {"", ".", ".."}:
        raise RuntimeError("report_destination_invalid")
    if os.name != "posix":
        raise RuntimeError("report_platform_unsupported")
    payload = _report_payload(content)
    _publish_report_posix(destination, payload)


def _validate_result_set(results: list[CaseResult], *, mode: str) -> None:
    """Require the exact selected catalog and unique nonempty evidence refs."""

    _defaults, catalog = load_matrix()
    expected = {
        case.case_id: (case.skill, case.mode, case.model_class)
        for case in catalog
        if mode in {"all", case.mode}
    }
    actual_ids = [result.case_id for result in results]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
        raise RuntimeError("runtime_case_set_invalid")
    if any(
        (result.skill, result.mode, result.model_class) != expected[result.case_id]
        for result in results
    ):
        raise RuntimeError("runtime_case_contract_invalid")
    _require_unique_evidence_references(results)


def _require_unique_evidence_references(results: list[CaseResult]) -> None:
    """Reject any two cases claiming the same run or trace reference."""

    for attribute in ("run_ref", "trace_ref"):
        references = [
            str(getattr(result, attribute))
            for result in results
            if getattr(result, attribute)
        ]
        if len(references) != len(set(references)):
            raise RuntimeError("runtime_evidence_reference_collision")


# Column order of the per-skill table; changing either tuple changes the report.
_DIRECT_REPORT_CHECKS = (
    "structural",
    "model_selection",
    "skill_binding",
    "semantic",
    "trace",
    "parent_ingestion",
)
_DELEGATED_REPORT_CHECKS = (
    "structural",
    "model_selection",
    "skill_binding",
    "semantic",
    "delegation",
    "trace",
    "parent_ingestion",
)


def _report_header_lines(generated_at: str) -> list[str]:
    """Return the report preamble and the per-skill table header."""

    return [
        "# Agent Utilities consolidated skill validation matrix",
        "",
        f"Generated: {generated_at}",
        "",
        "Validation used synthetic, read-only cases, sequential execution, "
        "metadata-only observability, and neutral `skill://` references. Raw model "
        "output, prompts, endpoints, credentials, identities, trace identifiers, and "
        "filesystem locations are intentionally absent.",
        "",
        "## Per-skill result",
        "",
        "| Skill | Direct static | Direct model selection | Direct skill binding | Direct semantic | Direct trace | Direct KG ingest | Delegated static | Delegated model selection | Delegated skill binding | Delegated semantic | Graph-OS delegation | Delegated trace | Delegated KG ingest | Paired result |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]


def _report_check_cells(
    result: CaseResult | None, checks: tuple[str, ...]
) -> list[str]:
    """Render one mode's check columns, or `not-run` when the case is absent."""

    if result is None:
        return ["not-run"] * len(checks)
    return [str(getattr(result, name)) for name in checks]


def _skill_matrix_row(skill: str, pair: dict[str, CaseResult]) -> str:
    """Render one skill's direct/delegated row of the per-skill table."""

    direct = pair.get("direct")
    delegated = pair.get("delegated")
    pair_passed = bool(direct and delegated and direct.passed and delegated.passed)
    cells = [
        f"`{skill}`",
        *_report_check_cells(direct, _DIRECT_REPORT_CHECKS),
        *_report_check_cells(delegated, _DELEGATED_REPORT_CHECKS),
        _PASS if pair_passed else _FAIL,
    ]
    return "| " + " | ".join(cells) + " |"


def _evidence_report_row(result: CaseResult) -> str:
    """Render one case's row of the privacy-safe evidence table."""

    routes = ", ".join(f"`{route}`" for route in result.selected_routes) or "none"
    errors = ", ".join(f"`{code}`" for code in result.error_codes) or "none"
    return (
        f"| `{result.case_id}` | {routes} | `{result.model_ref or 'none'}` | "
        f"`{result.skill_ref or 'none'}` | `{result.skill_body_ref or 'none'}` | "
        f"`{result.run_ref or 'none'}` | `{result.trace_ref or 'none'}` | "
        f"{result.trace_linkage} | {errors} |"
    )


def _report_aggregate_lines(
    results: list[CaseResult], by_skill: dict[str, dict[str, CaseResult]]
) -> list[str]:
    """Render the aggregate section and its linkage/ingestion method notes."""

    passed = sum(result.passed for result in results)
    fully_passed = sum(
        all(item.passed for item in pair.values()) and len(pair) == 2
        for pair in by_skill.values()
    )
    return [
        "",
        "## Aggregate",
        "",
        f"- Cases passed: {passed}/{len(results)}",
        f"- Skills fully passed: {fully_passed}/{len(by_skill)}",
        "- Trace linkage method: one exact-name `graph_run` trace whose metadata binds the case run, configured model, model class, skill, and skill body, queried through the Langfuse MCP tool mounted by Graph-OS.",
        "- Parent-ingestion proof: each exact trace resolves to exactly one `Trace` node written by Graph-OS parent mediation under verified `kg:write` authority.",
        "",
    ]


def render_report(results: list[CaseResult], *, generated_at: str) -> str:
    """Render only controlled fields and opaque references."""

    by_skill: dict[str, dict[str, CaseResult]] = {}
    for result in results:
        by_skill.setdefault(result.skill, {})[result.mode] = result
    lines = _report_header_lines(generated_at)
    lines.extend(
        _skill_matrix_row(skill, by_skill[skill]) for skill in sorted(by_skill)
    )
    lines.extend(
        [
            "",
            "## Privacy-safe evidence",
            "",
            "| Case | Routes selected | Model reference | Skill reference | Skill body reference | Run reference | Trace reference | Linkage | Errors |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(
        _evidence_report_row(result)
        for result in sorted(results, key=lambda item: item.case_id)
    )
    lines.extend(_report_aggregate_lines(results, by_skill))
    rendered = "\n".join(lines)
    _clean, privacy = PersistencePrivacyGuard().sanitize_text(rendered)
    if privacy.changed:
        raise RuntimeError("report_privacy_gate_failed")
    return rendered
