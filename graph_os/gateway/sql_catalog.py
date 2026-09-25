"""Read-only SQL catalog introspection over the epistemic-graph SQL surface.

The WebUI catalog browser needs the ``catalogs -> tables/views -> columns``
projection of the engine's synthesized ``information_schema``. This module
carries no caller-supplied SQL by construction:

* every statement issued is a module-level constant in :data:`CATALOG_STATEMENTS`;
  there is no string interpolation anywhere on this path;
* the only caller control, an optional schema-name filter, is validated as a SQL
  identifier and applied in Python over rows already returned, so it never
  reaches the engine;
* only the engine's read-only ``information_schema`` relations are read.

Statements run through the generated epistemic-graph ``QueryClient.sql`` of a
session-routed client for the caller's own tenant graph, under the engine's
row-level isolation. Engine fidelity is reported in the response's
``capabilities`` block rather than fabricated: primary keys are ``None``
("unknown") while ``information_schema.key_column_usage`` is empty, and
``nullable`` is passed through with ``capabilities.nullability = False``.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any

#: The fixed, server-authored catalog statements. Constants on purpose: the only
#: SQL this module can ever issue is one of these three strings, verbatim.
CATALOG_STATEMENTS: Any = MappingProxyType(
    {
        "tables": (
            "SELECT table_catalog, table_schema, table_name, table_type "
            "FROM information_schema.tables"
        ),
        "columns": (
            "SELECT table_catalog, table_schema, table_name, column_name, "
            "ordinal_position, is_nullable, data_type, udt_name "
            "FROM information_schema.columns"
        ),
        "primary_keys": (
            "SELECT table_catalog, table_schema, table_name, column_name "
            "FROM information_schema.key_column_usage"
        ),
    }
)

_TABLE_KINDS = MappingProxyType({"BASE TABLE": "table", "VIEW": "view"})
_SCHEMA_FILTER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


class SqlSchemaUnavailable(RuntimeError):
    """Typed, fail-closed catalog-introspection error.

    Never raised to report "healthy, nothing there": an empty projection is
    indistinguishable at the call site from a broken engine read, which is the
    exact failure mode AGENTS.md's "Fail closed" section exists to stop.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "catalog_unavailable",
        status_code: int = 503,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def as_payload(self) -> dict[str, Any]:
        """The wire error envelope (no engine internals, no caller echo)."""
        return {"status": "error", "code": self.code, "message": str(self)}


def validate_schema_filter(value: Any) -> str | None:
    """Return a validated schema name, or ``None`` for "every schema".

    The result is used as a **Python** comparison key, never as SQL text; the
    validation is defence in depth so a hostile value is rejected at the edge
    rather than travelling further into the projection.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise SqlSchemaUnavailable(
            "schema filter must be a string",
            code="invalid_schema_filter",
            status_code=422,
        )
    text = value.strip()
    if not text:
        return None
    if _SCHEMA_FILTER_RE.fullmatch(text) is None:
        raise SqlSchemaUnavailable(
            "schema filter is not a valid SQL identifier",
            code="invalid_schema_filter",
            status_code=422,
        )
    return text


def _decode_rows(payload: Any) -> list[dict[str, Any]] | None:
    """Rows from one engine SQL result, or ``None`` when the read failed.

    Distinguishing a failed read from an empty one is what lets the caller
    fail closed instead of reporting "no tables".
    """
    if not isinstance(payload, list):
        return None
    return [row for row in payload if isinstance(row, dict)]


async def _read_catalog(client: Any, key: str) -> list[dict[str, Any]] | None:
    """Run ONE constant catalog statement through the generated EG SQL client."""
    try:
        rows = await client.query.sql(CATALOG_STATEMENTS[key])
    except Exception as exc:
        raise SqlSchemaUnavailable(
            f"engine SQL catalog read failed ({type(exc).__name__})"
        ) from exc
    return _decode_rows(rows)


def _relation_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("table_catalog") or ""),
        str(row.get("table_schema") or ""),
        str(row.get("table_name") or ""),
    )


def _group_by_relation(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_relation_key(row), []).append(row)
    return grouped


def _primary_key_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], set[str]]:
    index: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        name = str(row.get("column_name") or "")
        if name:
            index.setdefault(_relation_key(row), set()).add(name)
    return index


def _column_entry(row: dict[str, Any], pk_names: set[str] | None) -> dict[str, Any]:
    name = str(row.get("column_name") or "")
    try:
        position = int(row.get("ordinal_position") or 0)
    except (TypeError, ValueError):
        position = 0
    return {
        "name": name,
        "position": position,
        "data_type": str(row.get("data_type") or ""),
        "udt_name": str(row.get("udt_name") or "") or None,
        "nullable": str(row.get("is_nullable") or "").strip().upper() == "YES",
        "primary_key": (name in pk_names) if pk_names is not None else None,
    }


def _relation_entry(
    row: dict[str, Any],
    columns_by_relation: dict[tuple[str, str, str], list[dict[str, Any]]],
    pk_index: dict[tuple[str, str, str], set[str]],
    *,
    primary_keys_supported: bool,
) -> dict[str, Any]:
    key = _relation_key(row)
    pk_names = pk_index.get(key, set()) if primary_keys_supported else None
    columns = [
        _column_entry(column_row, pk_names)
        for column_row in columns_by_relation.get(key, [])
    ]
    columns.sort(key=lambda entry: (entry["position"], entry["name"]))
    raw_type = str(row.get("table_type") or "").strip().upper()
    return {
        "catalog": key[0],
        "schema": key[1],
        "name": key[2],
        "kind": _TABLE_KINDS.get(raw_type, "table"),
        "table_type": raw_type or "BASE TABLE",
        "columns": columns,
    }


def _nest_by_catalog(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``[relation]`` → ``[{catalog, schemas: [{schema, tables: [...]}]}]``."""
    catalogs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for relation in relations:
        schemas = catalogs.setdefault(relation["catalog"], {})
        schemas.setdefault(relation["schema"], []).append(relation)
    return [
        {
            "catalog": catalog,
            "schemas": [
                {"schema": schema, "tables": sorted(tables, key=_relation_sort_key)}
                for schema, tables in sorted(schemas.items())
            ],
        }
        for catalog, schemas in sorted(catalogs.items())
    ]


def _relation_sort_key(relation: dict[str, Any]) -> tuple[str, str]:
    return (relation["kind"], relation["name"])


def _counts(catalogs: list[dict[str, Any]]) -> dict[str, int]:
    schemas = [schema for catalog in catalogs for schema in catalog["schemas"]]
    tables = [table for schema in schemas for table in schema["tables"]]
    return {
        "catalogs": len(catalogs),
        "schemas": len(schemas),
        "tables": len(tables),
        "columns": sum(len(table["columns"]) for table in tables),
    }


def build_projection(
    table_rows: list[dict[str, Any]],
    column_rows: list[dict[str, Any]],
    pk_rows: list[dict[str, Any]],
    *,
    schema_filter: str | None = None,
) -> dict[str, Any]:
    """Shape three ``information_schema`` reads into the browser projection."""
    columns_by_relation = _group_by_relation(column_rows)
    pk_index = _primary_key_index(pk_rows)
    primary_keys_supported = bool(pk_index)
    relations = [
        _relation_entry(
            row,
            columns_by_relation,
            pk_index,
            primary_keys_supported=primary_keys_supported,
        )
        for row in table_rows
        if schema_filter is None or str(row.get("table_schema") or "") == schema_filter
    ]
    catalogs = _nest_by_catalog(relations)
    return {
        "status": "success",
        "catalogs": catalogs,
        "capabilities": {
            "primary_keys": primary_keys_supported,
            "nullability": False,
        },
        "counts": _counts(catalogs),
    }


async def sql_schema(client: Any, *, schema: str | None = None) -> dict[str, Any]:
    """The ``catalogs → tables/views → columns`` projection. No caller SQL.

    Args:
        client: Session-routed epistemic-graph client for the caller's graph.
        schema: Optional schema-name filter, validated as a SQL identifier and
            matched in Python. ``None`` returns every readable schema.

    Raises:
        SqlSchemaUnavailable: the filter was malformed, the engine catalog was
            unreadable, or it produced nothing — the engine always synthesizes
            at least ``nodes``/``edges``, so an empty relation list means a
            failed read, not an empty database.
    """
    schema_filter = validate_schema_filter(schema)

    table_rows = await _read_catalog(client, "tables")
    if not table_rows:
        raise SqlSchemaUnavailable(
            "engine SQL catalog returned no relations; the synthesized "
            "information_schema is unreadable"
        )
    column_rows = await _read_catalog(client, "columns")
    if column_rows is None:
        raise SqlSchemaUnavailable("engine column catalog is unreadable")
    pk_rows = await _read_catalog(client, "primary_keys")

    projection = build_projection(
        table_rows, column_rows, pk_rows or [], schema_filter=schema_filter
    )
    if schema_filter is not None and not projection["catalogs"]:
        raise SqlSchemaUnavailable(
            "no readable schema matches that name",
            code="schema_not_found",
            status_code=404,
        )
    return projection


__all__ = [
    "CATALOG_STATEMENTS",
    "SqlSchemaUnavailable",
    "build_projection",
    "sql_schema",
    "validate_schema_filter",
]
