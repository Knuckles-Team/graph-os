"""Scheduled LDAP / AD group sync (IDM-13).

Every ``sync_interval_s`` (default 15 min) each enabled ``kind=ldap`` IdP is
read in full and every user is sent to the engine's ``idp.provision``
(see ``CONTRACT-REQUEST.md`` A2): the engine links or creates the principal
(source ``ldap:<idp>``), recomputes its IdP-sourced roles and memberships from
the IdP's mapping rules -- so a group removed in the directory removes the
role without waiting for a sign-in -- and deprovisions a disabled AD account
(sessions and API keys revoked, owned data kept).

A principal this IdP provisioned that is no longer in the directory is
deprovisioned too, unless that would deprovision more than
``max_deprovision_ratio`` of the IdP's principals: a wrong base DN, an empty
answer or a partial outage must never lock everyone out. A directory error
aborts the IdP's run before anything is deprovisioned.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import anyio

from graph_os.identity.idp_common import (
    IdentityPort,
    IdentityRefused,
    IdpDirectory,
    IdpRecord,
    UnknownIdp,
    call_expect,
    identity_op,
)
from graph_os.identity.ldap import (
    DirectoryEntry,
    DirectoryFactory,
    DirectoryUnavailable,
    ldap_settings,
)

__all__ = ["LdapSync", "SyncReport", "provision_request"]

_LOG = logging.getLogger(__name__)
_PAGE = 500
_TICK_S = 30.0


@dataclass
class SyncReport:
    """What one IdP's run did."""

    idp_id: str
    provisioned: int = 0
    deprovisioned: int = 0
    refused: dict[str, str] = field(default_factory=dict)
    aborted: str | None = None


def provision_request(idp_id: str, entry: DirectoryEntry) -> dict[str, Any]:
    """The ``idp.provision`` body for one directory entry."""
    request: dict[str, Any] = {
        "idp_id": idp_id,
        "subject": entry.subject,
        "username": entry.username,
        "active": entry.active,
        "claims": entry.claims(),
    }
    if entry.email:
        request["email"] = entry.email
    if entry.display_name:
        request["display_name"] = entry.display_name
    return request


def _deprovision_request(idp_id: str, row: dict[str, Any]) -> dict[str, Any]:
    user = row["user"]
    return {
        "idp_id": idp_id,
        "subject": str(row["subject"]),
        "username": str(user["username"]),
        "active": False,
        "claims": {},
    }


class LdapSync:
    """Run the sync once per IdP (:meth:`sync`) or forever (:meth:`run_forever`)."""

    def __init__(
        self,
        *,
        port: IdentityPort,
        directory: IdpDirectory,
        directories: DirectoryFactory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._port = port
        self._directory = directory
        self._directories = directories
        self._clock = clock
        self._last_run: dict[str, float] = {}

    def _read_directory(self, record: IdpRecord) -> list[DirectoryEntry]:
        return list(self._directories(record).users())

    async def _provisioned(self, idp_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            query: dict[str, Any] = {"idp_id": idp_id, "limit": _PAGE}
            if after:
                query["after"] = after
            op = identity_op("idp", "list_provisioned", query)
            page = await call_expect(self._port, op, "provisioned")
            rows.extend(page)
            if len(page) < _PAGE:
                return rows
            after = str(page[-1]["user"]["principal_id"])

    async def _provision(self, report: SyncReport, request: dict[str, Any]) -> None:
        try:
            await self._port.call(identity_op("idp", "provision", request))
        except IdentityRefused as refusal:
            report.refused[request["subject"]] = refusal.code
            return
        if request["active"]:
            report.provisioned += 1
        else:
            report.deprovisioned += 1

    async def _deprovision_missing(
        self, report: SyncReport, seen: set[str], ratio: float
    ) -> None:
        rows = await self._provisioned(report.idp_id)
        live = [r for r in rows if r["user"].get("status") != "deprovisioned"]
        missing = [r for r in live if str(r["subject"]) not in seen]
        if live and len(missing) > ratio * len(live):
            report.aborted = (
                f"refused to deprovision {len(missing)} of {len(live)} principals "
                "(above max_deprovision_ratio)"
            )
            return
        for row in missing:
            await self._provision(report, _deprovision_request(report.idp_id, row))

    async def sync(self, record: IdpRecord) -> SyncReport:
        """Mirror one LDAP IdP into the engine store."""
        report = SyncReport(record.idp_id)
        try:
            settings = ldap_settings(record)
            entries = await anyio.to_thread.run_sync(self._read_directory, record)
        except (UnknownIdp, DirectoryUnavailable) as exc:
            report.aborted = f"directory unavailable ({type(exc).__name__})"
            return report
        for entry in entries:
            await self._provision(report, provision_request(record.idp_id, entry))
        seen = {entry.subject for entry in entries}
        await self._deprovision_missing(report, seen, settings.max_deprovision_ratio)
        return report

    def _due(self, record: IdpRecord) -> bool:
        try:
            interval = ldap_settings(record).sync_interval_s
        except UnknownIdp:
            return False
        last = self._last_run.get(record.idp_id)
        return last is None or self._clock() - last >= interval

    async def sync_due(self, records: Iterable[IdpRecord]) -> list[SyncReport]:
        """Sync every enabled LDAP IdP whose interval has elapsed."""
        reports = []
        for record in records:
            if record.kind != "ldap" or not record.enabled or not self._due(record):
                continue
            self._last_run[record.idp_id] = self._clock()
            report = await self.sync(record)
            _LOG.info("ldap sync %s: %s", record.idp_id, report)
            reports.append(report)
        return reports

    async def run_forever(self) -> None:
        """The scheduler: check every IdP's interval every 30 seconds."""
        while True:
            try:
                await self.sync_due(await self._directory.records())
            except IdentityRefused as refusal:
                _LOG.warning("ldap sync could not read the identity store: %s", refusal.code)
            await anyio.sleep(_TICK_S)
