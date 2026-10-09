"""Pure, partitioned AIMD error-budget controller.

Implements the decision rule in ``specs/adaptive-capacity/plan.md`` for
GRAPHOS-CAPACITY-R001.1: additive-increase, multiplicative-decrease (AIMD)
concurrency control driven by a bounded window of real outcome classes.

This module holds no clock, no I/O, and no fleet dispatch wiring. ``decide``
is a pure function of a closed window and the controller's prior state, so
replaying the same window (for example after a crash before a decision
receipt was durably recorded) is idempotent: the same inputs always produce
the same :class:`ThrottleDecision`. Inserting a limiter into the real fleet
dispatch path, and the hosted ``capacity.throttle.*`` operations that expose
status and mode control, are separate, larger requirements
(GRAPHOS-CAPACITY-R001.2 and GRAPHOS-CAPACITY-R002); this module is their
shared, independently testable producer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class OutcomeClass(StrEnum):
    """One observed outcome of a single governed fleet call."""

    SUCCESS = "success"
    RETRYABLE_SERVICE_ERROR = "retryable_service_error"
    TIMEOUT = "timeout"
    PERMANENT_CALLER_ERROR = "permanent_caller_error"
    POLICY_DENIAL = "policy_denial"


class ThrottleMode(StrEnum):
    """Whether a decision is only recorded, or also enforced at admission."""

    OBSERVE = "observe"
    ENFORCE = "enforce"


# Outcome classes that teach the controller the child is healthy or
# unhealthy. Caller mistakes and policy refusals are deliberately excluded:
# neither is evidence that the child service itself has degraded.
_ELIGIBLE_FAILURE_CLASSES = frozenset(
    {OutcomeClass.RETRYABLE_SERVICE_ERROR, OutcomeClass.TIMEOUT}
)
_ELIGIBLE_CLASSES = _ELIGIBLE_FAILURE_CLASSES | {OutcomeClass.SUCCESS}


@dataclass(frozen=True, slots=True)
class Partition:
    """The boundary outcomes must never be compared across.

    Observations and limits for one tenant/child/operation-class/policy
    revision never influence another partition's decision.
    """

    tenant: str
    child: str
    operation_class: str
    policy_revision: str

    def __post_init__(self) -> None:
        for value in (
            self.tenant,
            self.child,
            self.operation_class,
            self.policy_revision,
        ):
            if not value:
                raise ValueError("partition fields must be non-empty")

    def key(self) -> tuple[str, str, str, str]:
        return (self.tenant, self.child, self.operation_class, self.policy_revision)


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    """One classified outcome contributed to a budget window."""

    outcome: OutcomeClass


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    """A bounded, closed window of outcome samples for one partition."""

    window_id: str
    partition: Partition
    samples: tuple[OutcomeSample, ...]

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("window_id is required")

    def digest(self) -> str:
        """Deterministic content digest used for idempotent decision receipts."""
        payload = {
            "window_id": self.window_id,
            "partition": list(self.partition.key()),
            "samples": [sample.outcome.value for sample in self.samples],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def eligible_count(self) -> int:
        return sum(1 for sample in self.samples if sample.outcome in _ELIGIBLE_CLASSES)

    def eligible_failure_count(self) -> int:
        return sum(
            1 for sample in self.samples if sample.outcome in _ELIGIBLE_FAILURE_CLASSES
        )


@dataclass(frozen=True, slots=True)
class AimdConfig:
    """Explicit, versioned AIMD tuning. No hidden constants.

    ``alpha`` is the bounded additive increment applied to a healthy window.
    ``beta`` is the multiplicative factor applied on a budget breach, in
    ``(0, 1)``. ``floor`` is the lowest concurrency automatic control may
    reach. ``min_sample_count`` is the fewest eligible samples a window needs
    before it can move the limit. ``error_budget_fraction`` is the eligible
    failure fraction that counts as a breach.
    """

    version: str
    alpha: int
    beta: float
    floor: int
    min_sample_count: int
    error_budget_fraction: float

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("config version is required")
        if self.alpha <= 0:
            raise ValueError("alpha must be positive")
        if not 0 < self.beta < 1:
            raise ValueError("beta must be in (0, 1)")
        if self.floor < 0:
            raise ValueError("floor must not be negative")
        if self.min_sample_count < 1:
            raise ValueError("min_sample_count must be at least 1")
        if not 0 < self.error_budget_fraction < 1:
            raise ValueError("error_budget_fraction must be in (0, 1)")


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    """A deterministic, replayable AIMD decision receipt.

    Carries the input window digest, the prior/new limits, the mode the
    decision was computed under, a stable machine reason, and the sample
    counts that produced it -- enough to reconstruct the decision without
    raw request content.
    """

    window_digest: str
    partition: Partition
    config_version: str
    prior_limit: int
    new_limit: int
    mode: ThrottleMode
    reason: str
    sample_count: int
    eligible_failure_count: int


def decide(
    *,
    window: BudgetWindow,
    prior_limit: int,
    engine_headroom: int,
    config: AimdConfig,
    mode: ThrottleMode,
) -> ThrottleDecision:
    """Compute the next AIMD limit for one window.

    The result never exceeds ``engine_headroom``: an engine-declared ceiling
    narrows the effective limit immediately regardless of the AIMD state. A
    window with fewer than ``config.min_sample_count`` eligible samples moves
    nothing and reports ``insufficient_samples``, so caller errors, policy
    denials, and quiet windows never fabricate a health signal.
    """

    if engine_headroom < 0:
        raise ValueError("engine_headroom must not be negative")
    if prior_limit < 0:
        raise ValueError("prior_limit must not be negative")

    bounded_prior = min(prior_limit, engine_headroom)
    eligible = window.eligible_count()
    failures = window.eligible_failure_count()

    if eligible < config.min_sample_count:
        return ThrottleDecision(
            window_digest=window.digest(),
            partition=window.partition,
            config_version=config.version,
            prior_limit=prior_limit,
            new_limit=bounded_prior,
            mode=mode,
            reason="insufficient_samples",
            sample_count=eligible,
            eligible_failure_count=failures,
        )

    error_fraction = failures / eligible
    if error_fraction > config.error_budget_fraction:
        new_limit = max(config.floor, int(bounded_prior * config.beta))
        reason = "budget_breach_decrease"
    else:
        new_limit = bounded_prior + config.alpha
        reason = "healthy_window_increase"
    new_limit = min(new_limit, engine_headroom)

    return ThrottleDecision(
        window_digest=window.digest(),
        partition=window.partition,
        config_version=config.version,
        prior_limit=prior_limit,
        new_limit=new_limit,
        mode=mode,
        reason=reason,
        sample_count=eligible,
        eligible_failure_count=failures,
    )
