"""Foreign-source operations backed by EG's served federation contract.

EH-607: List/Get/Share/Unshare/ProbeForeignSource are not wire-callable EG
methods in the Train 5 contract. Do not advertise their intent IDs until EG
supplies those methods, schemas and owner-scoped authorization semantics.
"""

from graph_os.api.registry import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Verb,
)


def _schema(method: str, *, result: bool = False) -> EgSchemaRef:
    filename = "result.cluster.json" if result else "method.request.json"
    return EgSchemaRef(path=f"contract/schemas/{filename}#/methods/{method}")


def _op(
    name: str,
    method: str,
    verb: Verb,
    summary: str,
    effect: Effect,
) -> OpSpec:
    return OpSpec(
        id=f"federation.sources.{name}",
        verb=verb,
        summary=summary,
        examples=(summary,),
        params=_schema(method),
        result=_schema(method, result=True),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({"federation:admin"}),
        effect=effect,
        principals=PrincipalRule.HUMAN_UNDELEGATED
        if effect is Effect.ADMIN
        else PrincipalRule.ANY,
        idempotency=Idempotency.NATURAL
        if effect is Effect.READ
        else Idempotency.KEY_REQUIRED,
        audit=AuditClass.EVENT if effect is not Effect.READ else AuditClass.NONE,
    )


def specs() -> tuple[OpSpec, ...]:
    """Publish only the foreign-source method backed by the current EG wire."""
    return (
        _op(
            "register",
            "RegisterForeignSource",
            Verb.MANAGE,
            "Register a foreign source under the verified caller's owner identity.",
            Effect.WRITE,
        ),
    )
