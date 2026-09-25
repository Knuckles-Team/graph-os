"""Security verification through the caller-bound engine."""

from graph_os.api.registry import EgMethod, EgSchemaRef, Idempotency, OpSpec, Verb


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="security.audit.verify",
            verb=Verb.WHY,
            summary="Verify the engine audit chain.",
            examples=("Verify the audit log has not been altered",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/AuditVerify"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.security.json#/methods/AuditVerify"
            ),
            binding=EgMethod(service="AuditVerify", op="AuditVerify"),
            scopes=frozenset({"security:audit"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
