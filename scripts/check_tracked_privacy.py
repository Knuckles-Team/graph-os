#!/usr/bin/env python3
"""Reject host-specific data and local identities from tracked public artifacts.

Copied verbatim from epistemic-graph's `scripts/check_tracked_privacy.py`
(commit b5292e01f1f860dc0480e6babdd6e1d7c41e09e0). No further adaptation
needed here: EG's own port from agent-utilities already replaced the
AU-tree-specific `_is_runtime_source_path` allowlist with a suffix-only rule
that scopes correctly to ANY repository's tree shape (see that function's
docstring below) — the exact problem a second, graph-os-specific allowlist
would have needed to solve is already solved generically.

Identifiers are derived in memory from the current account, home, checkout and
host. Findings report only ``file:line`` and a category; the sensitive matched
value is never written or printed. Machine paths and persisted path fields are
checked independently, so the gate remains useful in clean CI environments.

Ported verbatim from agent-utilities' ``scripts/check_tracked_privacy.py``
(guardrail-tracked-privacy), which already passes green on agent-utilities'
own ``main`` -- reused rather than reinvented per this fleet's
Extend-Before-Invent convention. The ONLY behavioral change from the
agent-utilities original is ``_is_runtime_source_path`` below, which no
longer gates on agent-utilities' OWN top-level directory allowlist
(``agent_utilities``, ``deploy``, ``docker``, ...) -- that allowlist is
specific to that repo's tree shape, and porting it unmodified into a repo
with a different tree would silently scan an EMPTY universe for every
directory not on the borrowed list: a confident green verdict over zero
files, the exact "gates that report more coverage than they have" failure
this fleet has hit before. See that function's own docstring for the
replacement rule.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import ipaddress
import os
import re
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# R-07: `pwd` is POSIX-only and raises ImportError at import time on Windows.
# It only ever supplies one extra candidate identifier (the passwd-db
# username) alongside several already-portable ones (getpass.getuser(),
# hostname, env vars, home-dir name) below, so on Windows this module simply
# runs with one fewer redundant source instead of failing to import at all.
if sys.platform != "win32":
    import pwd
else:  # pragma: no cover - exercised only on Windows
    pwd = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_subprocess_env import (  # noqa: E402
    sanitized_git_env,
    strip_inherited_git_repository_env,
)

# NE-059 (sibling of BUG-180/D-LGI-1, ported from agent-utilities' own fix):
# every ``git`` subprocess this module shells out to -- ``derive_local_
# identifiers``'s ``git rev-parse --git-common-dir``/``git config --get
# user.name|email`` and ``_git_file_names``'s ``git ls-files`` -- can inherit
# a real ``git commit``/``git push``'s exported ``GIT_DIR``/``GIT_INDEX_FILE``
# unmodified when this script runs as its own pre-commit hook: neither call
# passed its own ``env=``, and those vars win over ``cwd=``'s path-based
# repository discovery. As a *privacy/security* gate, a poisoned resolution
# is worse than a crash -- it can silently swap in a different repository's
# tracked-file inventory or let a decoy's exclude rules drop a real leaking
# file from the sweep, so the gate reports PASS having never looked at the
# file that leaks. Strip once, process-wide, at import time *and* pass an
# explicit sanitized ``env=`` at each call site, so no future call in this
# module can regress silently by omitting the strip precondition.
strip_inherited_git_repository_env()

ROOT = Path(__file__).resolve().parent.parent

_TEXT_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml", ".toml"})
_SOURCE_SUFFIXES = frozenset(
    {".js", ".md", ".ps1", ".py", ".rs", ".sh", ".ts", ".yaml", ".yml"}
)
_GENERIC_IDENTIFIERS = frozenset(
    {
        "admin",
        "agent",
        "apps",
        "build",
        "developer",
        "genius",
        "home",
        "localhost",
        "maintainer",
        "maintainers",
        "root",
        "runner",
        "service",
        "user",
        "workspace",
    }
)
_HOME_PATH_PATTERN = (
    r"(?:(?<![A-Za-z0-9_.-])/home/(?P<home_user>[A-Za-z0-9_.-]+)(?:/|\b)|"
    r"(?<![A-Za-z0-9_.-])/Users/(?P<users_user>[A-Za-z0-9_.-]+)(?:/|\b)|"
    r"(?<![A-Za-z0-9_.-])/mnt/[A-Za-z]/Users/(?P<mnt_user>[A-Za-z0-9_.-]+)(?:/|\b)|"
    # BUG-228: this branch had no left boundary guard (unlike the three
    # above), so a REST route id like ``"route:GET:/users/{id}"`` spuriously
    # matched it -- the "T" ending "GET" read as a fake drive letter. The
    # same lookbehind the other branches already use fixes it: a real drive
    # letter is never itself preceded by another identifier character.
    r"(?<![A-Za-z0-9_.-])[A-Za-z]:[\\/]Users[\\/](?P<win_user>[^\\/\s]+)(?:[\\/]|\b))"
)
_HOME_PATH_RE = re.compile(_HOME_PATH_PATTERN, re.IGNORECASE)
# BUG-228: usernames this repo's own tests use, over and over, as a
# documented "this is not a real account" stand-in -- generic role nouns
# (operator/user/local/app/account/person), the RFC 2606 "example" word and
# its natural variants (example/example-user/exampleuser), classic
# protocol-documentation personas (alice/bob, same convention IETF RFCs
# use), this repo's own synthetic-account naming idiom (agent-user and
# other ``*-account`` fixtures), and single-letter stand-ins (a/u). None of
# these can identify a real person or host; only the ambient candidates
# :func:`derive_local_identifiers` derives (the actual current account) and
# a handful of specific real names (e.g. the developer account, the real
# workspace root) do that, and those are NOT in this set on purpose.
_RESERVED_HOME_USERS = frozenset(
    {
        "a",
        "a-different-account",
        "account",
        "agent-user",
        "alice",
        "app",
        "bob",
        "example",
        "example-user",
        "exampleuser",
        "local",
        "local-account",
        "operator",
        "person",
        "sensitive-account",
        "some-account",
        "u",
        "user",
    }
)


def _home_path_user(match: re.Match[str]) -> str | None:
    for name in ("home_user", "users_user", "mnt_user", "win_user"):
        value = match.groupdict().get(name)
        if value:
            return value
    return None


def _is_reserved_home_user(user: str) -> bool:
    return user.strip("\\/").casefold() in _RESERVED_HOME_USERS


def _has_real_home_path(line: str) -> bool:
    """True if any home-path match on this line is NOT a reserved placeholder.

    A line can carry more than one match (e.g. a before/after pair); it is
    only a real leak if at least one of them is not a documented stand-in.
    """
    return any(
        not _is_reserved_home_user(user)
        for match in _HOME_PATH_RE.finditer(line)
        for user in (_home_path_user(match),)
        if user is not None
    )


# BUG-228: RFC 2606/6761 documentation domains, RFC 5737/3927/3849 reserved
# address blocks, and localhost can never resolve to (or disclose) a real
# host, so a fixture built on one of these is provably synthetic -- never a
# leak, regardless of what appears before the ``://``.
_RESERVED_DOCUMENTATION_TLDS = frozenset({"test", "example", "invalid", "localhost"})
_RESERVED_EXAMPLE_DOMAINS = frozenset({"example.com", "example.net", "example.org"})
_RESERVED_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",  # RFC 5735 loopback (0.0.0.0/32 below is separate)
        "0.0.0.0/32",
        "192.0.2.0/24",  # RFC 5737 TEST-NET-1
        "198.51.100.0/24",  # RFC 5737 TEST-NET-2
        "203.0.113.0/24",  # RFC 5737 TEST-NET-3
        "169.254.0.0/16",  # RFC 3927 link-local
    )
)
_RESERVED_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "fe80::/10",  # RFC 4291 link-local
        "2001:db8::/32",  # RFC 3849 documentation
    )
)


def _is_localhost_name(candidate: str) -> bool:
    """``localhost`` or any ``*.localhost`` name (RFC 6761)."""
    return candidate == "localhost" or candidate.endswith(".localhost")


def _is_docker_internal_name(candidate: str) -> bool:
    """CX-RAT-09: ``host.docker.internal`` is Docker's OWN published, universal
    convention -- it names no host specific to any one deployment; every
    Docker install answers to it identically.
    """
    return candidate == "host.docker.internal"


def _is_reserved_example_domain(candidate: str) -> bool:
    """RFC 2606 ``example.com``/``.net``/``.org`` and their subdomains."""
    return candidate in _RESERVED_EXAMPLE_DOMAINS or any(
        candidate.endswith(f".{domain}") for domain in _RESERVED_EXAMPLE_DOMAINS
    )


def _is_example_prefixed_label(candidate: str) -> bool:
    """BUG-241: leading label is ``example`` or ``example-*``."""
    labels = candidate.split(".")
    return labels[0] == "example" or labels[0].startswith("example-")


def _is_documentation_tld(candidate: str) -> bool:
    """Trailing label is an RFC 2606 documentation TLD."""
    return candidate.split(".")[-1] in _RESERVED_DOCUMENTATION_TLDS


def _is_reserved_documentation_address(candidate: str) -> bool:
    """RFC 5737/3927/3849 documentation-reserved IPv4/IPv6 address block."""
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    networks = (
        _RESERVED_IPV6_NETWORKS if address.version == 6 else _RESERVED_IPV4_NETWORKS
    )
    return any(address in network for network in networks)


_RESERVED_HOSTNAME_CHECKS = (
    _is_localhost_name,
    _is_docker_internal_name,
    _is_reserved_example_domain,
    _is_example_prefixed_label,
    _is_documentation_tld,
    _is_reserved_documentation_address,
)


def _is_reserved_hostname(host: str) -> bool:
    """True for an RFC-reserved-for-documentation hostname/address."""
    candidate = host.strip().rstrip(".").casefold()
    if not candidate:
        return False
    return any(check(candidate) for check in _RESERVED_HOSTNAME_CHECKS)


_PERSISTED_FIELD_RE = re.compile(
    r"[\"']?(?P<field>workspace_path|source_path|skill_path|local_path|source_file|"
    r"eg_ledger_path)[\"']?\s*[:=]\s*(?P<value>.+)",
    re.IGNORECASE,
)
_NEUTRAL_URI_RE = re.compile(
    r"^[\s\"']*(?:repo|skill|connector|design)://", re.IGNORECASE
)
_INTERNAL_ENDPOINT_RE = re.compile(
    r"(?i)\b(?:[A-Za-z0-9-]+\.)+(?:arpa|internal)\b|"
    r"\b(?:[A-Za-z0-9-]+\.)*svc\.cluster\.local\b|"
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
_HOSTNAME_LABEL_RE = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_BARE_INTERNAL_HOSTNAME_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?:"
    rf"(?:{_HOSTNAME_LABEL_RE}\.)+(?:arpa|internal|corp|lan)"
    r"|"
    rf"(?:{_HOSTNAME_LABEL_RE}\.)*svc\.cluster\.local"
    r")"
    r"(?![A-Za-z0-9_.-])"
)


def _internal_endpoint_in_line(line: str) -> bool:
    """True if ``line`` contains a non-reserved internal-hostname literal."""
    return any(
        not _is_reserved_hostname(match.group(0))
        for match in _BARE_INTERNAL_HOSTNAME_RE.finditer(line)
    )


_PRIVATE_KEY_LINE_RE = re.compile(r"^\s*-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----\s*$")
_CREDENTIAL_URI_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:(?P<secret>[^\s/@]+)"
    r"@(?P<cred_host>[^\s/@:\"'<>]+)"
)
_CREDENTIAL_PLACEHOLDER_TOKENS = frozenset(
    {
        "agent",
        "changeme",
        "change_me",
        "example",
        "fixme",
        "masked",
        "password",
        "placeholder",
        "redacted",
        "replace",
        "sample",
        "secret",
        "test",
        "todo",
        "xxxx",
        "your",
    }
)
_HOST_IDENTITY_RE = re.compile(r"(?i)\bssh://(?!\$\{)[^\s/@]+@")
_MACHINE_HOST_ID_RE = re.compile(r"(?i)(?<![a-z0-9])(?:rw?|host)[0-9]{3,}(?![a-z0-9])")
_NEUTRAL_AUTHOR_NAME = "repository maintainers"
_NEUTRAL_AUTHOR_EMAIL_SUFFIX = "@example.invalid"
_SCAN_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".acp-sessions",
        ".benchmarks",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".pytest_tmp",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site",
        "target",
        "venv",
        "workspace",
    }
)
_MAX_SCAN_FILES = 500_000


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    category: str
    content_hash: str
    ordinal: int

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.category}"


def _content_hash(text: str) -> str:
    """Irreversible fingerprint of a line's content, never the value itself."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_credential_placeholder(secret: str) -> bool:
    rendered = secret.strip()
    if not rendered:
        return True
    if re.fullmatch(r"(?:\*+|#+|x{4,})", rendered, flags=re.IGNORECASE):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", rendered.lower()))
    return bool(tokens & _CREDENTIAL_PLACEHOLDER_TOKENS)


def _is_credential_exempt(match: re.Match[str]) -> bool:
    """A credential-shaped URI is not a real leak when either the secret
    token is a documented placeholder word OR the host it targets is
    RFC-reserved-for-documentation.
    """
    if _is_credential_placeholder(match.group("secret")):
        return True
    host = match.group("cred_host")
    return bool(host) and _is_reserved_hostname(host)


def _identifier_from_path(value: str) -> set[str]:
    identifiers: set[str] = set()
    normalized = value.replace("\\", "/")
    for pattern in (r"/home/([^/]+)", r"/Users/([^/]+)"):
        identifiers.update(re.findall(pattern, normalized, flags=re.IGNORECASE))
    return identifiers


def _identifiers_from_override(override: str) -> frozenset[str]:
    """Declared identifiers from ``AGENT_UTILITIES_PRIVACY_IDENTIFIERS``."""
    declared = {
        value.strip() for value in re.split(r"[,\n]", override) if value.strip()
    }
    return frozenset(
        value.casefold()
        for value in declared
        if len(value) >= 4 and value.casefold() not in _GENERIC_IDENTIFIERS
    )


def _os_identifier_candidates() -> set[str]:
    """Ambient OS username/hostname/home-dir candidates (unfiltered)."""
    candidates = {
        getpass.getuser(),
        socket.gethostname(),
        socket.gethostname().split(".", 1)[0],
        os.environ.get("USER", ""),
        os.environ.get("LOGNAME", ""),
        os.environ.get("USERNAME", ""),
        Path.home().name,
    }
    if pwd is not None:  # POSIX: one more redundant source, see import above
        candidates.add(pwd.getpwuid(os.getuid()).pw_name)
    candidates.update(_identifier_from_path(str(Path.home())))
    return candidates


def _git_common_dir_identifiers(root: Path) -> set[str]:
    """Identifiers embedded in the checkout's own ``--git-common-dir`` path."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=sanitized_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return _identifier_from_path(result.stdout.strip())


def _git_config_identifiers(root: Path) -> set[str]:
    """The calling process's own ``git config user.name``/``user.email``."""
    candidates: set[str] = set()
    for command in (
        ["git", "config", "--get", "user.name"],
        ["git", "config", "--get", "user.email"],
    ):
        try:
            result = subprocess.run(
                command,
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                env=sanitized_git_env(),
            )
        except OSError:
            continue
        for value in result.stdout.splitlines():
            candidates.add(value.strip())
    return candidates


def derive_local_identifiers(root: Path = ROOT) -> frozenset[str]:
    """Identifiers this gate treats as sensitive if they appear in tracked text.

    D-ORC-57: purely ambient derivation (OS username, hostname, the CALLING
    process's local git config) makes the verdict depend on who/where the
    scan runs, not on the tree being scanned.

    ``AGENT_UTILITIES_PRIVACY_IDENTIFIERS`` is a DECLARED, stable override:
    when set (comma- or newline-separated), it is used INSTEAD of the
    ambient OS/git-config-derived candidates below, so CI/pre-commit can pin
    one deterministic identity set regardless of which sandbox or host runs
    the scan. Kept as the exact fleet-wide variable name (shared with
    agent-utilities and epistemic-graph) rather than a graph-os-specific one,
    so one operator override works across every repository in the fleet.
    """
    override = os.environ.get("AGENT_UTILITIES_PRIVACY_IDENTIFIERS", "").strip()
    if override:
        return _identifiers_from_override(override)

    candidates = _os_identifier_candidates()
    candidates.update(_git_common_dir_identifiers(root))
    candidates.update(_git_config_identifiers(root))
    return frozenset(
        value.casefold()
        for value in candidates
        if value and len(value) >= 4 and value.casefold() not in _GENERIC_IDENTIFIERS
    )


def _is_deployment_doc(path: Path) -> bool:
    value = path.as_posix().casefold()
    return value.startswith("docs/recipes/") or any(
        marker in value
        for marker in (
            "deploy",
            "runbook",
            "configuration",
            "workspace-config",
            "mcp_auth",
            "secrets-auth",
        )
    )


def _persisted_path_category(line: str) -> str | None:
    """"persisted machine path" if the line assigns a non-neutral path field."""
    persisted = _PERSISTED_FIELD_RE.search(line)
    if not persisted or _NEUTRAL_URI_RE.search(persisted.group("value")):
        return None
    value = persisted.group("value").strip(" \t,;)}]\"'").casefold()
    field = persisted.group("field")
    is_template_placeholder = value.startswith("${")
    runtime_relative = (
        field.isupper() or is_template_placeholder
    ) and not re.match(r"^(?:[a-z]:|[/\\]|~)", value, re.IGNORECASE)
    if value in {"", "none", "null", "unset"} or runtime_relative:
        return None
    return "persisted machine path"


def _identifier_category(folded_line: str, identifiers: frozenset[str]) -> str | None:
    """"local account or host identifier" if any derived identifier appears."""
    matches = any(
        re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", folded_line)
        for value in identifiers
    )
    return "local account or host identifier" if matches else None


def _deployment_doc_categories(line: str) -> frozenset[str]:
    """Categories that only apply inside a deployment/recipe doc."""
    categories: set[str] = set()
    if _INTERNAL_ENDPOINT_RE.search(line):
        categories.add("hard-coded internal endpoint")
    credential_match = _CREDENTIAL_URI_RE.search(line)
    if credential_match and not _is_credential_exempt(credential_match):
        categories.add("credential-bearing URI")
    if _HOST_IDENTITY_RE.search(line):
        categories.add("hard-coded remote account")
    return frozenset(categories)


def classify_line(
    line: str,
    *,
    identifiers: frozenset[str],
    deployment_doc: bool,
) -> frozenset[str]:
    categories: set[str] = set()
    persisted_category = _persisted_path_category(line)
    if persisted_category:
        categories.add(persisted_category)
    elif _has_real_home_path(line):
        categories.add("machine-specific home path")
    identifier_category = _identifier_category(line.casefold(), identifiers)
    if identifier_category:
        categories.add(identifier_category)
    if _MACHINE_HOST_ID_RE.search(line):
        categories.add("machine-specific host identifier")
    if deployment_doc:
        categories |= _deployment_doc_categories(line)
    return frozenset(categories)


def classify_runtime_source_line(
    line: str, *, identifiers: frozenset[str]
) -> frozenset[str]:
    """Classify runtime/deployment source without applying public-doc path heuristics.

    Source code legitimately manipulates path-shaped values, so the generic
    ``source_path = ...`` rule would be noisy here. Concrete account paths,
    environment endpoints, credential-bearing URLs, and local identities are
    never legitimate package defaults and are checked for every shipped
    runtime and deployment file instead.
    """

    categories: set[str] = set()
    if _has_real_home_path(line):
        categories.add("machine-specific home path in runtime source")
    folded = line.casefold()
    if any(
        re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", folded)
        for value in identifiers
    ):
        categories.add("local account or host identifier in runtime source")
    if _internal_endpoint_in_line(line):
        categories.add("hard-coded internal endpoint in runtime source")
    credential_match = _CREDENTIAL_URI_RE.search(line)
    if credential_match and not _is_credential_exempt(credential_match):
        categories.add("credential-bearing URI in runtime source")
    if _PRIVATE_KEY_LINE_RE.fullmatch(line):
        categories.add("private key material in runtime source")
    return frozenset(categories)


# BUG-228: this used to be {"docs", ".github"} plus top-level files and any
# *.toml, which is why a leak that landed under `tests/` or `.specify/` was
# structurally invisible to this pass -- the gate's own selection excluded
# the tree, not the file type. A tracked test fixture in a PUBLIC repo
# discloses exactly as much as tracked source.
_PUBLIC_TEXT_TREES = frozenset({"docs", ".github", "tests", ".specify", "examples"})


def _is_public_artifact(name: str) -> bool:
    path = Path(name)
    if path.suffix.casefold() not in _TEXT_SUFFIXES:
        return False
    if "skills" in path.parts:
        return False
    return (
        path.parts[0] in _PUBLIC_TEXT_TREES
        or len(path.parts) == 1
        or path.suffix.casefold() == ".toml"
    )


def _is_traversable_directory(current: Path, name: str) -> bool:
    """A real, non-excluded, non-symlink subdirectory worth walking into."""
    if name in _SCAN_EXCLUDED_DIRECTORIES or name.endswith(".egg-info"):
        return False
    metadata = (current / name).lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return False
    return stat.S_ISDIR(metadata.st_mode)


def _regular_files_under(current: Path, file_names: list[str]) -> list[Path]:
    """Every plain (non-symlink, non-special) file among ``file_names``."""
    found: list[Path] = []
    for name in sorted(file_names):
        path = current / name
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            found.append(path)
    return found


def _filesystem_files(root: Path) -> list[Path]:
    """Enumerate a bounded no-Git source snapshot without following links."""

    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, topdown=True):
        current = Path(directory)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if _is_traversable_directory(current, name)
        )
        for path in _regular_files_under(current, file_names):
            files.append(path)
            if len(files) > _MAX_SCAN_FILES:
                raise RuntimeError("privacy source inventory exceeds its file bound")
    return files


def _git_file_names(root: Path, command: list[str]) -> list[str] | None:
    """Return Git inventory names, or ``None`` for an immutable no-Git snapshot."""

    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=sanitized_git_env(),
    )
    if result.returncode != 0:
        return None
    return [name for name in result.stdout.splitlines() if name]


def _tracked_artifacts(root: Path) -> list[Path]:
    names = _git_file_names(
        root,
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
    )
    candidates = (
        _filesystem_files(root) if names is None else [root / name for name in names]
    )
    return [
        path
        for path in candidates
        if _is_public_artifact(path.relative_to(root).as_posix())
    ]


def _runtime_source_artifacts(root: Path) -> list[Path]:
    """Every **tracked** runtime/deployment source path, not merely the changed ones."""

    names = _git_file_names(
        root,
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
    )
    candidates = (
        _filesystem_files(root) if names is None else [root / name for name in names]
    )
    return sorted(
        (
            path
            for path in candidates
            if path.suffix.casefold() in _SOURCE_SUFFIXES
            and _is_runtime_source_path(path.relative_to(root))
        ),
        key=lambda path: path.as_posix(),
    )


def _is_runtime_source_path(path: Path) -> bool:
    """Scope the source-literal pass to every tree that can carry a real leak.

    PORT NOTE: the upstream (agent-utilities) version gates this pass on
    agent-utilities' OWN top-level directory allowlist. That allowlist
    encodes ONE repo's tree shape; reusing it unmodified in a repo with a
    different tree would silently scan an EMPTY universe for every directory
    not on the borrowed list. This port drops the allowlist and scopes
    purely by suffix (``_SOURCE_SUFFIXES``, applied by the caller) plus
    git's own tracked/exclude-standard file set -- a directory that is
    gitignored is already invisible to
    ``git ls-files --cached --others --exclude-standard`` and never reaches
    this function at all, so widening this check costs nothing.
    """

    return bool(path.parts)


def _is_bundled_connector_profile(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return parts[:4] == (
        "agent_utilities",
        "protocols",
        "source_connectors",
        "profiles",
    ) and path.suffix.casefold() in {".py", ".json", ".yaml", ".yml"}


def _is_non_neutral_author_line(folded: str, *, in_project_authors: bool) -> bool:
    """True if this folded, stripped line is a non-neutral author assignment."""
    if re.match(r"authors\s*=", folded):
        return (
            _NEUTRAL_AUTHOR_NAME not in folded
            or _NEUTRAL_AUTHOR_EMAIL_SUFFIX not in folded
        )
    if in_project_authors and re.match(r"name\s*=", folded):
        return _NEUTRAL_AUTHOR_NAME not in folded
    if in_project_authors and re.match(r"email\s*=", folded):
        return _NEUTRAL_AUTHOR_EMAIL_SUFFIX not in folded
    return False


def _author_metadata_lines(path: Path, lines: list[str]) -> list[int]:
    """Return non-neutral package-author lines without returning their values."""
    if path.suffix.casefold() != ".toml":
        return []
    violations: list[int] = []
    in_project_authors = False
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == "[[project.authors]]":
            in_project_authors = True
            continue
        if in_project_authors and stripped.startswith("["):
            in_project_authors = False
        if _is_non_neutral_author_line(
            stripped.casefold(), in_project_authors=in_project_authors
        ):
            violations.append(number)
    return violations


def _next_ordinal(
    counts: dict[tuple[str, str, str], int], group: tuple[str, str, str]
) -> int:
    ordinal = counts.get(group, 0)
    counts[group] = ordinal + 1
    return ordinal


def _record_violation(
    violations: list[Violation],
    ordinals: dict[tuple[str, str, str], int],
    rel_str: str,
    number: int,
    category: str,
    content_hash: str,
) -> None:
    ordinal = _next_ordinal(ordinals, (rel_str, category, content_hash))
    violations.append(Violation(rel_str, number, category, content_hash, ordinal))


def _scan_tracked_artifact(
    path: Path,
    root: Path,
    identifiers: frozenset[str],
    violations: list[Violation],
    ordinals: dict[tuple[str, str, str], int],
) -> None:
    relative = path.relative_to(root)
    rel_str = relative.as_posix()
    deployment_doc = _is_deployment_doc(relative)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for number in _author_metadata_lines(path, lines):
        category = "non-neutral package author identity"
        content_hash = _content_hash(lines[number - 1])
        _record_violation(violations, ordinals, rel_str, number, category, content_hash)
    for number, line in enumerate(lines, 1):
        for category in classify_line(
            line,
            identifiers=identifiers,
            deployment_doc=deployment_doc,
        ):
            content_hash = _content_hash(line)
            _record_violation(
                violations, ordinals, rel_str, number, category, content_hash
            )


def _scan_runtime_source_artifact(
    path: Path,
    root: Path,
    identifiers: frozenset[str],
    violations: list[Violation],
    ordinals: dict[tuple[str, str, str], int],
) -> None:
    relative = path.relative_to(root)
    rel_str = relative.as_posix()
    if _is_bundled_connector_profile(relative):
        category = "bundled environment-specific connector profile"
        content_hash = _content_hash(rel_str)
        _record_violation(violations, ordinals, rel_str, 1, category, content_hash)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for number, line in enumerate(lines, 1):
        for category in classify_runtime_source_line(line, identifiers=identifiers):
            content_hash = _content_hash(line)
            _record_violation(
                violations, ordinals, rel_str, number, category, content_hash
            )


def scan(root: Path = ROOT) -> list[Violation]:
    identifiers = derive_local_identifiers(root)
    violations: list[Violation] = []
    ordinals: dict[tuple[str, str, str], int] = {}
    for path in _tracked_artifacts(root):
        if path.is_file():
            _scan_tracked_artifact(path, root, identifiers, violations, ordinals)
    for path in _runtime_source_artifacts(root):
        if path.is_file():
            _scan_runtime_source_artifact(path, root, identifiers, violations, ordinals)
    return violations


# CX-RAT-09: the baseline/ratchet mechanism is DELETED, not merely emptied. A
# count-based allowance is the wrong instrument for a leak-prevention gate on
# a repo that publishes to a PUBLIC GitHub org. ``MAX`` is an ABSOLUTE
# constant (not a per-repo drift budget).
MAX = 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="check-tracked-privacy")
    parser.parse_args()

    violations = scan()
    count = len(violations)
    print(f"Tracked artifact privacy gate: {count} finding(s) (MAX={MAX}).")

    if count > MAX:
        print("Tracked artifact privacy gate FAILED:")
        for violation in violations:
            print(f"  - {violation.render()}")
        print("Matched values are intentionally suppressed.")
        print(f"{count} leak(s) found; absolute maximum is {MAX}.")
        print(
            "resolution (forensic breadcrumb): "
            f"ROOT={ROOT} cwd={Path.cwd()} total_violations={count} "
            f"PRE_COMMIT_HOME={os.environ.get('PRE_COMMIT_HOME', '<unset>')!r}"
        )
        return 1
    print("Tracked artifact privacy gate PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
