"""Owner-scoped foreign-source operations backed by EG's federation contract.

The five methods beyond registration require corresponding served EG methods and
wheel schemas before this domain may be mounted in the generated registry.
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
    """Declare the six intent IDs; absent EG methods must fail contract generation."""
    return (
        _op(
            "register",
            "RegisterForeignSource",
            Verb.MANAGE,
            "Register a foreign source under the verified caller's owner identity.",
            Effect.WRITE,
        ),
        _op(
            "list",
            "ListForeignSources",
            Verb.ASK,
            "List foreign sources owned by the verified caller.",
            Effect.READ,
        ),
        _op(
            "get",
            "GetForeignSource",
            Verb.ASK,
            "Get one foreign source owned by the verified caller.",
            Effect.READ,
        ),
        _op(
            "share",
            "ShareForeignSource",
            Verb.MANAGE,
            "Grant one principal use of an owned foreign source.",
            Effect.ADMIN,
        ),
        _op(
            "unshare",
            "UnshareForeignSource",
            Verb.MANAGE,
            "Revoke one principal's foreign-source use grant.",
            Effect.ADMIN,
        ),
        _op(
            "probe",
            "ProbeForeignSource",
            Verb.ASK,
            "Probe an owned foreign source within its SSRF and network budget.",
            Effect.READ,
        ),
    )
