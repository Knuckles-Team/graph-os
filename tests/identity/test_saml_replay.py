"""Replay check for the .2.3.1 slice: R012.2.3.1."""

from datetime import UTC, datetime, timedelta

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.saml import (
    ParsedSamlAssertion,
    SamlAssertionRefused,
    SamlRefusalReason,
    check_assertion_replay,
)

_T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class _FakeStore:
    def __init__(self) -> None:
        self.seen: dict[str, datetime] = {}

    def register(self, assertion_id: str, expires_at: datetime) -> bool:
        if assertion_id in self.seen:
            return False
        self.seen[assertion_id] = expires_at
        return True


def _assertion(assertion_id: str = "_a1") -> ParsedSamlAssertion:
    return ParsedSamlAssertion(
        assertion_id=assertion_id,
        issuer="https://idp.example.org",
        subject="alice",
        audiences=("urn:graph-os:sp",),
        recipient="https://graph-os.example.org/saml/acs",
        not_before=_T0 - timedelta(minutes=1),
        not_on_or_after=_T0 + timedelta(minutes=5),
    )


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.3.1")
def test_first_assertion_is_accepted_and_recorded() -> None:
    store = _FakeStore()
    check_assertion_replay(_assertion(), store)
    assert store.seen == {"_a1": _T0 + timedelta(minutes=5)}


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.3.1")
def test_repeated_assertion_id_is_refused_as_replayed() -> None:
    store = _FakeStore()
    check_assertion_replay(_assertion(), store)
    with pytest.raises(SamlAssertionRefused) as info:
        check_assertion_replay(_assertion(), store)
    assert info.value.reason is SamlRefusalReason.REPLAYED


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.3.1")
def test_distinct_assertion_ids_are_both_accepted() -> None:
    store = _FakeStore()
    check_assertion_replay(_assertion("_a1"), store)
    check_assertion_replay(_assertion("_a2"), store)


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.3.1")
def test_missing_store_fails_closed() -> None:
    with pytest.raises(IdentityUnavailable):
        check_assertion_replay(_assertion(), None)
