"""Caller-generated identity secrets.

The engine stores only hashes (or a sealed blob) of these values and enforces
their entropy floor at its request boundary; GraphOS generates them from the
OS CSPRNG, shows the user-facing ones once, and forgets them.
"""

from __future__ import annotations

import base64
import ipaddress
import secrets
from dataclasses import dataclass
from urllib.parse import quote

__all__ = [
    "API_KEY_PREFIX",
    "ApiKeySecret",
    "client_ip_prefix",
    "new_api_key",
    "new_recovery_codes",
    "new_token",
    "new_totp_secret",
    "parse_api_key",
    "totp_provisioning_uri",
]

#: Public prefix of a presented API key: ``gok_<key id>.<secret>``.
API_KEY_PREFIX = "gok_"
#: 256-bit tokens: 43 URL-safe base64 characters, over the engine's floor.
_TOKEN_BYTES = 32
_RECOVERY_CODE_COUNT = 10
_RECOVERY_GROUP_CHARS = 5
_RECOVERY_GROUPS = 4
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
#: RFC 4226 §4: at least 160 bits of shared secret.
_TOTP_SECRET_BYTES = 20


def new_token() -> str:
    """A 256-bit session id, one-time token or API-key secret."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


@dataclass(frozen=True)
class ApiKeySecret:
    """A new API key: its public id, its secret, and the string shown once."""

    key_id: str
    secret: str

    @property
    def presented(self) -> str:
        return f"{API_KEY_PREFIX}{self.key_id}.{self.secret}"


def new_api_key() -> ApiKeySecret:
    return ApiKeySecret(key_id=secrets.token_hex(8), secret=new_token())


def parse_api_key(presented: str) -> ApiKeySecret | None:
    """Split a presented key; ``None`` when it is not API-key shaped."""
    if not presented.startswith(API_KEY_PREFIX):
        return None
    key_id, dot, secret = presented[len(API_KEY_PREFIX) :].partition(".")
    if not dot or not key_id or not secret:
        return None
    return ApiKeySecret(key_id=key_id, secret=secret)


def new_totp_secret() -> str:
    """A 160-bit base32 TOTP secret (32 characters, no padding)."""
    raw = secrets.token_bytes(_TOTP_SECRET_BYTES)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def totp_provisioning_uri(secret: str, account: str, issuer_label: str) -> str:
    """The ``otpauth://`` URI an authenticator app enrolls from."""
    label = quote(f"{issuer_label}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer_label, safe='')}&algorithm=SHA1&digits=6&period=30"
    )


def _recovery_code() -> str:
    groups = (
        "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_GROUP_CHARS))
        for _ in range(_RECOVERY_GROUPS)
    )
    return "-".join(groups)


def new_recovery_codes() -> list[str]:
    """Ten single-use codes, each ~98 bits, typeable from paper."""
    return [_recovery_code() for _ in range(_RECOVERY_CODE_COUNT)]


def client_ip_prefix(address: str | None) -> str | None:
    """Truncate a client address for audit and throttling (/24 v4, /48 v6)."""
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    bits = 24 if parsed.version == 4 else 48
    return str(ipaddress.ip_network(f"{parsed}/{bits}", strict=False))
