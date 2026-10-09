#!/usr/bin/python
"""Typed public-ingress identity profile model (GRAPHOS-INGRESS-R001.1).

CONCEPT:AU-OS.deployment.public-ingress-identity — see
``specs/public-ingress-identity/plan.md`` ("Profile and interface contract").
This slice is the versioned public-origin, TLS-reference, exact-callback and
trusted-proxy contract the plan calls for, as a closed-schema value object
with its own load-time refusals (test-spec.md T-IN-01/T-IN-02). It is
deliberately standalone: it does not yet extend
:class:`graph_os.deployment.genesis_environments.EnvironmentProfile`, is not
wired into the gateway/MCP identity admission path, and renders no live
config. That wiring is GRAPHOS-INGRESS-R001.2+.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .genesis_environments import EnvironmentProfileError

#: The only schema version this slice accepts. A profile migration (mapping
#: old keys to new ones) is out of scope for this slice — see plan.md
#: "A profile migration must explicitly map old keys to new keys."
SUPPORTED_VERSION = 1

#: Same reference grammar as
#: ``graph_os.deployment.genesis_environments._SECRET_REF_RE`` — a secret is
#: always a reference, never a value.
_SECRET_REF_RE = re.compile(
    r"^(?:"
    r"env://[A-Za-z_][A-Za-z0-9_]{0,127}"
    r"|vault://\S+"
    r"|secret://\S+"
    r"|k8s-secret://[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?/[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?"
    r")$"
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_REQUIRED_KEYS = (
    "version",
    "public_origin",
    "tls_secret_ref",
    "callback_uri",
    "trusted_proxy",
)


class PublicIngressProfileError(EnvironmentProfileError):
    """A public-ingress profile mapping is missing, malformed, or fails validation.

    Always names the exact key at fault. Never includes a secret value — this
    schema only ever carries secret *references*, matching
    ``secrets.required`` elsewhere in the genesis profile.
    """


@dataclass(frozen=True)
class PublicIngressProfile:
    """Canonical origin, TLS reference, exact callback and trusted-proxy identity.

    Closed schema: an omitted or unrecognized key is a load-time
    :class:`PublicIngressProfileError`, never a silently-filled default — see
    plan.md ("No implicit domain or IdP default.").
    """

    version: int
    public_origin: str
    tls_secret_ref: str
    callback_uri: str
    trusted_proxy: str

    @property
    def profile_digest(self) -> str:
        """A stable, non-secret fingerprint of this profile's effective config."""
        material = "|".join(
            (
                str(self.version),
                self.public_origin,
                self.callback_uri,
                self.trusted_proxy,
            )
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def redacted_render(self) -> dict[str, str]:
        """A reviewable effective configuration — never a secret value.

        Only the *name* of the TLS secret reference is shown, matching the
        existing ``secrets.required`` convention of referencing, not
        resolving, credentials.
        """
        return {
            "version": str(self.version),
            "public_origin": self.public_origin,
            "callback_uri": self.callback_uri,
            "trusted_proxy": self.trusted_proxy,
            "tls_secret_ref": self.tls_secret_ref,
            "profile_digest": self.profile_digest,
        }


def _is_https_or_loopback(origin: str) -> bool:
    if origin.startswith("https://"):
        return True
    if origin.startswith("http://"):
        host = origin[len("http://") :].split("/", 1)[0].split(":", 1)[0]
        return host in _LOOPBACK_HOSTS
    return False


def _validate_keys(raw: Mapping[str, Any]) -> None:
    unknown = set(raw) - set(_REQUIRED_KEYS)
    if unknown:
        raise PublicIngressProfileError(
            f"public ingress profile has unknown key(s): {sorted(unknown)}."
        )
    missing = [key for key in _REQUIRED_KEYS if key not in raw]
    if missing:
        raise PublicIngressProfileError(
            f"public ingress profile missing required key(s): {missing}."
        )


def _validate_version(version: Any) -> int:
    if not isinstance(version, int) or version != SUPPORTED_VERSION:
        raise PublicIngressProfileError(
            f"public ingress profile 'version' must be {SUPPORTED_VERSION}, "
            f"got {version!r}."
        )
    return version


def _validate_public_origin(public_origin: Any) -> str:
    if (
        not isinstance(public_origin, str)
        or "*" in public_origin
        or not _is_https_or_loopback(public_origin)
    ):
        raise PublicIngressProfileError(
            "public ingress profile 'public_origin' must be an https:// URL "
            "(or a loopback http:// URL) with no wildcard Host, got "
            f"{public_origin!r}."
        )
    return public_origin


def _validate_tls_secret_ref(tls_secret_ref: Any) -> str:
    if not isinstance(tls_secret_ref, str) or not _SECRET_REF_RE.match(tls_secret_ref):
        raise PublicIngressProfileError(
            "public ingress profile 'tls_secret_ref' must be a reference "
            "(env://, vault://, secret://, or k8s-secret://<ns>/<name>), got "
            f"{tls_secret_ref!r}."
        )
    return tls_secret_ref


def _validate_callback_uri(callback_uri: Any, public_origin: str) -> str:
    if not isinstance(callback_uri, str) or not callback_uri.startswith(
        f"{public_origin}/"
    ):
        raise PublicIngressProfileError(
            "public ingress profile 'callback_uri' must be an exact URI under "
            f"'public_origin' ({public_origin!r}), got {callback_uri!r}."
        )
    return callback_uri


def _validate_trusted_proxy(trusted_proxy: Any) -> str:
    if not isinstance(trusted_proxy, str) or not trusted_proxy or "*" in trusted_proxy:
        raise PublicIngressProfileError(
            "public ingress profile 'trusted_proxy' must name a specific "
            f"proxy identity (no wildcard), got {trusted_proxy!r}."
        )
    return trusted_proxy


def public_ingress_profile_from_mapping(raw: Any) -> PublicIngressProfile:
    """Build and validate a :class:`PublicIngressProfile` from a plain mapping.

    Raises :class:`PublicIngressProfileError` naming the exact key for every
    refusal case in test-spec.md T-IN-02: an unknown field, a missing/malformed
    TLS reference, a malformed callback, an HTTP origin that is not loopback,
    or a wildcard Host/proxy.
    """
    if not isinstance(raw, Mapping):
        raise PublicIngressProfileError(
            f"public ingress profile must be a mapping, got {type(raw).__name__}."
        )

    _validate_keys(raw)
    version = _validate_version(raw["version"])
    public_origin = _validate_public_origin(raw["public_origin"])
    tls_secret_ref = _validate_tls_secret_ref(raw["tls_secret_ref"])
    callback_uri = _validate_callback_uri(raw["callback_uri"], public_origin)
    trusted_proxy = _validate_trusted_proxy(raw["trusted_proxy"])

    return PublicIngressProfile(
        version=version,
        public_origin=public_origin,
        tls_secret_ref=tls_secret_ref,
        callback_uri=callback_uri,
        trusted_proxy=trusted_proxy,
    )
