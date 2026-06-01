"""
backend.openapi_export — Export OpenAPI schema including all WS event models.

Standard FastAPI /openapi.json only reflects HTTP routes and EXCLUDES WebSocket
payload types (RESEARCH.md Pitfall 3). This module builds a minimal FastAPI app
with a single typed dummy GET endpoint that returns the Union of all WS event models,
forcing FastAPI to register them all in components.schemas.

Usage:
    python -m backend.openapi_export             # writes openapi.json to stdout
    python -m backend.openapi_export --out PATH  # writes openapi.json to file

The gen-types.sh script pipes this output to openapi-typescript to generate
frontend/types/api.ts (D-27).
"""

from __future__ import annotations

import json
import sys

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from backend.ws.models import (
    ArbOpp,
    CurrentState,
    Envelope,
    JournalEvent,
    ModelStatus,
    NavTick,
    SessionEvent,
    TradeEvent,
)

# Union of all WS event models — used as response_model to surface them in components.schemas.
# Defined as a module-level alias so ruff UP007 does not fire on the call-site value.
_WsEventUnion = (
    NavTick
    | TradeEvent
    | JournalEvent
    | ModelStatus
    | ArbOpp
    | SessionEvent
    | CurrentState
    | Envelope
)


def build_openapi_app() -> FastAPI:
    """
    Build a minimal FastAPI app that surfaces all WS event models into OpenAPI components.

    Pattern 4 (from RESEARCH.md): register a dummy GET endpoint typed to return
    Union[<all WS models>] so FastAPI includes them in components.schemas.
    Without this, WS-only types are invisible to openapi-typescript (Pitfall 3).
    """
    app = FastAPI(
        title="trAIder Backend API",
        description=(
            "trAIder backend — WebSocket event types + REST API.\n\n"
            "This OpenAPI schema is the source of truth for frontend TypeScript types (D-27).\n"
            "Generated types live at frontend/types/api.ts — do not edit manually."
        ),
        version="0.1.0",
    )

    # Dummy endpoint: typed to return the Union of ALL WS event models.
    # FastAPI inspects return type annotations and registers all Union members
    # in components.schemas. This is the ONLY purpose of this endpoint.
    # It is never called at runtime; it exists solely for the OpenAPI schema export.
    @app.get(
        "/_ws_types",
        response_model=_WsEventUnion,
        include_in_schema=True,
        summary="[CODEGEN ONLY] WS event type registry",
        description=(
            "This endpoint exists ONLY to surface WebSocket payload types into OpenAPI "
            "components.schemas for openapi-typescript codegen (D-27, Pitfall 3). "
            "It is never called at runtime."
        ),
        tags=["codegen"],
    )
    async def _ws_type_registry() -> None:  # pragma: no cover
        """Never called. Return type drives component registration."""
        raise NotImplementedError("This endpoint is for codegen only.")

    return app


def export_openapi(out_path: str | None = None) -> dict:
    """
    Generate the OpenAPI schema dict and optionally write it to a file or stdout.

    Args:
        out_path: File path to write JSON to. If None, writes to stdout.

    Returns:
        The OpenAPI schema dict.
    """
    app = build_openapi_app()
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    json_output = json.dumps(schema, indent=2)

    if out_path is None:
        sys.stdout.write(json_output)
        sys.stdout.write("\n")
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json_output)
            f.write("\n")

    return schema


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export OpenAPI JSON for trAIder backend")
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Output file path. Defaults to stdout.",
    )
    args = parser.parse_args()
    export_openapi(out_path=args.out)
