"""Focused contract tests for the generated-descriptor intent resolver."""

from graph_os.api.mcp.resolve import IntentResolver


def _items() -> list[dict[str, object]]:
    return [
        {
            "op": "identity.users.disable",
            "verb": "manage",
            "summary": "Disable a user account",
            "examples": ["disable this user"],
            "params_schema": {"required": ["user_id", "reason"]},
        },
        {
            "op": "identity.users.list",
            "verb": "ask",
            "summary": "List user accounts",
            "examples": ["show users"],
        },
        {
            "op": "query.uql",
            "verb": "ask",
            "summary": "Execute structured UQL query",
            "examples": ["run a UQL query"],
        },
        {
            "id": "fleet.call",
            "verb": "act",
            "kind": "fleet",
            "summary": "Invoke an authorized fleet tool",
            "examples": ["run the calendar agent"],
        },
    ]


def test_generated_and_fleet_descriptors_rank_in_visible_verb() -> None:
    resolver = IntentResolver()
    scope = resolver.scope_ref("tenant-a", "policy-1")
    assert (
        resolver.rank("manage", "disable user", _items(), scope_ref=scope)[
            0
        ].descriptor.id
        == "identity.users.disable"
    )
    assert (
        resolver.rank("act", "run the calendar agent", _items(), scope_ref=scope)[
            0
        ].descriptor.kind
        == "fleet"
    )
    assert all(
        candidate.descriptor.verb == "ask"
        for candidate in resolver.rank("ask", "list users", _items(), scope_ref=scope)
    )
    assert (
        resolver.rank("find", "disable user", _items(), scope_ref=scope)[
            0
        ].descriptor.id
        == "identity.users.disable"
    )
    discovery = resolver.resolve("find", "disable user", _items(), scope_ref=scope)
    assert discovery.op is None
    assert discovery.alternatives[0] == "identity.users.disable"


def test_nl_mutation_previews_missing_params_and_never_executes() -> None:
    resolver = IntentResolver()
    result = resolver.resolve(
        "manage",
        "disable this user",
        _items(),
        scope_ref=None,
        params={"user_id": "u1"},
    )
    assert result.op == "identity.users.disable"
    assert result.preview
    assert result.missing_required == ("reason",)


def test_unknown_ask_falls_back_only_when_uql_is_visible() -> None:
    resolver = IntentResolver()
    result = resolver.resolve(
        "ask", "what does my unusual dataset imply?", _items(), scope_ref=None
    )
    assert result.op == "query.uql"
    assert result.fallback
    assert result.params == {"query": "what does my unusual dataset imply?", "nl": True}
    hidden = [item for item in _items() if item.get("op") != "query.uql"]
    assert (
        resolver.resolve(
            "ask", "what does my unusual dataset imply?", hidden, scope_ref=None
        ).op
        is None
    )
    assert resolver.resolve("manage", "gibberish", _items(), scope_ref=None).op is None


def test_visibility_and_policy_revision_partition_ranking() -> None:
    resolver = IntentResolver()
    first = resolver.scope_ref("tenant-a", "policy-1")
    changed = resolver.scope_ref("tenant-a", "policy-2")
    other = resolver.scope_ref("tenant-b", "policy-1")
    assert len({first, changed, other}) == 3
    visible = _items()
    assert resolver.rank("manage", "disable user", visible, scope_ref=first)
    assert not resolver.rank("manage", "disable user", visible[1:], scope_ref=first)
    assert len(resolver._cache) == 2
    resolver.rank("manage", "disable user", visible, scope_ref=changed)
    assert len(resolver._cache) == 3


def test_outcome_learning_is_scoped_and_invalidates_cached_rank() -> None:
    resolver = IntentResolver()
    descriptors = [
        {"op": "query.alpha", "verb": "ask", "summary": "inspect records"},
        {"op": "query.beta", "verb": "ask", "summary": "inspect records"},
    ]
    a = resolver.scope_ref("tenant-a", "policy-1")
    b = resolver.scope_ref("tenant-b", "policy-1")
    assert (
        resolver.rank("ask", "inspect records", descriptors, scope_ref=a)[
            0
        ].descriptor.id
        == "query.alpha"
    )
    resolver.record_execution(scope_ref=a, verb="ask", op="query.beta", success=True)
    assert (
        resolver.rank("ask", "inspect records", descriptors, scope_ref=a)[
            0
        ].descriptor.id
        == "query.beta"
    )
    assert (
        resolver.rank("ask", "inspect records", descriptors, scope_ref=b)[
            0
        ].descriptor.id
        == "query.alpha"
    )


def test_semantic_scores_and_cache_bound() -> None:
    resolver = IntentResolver(cache_limit=2)
    descriptors = [
        {"op": "query.alpha", "verb": "ask", "summary": "look up history"},
        {"op": "query.beta", "verb": "ask", "summary": "look up history"},
    ]
    ranked = resolver.rank(
        "ask",
        "opaque words",
        descriptors,
        scope_ref=None,
        semantic_scores={"query.beta": 0.9},
    )
    assert ranked[0].descriptor.id == "query.beta"
    finite = resolver.rank(
        "ask",
        "opaque words",
        descriptors,
        scope_ref=None,
        semantic_scores={"query.alpha": float("nan")},
    )
    assert all(candidate.score == candidate.score for candidate in finite)
    for intent in ("first", "second", "third"):
        resolver.rank("ask", intent, descriptors, scope_ref=None)
    assert len(resolver._cache) == 2
