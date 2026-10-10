"""Typed model for the SCIM 2.0 service credential (GRAPHOS-IDENTITY-R011).

Slices .1 (credential) and .2.1 (User resource model); handlers and routes follow.
The create/update/patch/deactivate server surface is a later slice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .engine import IdentityUnavailable


@dataclass(frozen=True, slots=True)
class ScimServiceCredential:
    """A SCIM service credential, scoped to exactly one provider."""

    provider_id: str
    token_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise IdentityUnavailable("SCIM credential requires a provider id")
        if not isinstance(self.token_id, str) or not self.token_id:
            raise IdentityUnavailable("SCIM credential requires a token id")

    def authorizes(self, requested_provider_id: str) -> bool:
        """A token scoped to a different provider is refused by its caller."""
        return requested_provider_id == self.provider_id


SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


@dataclass(frozen=True, slots=True)
class ScimUser:
    """A validated SCIM 2.0 User resource (GRAPHOS-IDENTITY-R011.2.1)."""

    user_name: str
    active: bool = True
    external_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_name, str) or not self.user_name.strip():
            raise IdentityUnavailable("SCIM user requires a userName")
        if not isinstance(self.active, bool):
            raise IdentityUnavailable("SCIM user active must be a boolean")
        if self.external_id is not None and (
            not isinstance(self.external_id, str) or not self.external_id
        ):
            raise IdentityUnavailable("SCIM user externalId must be non-empty")

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ScimUser:
        """Parse a SCIM User payload, refusing a malformed one."""
        schemas = payload.get("schemas")
        if not isinstance(schemas, list) or SCIM_USER_SCHEMA not in schemas:
            raise IdentityUnavailable("SCIM payload lacks the User schema")
        user_name = payload.get("userName")
        external_id = payload.get("externalId")
        active = payload.get("active", True)
        if not isinstance(active, bool):
            raise IdentityUnavailable("SCIM user active must be a boolean")
        return cls(
            user_name=user_name if isinstance(user_name, str) else "",
            active=active,
            external_id=external_id if isinstance(external_id, str) else None,
        )


class ScimCredentialRefused(IdentityUnavailable):
    """A SCIM request carried a missing, unknown or wrong-provider credential."""


def check_scim_credential(
    credentials: Mapping[str, ScimServiceCredential],
    bearer_token: str | None,
    requested_provider_id: str,
) -> ScimServiceCredential:
    """Gate a SCIM request on its bearer token (GRAPHOS-IDENTITY-R011.2.2).

    ``credentials`` maps bearer tokens to their provider-scoped credential.
    Fails closed with ``ScimCredentialRefused`` for a missing or unknown token
    or one scoped to a different provider.
    """
    if not isinstance(bearer_token, str) or not bearer_token:
        raise ScimCredentialRefused("SCIM request lacks a bearer credential")
    credential = credentials.get(bearer_token)
    if credential is None:
        raise ScimCredentialRefused("SCIM bearer credential is unknown")
    if not credential.authorizes(requested_provider_id):
        raise ScimCredentialRefused("SCIM credential is scoped to a different provider")
    return credential
