"""Public operation-registry contract for GraphOS surfaces and generators."""

from .digest import canonical_op, canonical_registry, registry_digest
from .registry import Caller, PolicyDecision, Registry, authorized
from .spec import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    EgMethod,
    EgSchemaRef,
    Executor,
    HttpShape,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Stability,
    SubjectRef,
    SubjectSource,
    Surface,
    Verb,
)

__all__ = [
    "AuditClass",
    "Caller",
    "Composite",
    "Confirm",
    "Effect",
    "EgMethod",
    "EgSchemaRef",
    "Executor",
    "HttpShape",
    "Idempotency",
    "OpSpec",
    "PolicyDecision",
    "PrincipalRule",
    "Registry",
    "Stability",
    "SubjectRef",
    "SubjectSource",
    "Surface",
    "Verb",
    "authorized",
    "canonical_op",
    "canonical_registry",
    "registry_digest",
]
