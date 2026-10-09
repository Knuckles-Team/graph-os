"""Ingestion services for the GraphOS operation registry.

GRAPHOS-OPS-R020.1: GraphOS calls the ingestion SDK only through the typed
:class:`~graph_os.ingest.service.IngestRunner` port defined here. No handler
imports a private ingestion implementation directly.
"""

from __future__ import annotations

from .service import (
    IngestIndexReceipt,
    IngestRunner,
    IngestSourceInventory,
    IngestSourceRecord,
    IngestSyncReceipt,
    get_source_status,
    index_repository,
    list_sources,
    sync_source,
)

__all__ = [
    "IngestIndexReceipt",
    "IngestRunner",
    "IngestSourceInventory",
    "IngestSourceRecord",
    "IngestSyncReceipt",
    "get_source_status",
    "index_repository",
    "list_sources",
    "sync_source",
]
