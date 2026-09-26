"""Project the two native A2A protocol routes onto a FastAPI application."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def mount_a2a_routes(app: FastAPI, *, card_handler: Any, rpc_handler: Any) -> None:
    """Mount the Agent Card and JSON-RPC handlers without changing their authority."""
    app.add_api_route("/.well-known/agent-card.json", card_handler, methods=["GET"])
    app.add_api_route("/a2a", rpc_handler, methods=["POST"])
