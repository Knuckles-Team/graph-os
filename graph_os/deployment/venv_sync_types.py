"""Value objects returned by the shared virtualenv reconciler.

The reconciler's safety logic stays in :mod:`venv_sync`; this module keeps the
immutable result and observation records together so the policy implementation
does not also have to own every data shape it emits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .venv_sync import Member, SyncPlan, Verdict

__all__ = [
    "Backup",
    "CommandResult",
    "DriftFinding",
    "DriftReport",
    "MemberInstallState",
    "PruneCandidate",
    "PruneOutcome",
    "PrunePlan",
    "ProbeResult",
    "SyncOutcome",
    "UpgradeOutcome",
]


@dataclass(frozen=True)
class Backup:
    """One archived ``uv.lock`` plus the metadata needed to trust it."""

    id: str
    path: Path
    created_at: str
    digest: str
    reason: str
    verified: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "created_at": self.created_at,
            "digest": self.digest,
            "reason": self.reason,
            "verified": self.verified,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class CommandResult:
    """Captured result of one external command invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


@dataclass(frozen=True)
class SyncOutcome:
    """The result of asking for the environment to be made current."""

    verdict: Verdict
    plan: SyncPlan | None
    applied: bool
    detail: str = ""
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.verdict.decision == "allow" and (
            self.applied or (self.plan is not None and self.plan.is_empty)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.as_dict(),
            "applied": self.applied,
            "detail": self.detail,
            "duration_s": round(self.duration_s, 3),
            "plan": {
                "installs": [d.name for d in (self.plan.installs if self.plan else ())],
                "uninstalls": [
                    d.name for d in (self.plan.uninstalls if self.plan else ())
                ],
                "empty": self.plan.is_empty if self.plan else None,
            },
        }


@dataclass(frozen=True)
class PruneCandidate:
    """An installed distribution that is neither locked nor a workspace member."""

    name: str
    version: str


@dataclass(frozen=True)
class PrunePlan:
    """What ``prune()`` would remove, computed by set difference."""

    candidates: tuple[PruneCandidate, ...]

    @property
    def is_empty(self) -> bool:
        return not self.candidates

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": [f"{c.name}=={c.version}" for c in self.candidates],
        }


@dataclass(frozen=True)
class PruneOutcome:
    """The result of asking for extraneous packages to be removed."""

    plan: PrunePlan
    applied: bool
    refused: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.as_dict(),
            "applied": self.applied,
            "refused": self.refused,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProbeResult:
    """One post-change health assertion."""

    name: str
    ok: bool | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class MemberInstallState:
    """How one editable workspace member is actually installed right now."""

    member: Member
    installed: bool
    editable: bool
    source_version: str | None
    installed_version: str | None
    source_entry_points: tuple[str, ...]
    installed_entry_points: tuple[str, ...]
    differences: tuple[str, ...]

    @property
    def stale(self) -> bool:
        return bool(self.differences)


@dataclass(frozen=True)
class _InstalledRecord:
    """Minimal metadata read from one installed distribution."""

    name: str
    version: str
    editable: bool
    entry_points: tuple[str, ...]


@dataclass(frozen=True)
class DriftFinding:
    """One finding produced by shared-venv drift detection."""

    code: str
    severity: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass(frozen=True)
class DriftReport:
    """Complete drift report, including its derived status and summary."""

    findings: tuple[DriftFinding, ...]

    @property
    def status(self) -> str:
        ranks = {"ok": 0, "warn": 1, "fail": 2}
        worst = max((ranks[f.severity] for f in self.findings), default=0)
        return {0: "ok", 1: "warn", 2: "fail"}[worst]

    @property
    def summary(self) -> str:
        problems = [f for f in self.findings if f.severity != "ok"]
        if not problems:
            return "shared venv is current with uv.lock"
        return "; ".join(f.detail for f in problems)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass(frozen=True)
class UpgradeOutcome:
    """Result of moving the lock forward and proving the result works."""

    verdict: Verdict
    backup: Backup | None
    plan: SyncPlan | None
    probes: tuple[ProbeResult, ...]
    applied: bool
    rolled_back: bool
    detail: str

    @property
    def ok(self) -> bool:
        return self.applied and not self.rolled_back

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.as_dict(),
            "backup": self.backup.as_dict() if self.backup else None,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "detail": self.detail,
            "probes": [p.as_dict() for p in self.probes],
        }
