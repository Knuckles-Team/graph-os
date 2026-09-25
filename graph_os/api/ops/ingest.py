"""Intent registry declarations for governed ingestion operations (MCPI-16)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Verb,
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: str | None = None
    stream: str | None = None
    repository: str | None = None
    branch: str | None = None
    pack_id: str | None = None
    job_id: str | None = None
    report_id: str | None = None
    proposal_id: str | None = None
    model_id: str | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    request: dict[str, object] | None = None


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: object


_HANDLER = Composite(handler="graph_os.ingest.service.execute")


def _op(
    name: str,
    verb: Verb,
    summary: str,
    scope: str,
    *,
    effect: Effect = Effect.READ,
    console: bool = False,
) -> OpSpec:
    return OpSpec(
        id=f"ingest.{name}",
        verb=verb,
        summary=summary,
        examples=(summary,),
        params=_Input,
        result=_Result,
        binding=_HANDLER,
        scopes=frozenset({scope}),
        effect=effect,
        principals=PrincipalRule.HUMAN_UNDELEGATED if console else PrincipalRule.ANY,
        confirm=Confirm.CONSOLE if console else None,
        idempotency=Idempotency.KEY_REQUIRED
        if effect is not Effect.READ
        else Idempotency.NONE,
        audit=AuditClass.EVENT if effect is not Effect.READ else AuditClass.NONE,
    )


def specs() -> tuple[OpSpec, ...]:
    """The stable ingest IDs; availability is decided by the bound service."""
    return (
        _op(
            "repositories.index",
            Verb.ACT,
            "Index a repository branch",
            "source:ingest",
            effect=Effect.WRITE,
        ),
        _op("sources.list", Verb.ASK, "List connector sources", "source:ingest"),
        _op(
            "sources.status",
            Verb.ASK,
            "Read a connector stream status",
            "source:ingest",
        ),
        _op(
            "sources.sync",
            Verb.ACT,
            "Sync a connector source",
            "source:ingest",
            effect=Effect.WRITE,
        ),
        _op("packs.list", Verb.ASK, "List connector packs", "agent:pack-control"),
        _op(
            "packs.status", Verb.ASK, "Read connector pack status", "agent:pack-control"
        ),
        _op(
            "packs.import",
            Verb.MANAGE,
            "Import a digest-pinned connector pack",
            "admin:connector-pack",
            effect=Effect.ADMIN,
            console=True,
        ),
        _op(
            "packs.bind",
            Verb.MANAGE,
            "Bind a connector pack",
            "admin:connector-pack",
            effect=Effect.ADMIN,
            console=True,
        ),
        _op(
            "packs.retire",
            Verb.MANAGE,
            "Retire a connector pack",
            "admin:connector-pack",
            effect=Effect.DESTRUCTIVE,
            console=True,
        ),
        _op("jobs.list", Verb.ASK, "List ingestion jobs", "source:ingest"),
        _op("jobs.status", Verb.ASK, "Read an ingestion job", "source:ingest"),
        _op("drift.list", Verb.ASK, "List schema drift reports", "ingest:read"),
        _op("drift.get", Verb.ASK, "Read a schema drift report", "ingest:read"),
        _op(
            "drift.repair.propose",
            Verb.MANAGE,
            "Propose a schema drift repair",
            "admin:connector-pack",
            effect=Effect.ADMIN,
            console=True,
        ),
        _op(
            "drift.repair.approve",
            Verb.MANAGE,
            "Approve a schema drift repair",
            "admin:connector-pack",
            effect=Effect.ADMIN,
            console=True,
        ),
        _op(
            "embedding.admission.propose",
            Verb.MANAGE,
            "Propose an embedding admission",
            "admin:connector-pack",
            effect=Effect.ADMIN,
            console=True,
        ),
        _op(
            "embedding.admission.status",
            Verb.ASK,
            "Read embedding admission status",
            "ingest:read",
        ),
        _op(
            "embedding.reembed.propose",
            Verb.MANAGE,
            "Propose a shadow re-embedding",
            "admin:connector-pack",
            effect=Effect.ADMIN,
            console=True,
        ),
        _op(
            "embedding.reembed.status",
            Verb.ASK,
            "Read shadow re-embedding status",
            "ingest:read",
        ),
    )
