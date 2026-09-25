"""Intent ranking over already-authorized operation and fleet descriptors.

The caller filters descriptors through scope, principal and Eunomia policy before
passing them here. A ranking can select an operation, but it cannot authorize it.
Only a trusted, completed invocation may feed :meth:`record_execution`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from collections import Counter, OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_WORDS = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the of for to in on with and or is are be do what how why my me i you your please".split()
)
_READ_VERBS = frozenset({"ask", "why", "find"})
_VERBS = _READ_VERBS | {"write", "act", "manage"}
_CACHE_LIMIT = 256
_OUTCOME_LIMIT = 4096
_REWARD_WEIGHT = 0.2
_SEMANTIC_WEIGHT = 0.35
_MIN_EVIDENCE = 0.05


def _tokens(value: str) -> Counter[str]:
    return Counter(word for word in _WORDS.findall(value.lower()) if word not in _STOP)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _verb(value: object) -> str:
    return str(getattr(value, "value", value)).lower()


@dataclass(frozen=True)
class Descriptor:
    """A generated op descriptor or one visible fleet catalog item."""

    id: str
    verb: str
    summary: str
    examples: tuple[str, ...] = ()
    domain: str = ""
    tags: tuple[str, ...] = ()
    kind: str = "op"
    params_schema: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> Descriptor:
        """Accept registry descriptors and fleet catalog projections uniformly."""
        identifier = str(item.get("op") or item.get("id") or "")
        if not identifier:
            raise ValueError("A routing descriptor requires an op or id")
        return cls(
            id=identifier,
            verb=_verb(item.get("verb", "act")),
            summary=str(item.get("summary") or item.get("description") or ""),
            examples=tuple(str(value) for value in item.get("examples", ())),
            domain=str(item.get("domain") or ""),
            tags=tuple(str(value) for value in item.get("tags", ())),
            kind=str(item.get("kind") or "op"),
            params_schema=item.get("params_schema") or {},
        )


@dataclass(frozen=True)
class Candidate:
    descriptor: Descriptor
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    """Routing decision; ``preview`` requires the invocation layer's plan lease."""

    op: str | None
    params: Mapping[str, Any]
    preview: bool
    missing_required: tuple[str, ...]
    alternatives: tuple[str, ...]
    why: str
    fallback: bool = False


class IntentResolver:
    """Bounded lexical/semantic router with policy-partitioned outcome learning."""

    def __init__(self, *, cache_limit: int = _CACHE_LIMIT) -> None:
        if cache_limit < 1:
            raise ValueError("cache_limit must be positive")
        self._secret = secrets.token_bytes(32)
        self._cache_limit = cache_limit
        self._cache: OrderedDict[tuple[str, ...], tuple[Candidate, ...]] = OrderedDict()
        self._outcomes: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._epoch = 0

    def scope_ref(
        self,
        tenant: str,
        policy_revision: str,
        *,
        audience: str = "",
        scopes: Sequence[str] = (),
    ) -> str:
        """Opaque process-local reference for verified authority, never caller hints."""
        if not tenant or not policy_revision:
            raise ValueError("Verified tenant and policy revision are required")
        authority = json.dumps(
            [tenant, policy_revision, audience, sorted(scopes)], separators=(",", ":")
        ).encode()
        return hmac.digest(self._secret, authority, "sha256").hex()

    def rank(
        self,
        verb: str,
        intent: str,
        descriptors: Sequence[Descriptor | Mapping[str, Any]],
        *,
        scope_ref: str | None,
        semantic_scores: Mapping[str, float] | None = None,
        top_k: int = 5,
    ) -> tuple[Candidate, ...]:
        """Rank only the visible input snapshot; unseen items never enter cache."""
        if verb not in _VERBS:
            raise ValueError(f"Unknown intent verb: {verb}")
        visible = tuple(_coerce(item) for item in descriptors)
        scores = semantic_scores or {}
        limit = max(1, min(top_k, 20))
        key = (
            verb,
            _digest(" ".join(intent.lower().split())),
            _digest([_fingerprint(item) for item in visible]),
            _digest(scores),
            scope_ref or "unverified",
            str(limit),
            str(self._epoch),
        )
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        terms = _tokens(intent)
        pool = (item for item in visible if verb == "find" or item.verb == verb)
        ranked = tuple(
            sorted(
                (self._score(item, terms, scores, scope_ref) for item in pool),
                key=lambda candidate: (-candidate.score, candidate.descriptor.id),
            )[:limit]
        )
        self._cache[key] = ranked
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        return ranked

    def resolve(
        self,
        verb: str,
        intent: str,
        descriptors: Sequence[Descriptor | Mapping[str, Any]],
        *,
        scope_ref: str | None,
        params: Mapping[str, Any] | None = None,
        semantic_scores: Mapping[str, float] | None = None,
    ) -> Resolution:
        """Select an NL op; mutations always require a reviewed preview first."""
        ranked = self.rank(
            verb,
            intent,
            descriptors,
            scope_ref=scope_ref,
            semantic_scores=semantic_scores,
        )
        supplied = dict(params or {})
        if verb == "find":
            return Resolution(
                None,
                supplied,
                False,
                (),
                tuple(candidate.descriptor.id for candidate in ranked),
                "Discovery results; no operation executed",
            )
        winner = ranked[0] if ranked else None
        if winner is None or winner.score < _MIN_EVIDENCE:
            return _ask_fallback(verb, intent, supplied, descriptors)
        selected = winner.descriptor
        missing = _missing_required(selected.params_schema, supplied)
        return Resolution(
            op=selected.id,
            params=supplied,
            preview=verb not in _READ_VERBS,
            missing_required=missing,
            alternatives=tuple(candidate.descriptor.id for candidate in ranked[1:]),
            why=f"Matched {', '.join(winner.matched_terms) or 'semantic similarity'}",
        )

    def record_execution(
        self, *, scope_ref: str | None, verb: str, op: str, success: bool
    ) -> None:
        """Record an observed authorized NL dispatch, called by invoke after completion."""
        if scope_ref is None or verb not in _VERBS or not op:
            return
        key = (scope_ref, verb, op)
        previous = self._outcomes.get(key, 0.5)
        self._outcomes[key] = 0.2 * float(success) + 0.8 * previous
        self._outcomes.move_to_end(key)
        while len(self._outcomes) > _OUTCOME_LIMIT:
            self._outcomes.popitem(last=False)
        self._epoch += 1

    def _score(
        self,
        item: Descriptor,
        terms: Counter[str],
        semantic_scores: Mapping[str, float],
        scope_ref: str | None,
    ) -> Candidate:
        name = set(_WORDS.findall(item.id.lower()))
        description = _tokens(
            " ".join((item.summary, *item.examples, item.domain, *item.tags))
        )
        matched = tuple(
            sorted(term for term in terms if term in name or term in description)
        )
        weight = sum(
            count * (2 if term in name else 1)
            for term, count in terms.items()
            if term in matched
        )
        lexical = weight / max(sum(terms.values()), 1)
        name_hits = len(name.intersection(matched))
        lexical += (name_hits / len(name)) * 0.01 if name else 0.0
        semantic = float(semantic_scores.get(item.id, 0.0))
        semantic = max(0.0, min(1.0, semantic)) if math.isfinite(semantic) else 0.0
        reward = (
            self._outcomes.get((scope_ref, item.verb, item.id), 0.5)
            if scope_ref
            else 0.5
        )
        return Candidate(
            item,
            lexical + _SEMANTIC_WEIGHT * semantic + _REWARD_WEIGHT * (reward - 0.5),
            matched,
        )


def _coerce(item: Descriptor | Mapping[str, Any]) -> Descriptor:
    return item if isinstance(item, Descriptor) else Descriptor.from_mapping(item)


def _fingerprint(item: Descriptor) -> tuple[object, ...]:
    return (
        item.id,
        item.verb,
        item.summary,
        item.examples,
        item.domain,
        item.tags,
        item.kind,
        item.params_schema,
    )


def _missing_required(
    schema: Mapping[str, Any], params: Mapping[str, Any]
) -> tuple[str, ...]:
    required = schema.get("required", ())
    return tuple(sorted(str(name) for name in required if name not in params))


def _ask_fallback(
    verb: str,
    intent: str,
    params: Mapping[str, Any],
    descriptors: Sequence[Descriptor | Mapping[str, Any]],
) -> Resolution:
    visible = tuple(_coerce(item) for item in descriptors)
    has_uql = any(item.id == "query.uql" and item.verb == "ask" for item in visible)
    if verb != "ask" or not has_uql:
        return Resolution(
            None, params, False, (), (), "No matching authorized operation"
        )
    return Resolution(
        "query.uql",
        {**params, "query": intent, "nl": True},
        False,
        (),
        (),
        "Using the authorized natural-language UQL planner",
        True,
    )
