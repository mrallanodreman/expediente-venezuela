#!/usr/bin/env python3
"""
Expediente Venezuela — Denuncia Management MCP Server.

AI agent tool for scraping, categorizing, dossiering, and publishing
Venezuelan denuncias from X/Twitter.

Transport: stdio (JSON-RPC)
Tools: 10 tools for full denuncia lifecycle
Resources: 3 resources for frontend data access
"""
import json
import asyncio
from typing import Optional, List, Dict, Any, Annotated
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ConfigDict, field_validator
from mcp.server.fastmcp import FastMCP

try:
    from .denuncias_db import (
        init_db, get_denuncia, list_denuncias, insert_denuncia,
        update_denuncia, publish_denuncia, unpublish_denuncia,
        get_stats, export_to_json, get_next_expediente_id,
        DB_PATH, EXPORT_PATH, CATEGORY_LABELS
    )
    from .denuncias_scraper import search_denuncias as _scraper_search, backfill_media as _backfill_media
except ImportError:
    from denuncias_db import (
        init_db, get_denuncia, list_denuncias, insert_denuncia,
        update_denuncia, publish_denuncia, unpublish_denuncia,
        get_stats, export_to_json, get_next_expediente_id,
        DB_PATH, EXPORT_PATH, CATEGORY_LABELS
    )
    from denuncias_scraper import search_denuncias as _scraper_search, backfill_media as _backfill_media

mcp = FastMCP("expediente_venezuela_mcp")

_db_conn = None


def get_conn():
    global _db_conn
    if _db_conn is None:
        _db_conn = init_db()
    return _db_conn


# --- Tool Input Models ---

class SearchDenunciasInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    queries: List[str] = Field(..., description="Search queries for X/Twitter", min_length=1, max_length=10)
    max_results: int = Field(default=20, description="Max results per query", ge=1, le=100)


class CategorizeDenunciaInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    denuncia_id: str = Field(..., description="Expediente ID (EV-2026-NNNN) or row ID", min_length=1)
    category: str = Field(..., description="Category: corrupcion, abuso, extorsion, desalojo, represion, servicios, salud, censura, persecucion, general")
    severity: str = Field(default="info", description="Severity: critical, high, medium, info")
    tags: Optional[List[str]] = Field(default=None, description="Optional tags")

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        valid = list(CATEGORY_LABELS.keys())
        if v not in valid:
            raise ValueError(f"Invalid category. Must be one of: {', '.join(valid)}")
        return v

    @field_validator('severity')
    @classmethod
    def validate_severity(cls, v):
        valid = ['critical', 'high', 'medium', 'info']
        if v not in valid:
            raise ValueError(f"Invalid severity. Must be one of: {', '.join(valid)}")
        return v


class GenerateDossierInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)
    resumen: str = Field(..., description="Executive summary (2-4 sentences)", min_length=10)
    contexto: str = Field(default="", description="Background context")
    fuentes: Optional[List[str]] = Field(default=None, description="Source URLs or references")
    evidencias: Optional[List[str]] = Field(default=None, description="Evidence descriptions or URLs")
    conclusion: str = Field(default="", description="Conclusions or findings")


class ListDenunciasInput(BaseModel):
    category: Optional[str] = Field(default=None, description="Filter by category")
    status: Optional[str] = Field(default=None, description="Filter by status: draft or published")
    severity: Optional[str] = Field(default=None, description="Filter by severity")
    limit: int = Field(default=20, description="Max results", ge=1, le=200)
    offset: int = Field(default=0, description="Pagination offset", ge=0)


class GetDenunciaInput(BaseModel):
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)


class UpdateDenunciaInput(BaseModel):
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)
    fields: Dict[str, Any] = Field(..., description="Fields to update")


class PublishDenunciaInput(BaseModel):
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)


class UnpublishDenunciaInput(BaseModel):
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)


class RunCycleInput(BaseModel):
    queries: Optional[List[str]] = Field(default=None, description="Custom queries. None = default EXPEDIENTE_QUERIES.")
    max_results: int = Field(default=20, description="Max results per query", ge=1, le=100)


# ============================================================
# TOOL 1: search_denuncias
# ============================================================
@mcp.tool(
    name="search_denuncias",
    annotations={"title": "Search X/Twitter for Denuncias", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def search_denuncias(params: SearchDenunciasInput) -> str:
    """Search X/Twitter for denuncias using Playwright + Ferdium cookies."""
    try:
        result = await asyncio.to_thread(_scraper_search, queries=params.queries, max_results=params.max_results)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "new_denuncias": []})


# ============================================================
# TOOL 2: categorize_denuncia
# ============================================================
@mcp.tool(
    name="categorize_denuncia",
    annotations={"title": "Categorize a Denuncia", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def categorize_denuncia(params: CategorizeDenunciaInput) -> str:
    """Categorize a denuncia with category, severity, and tags. The AI agent decides the classification."""
    try:
        conn = get_conn()
        fields = {"category": params.category, "severity": params.severity}
        if params.tags is not None:
            fields["tags"] = json.dumps(params.tags)
        updated = update_denuncia(conn, params.denuncia_id, fields)
        if not updated:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})
        denuncia = get_denuncia(conn, params.denuncia_id)
        return json.dumps(denuncia, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 3: generate_dossier
# ============================================================
@mcp.tool(
    name="generate_dossier",
    annotations={"title": "Generate Dossier for a Denuncia", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def generate_dossier(params: GenerateDossierInput) -> str:
    """Generate full dossier content for a denuncia. The AI agent writes resumen, contexto, fuentes, evidencias, conclusion."""
    try:
        conn = get_conn()
        fields = {"resumen": params.resumen, "contexto": params.contexto, "conclusion": params.conclusion}
        if params.fuentes is not None:
            fields["fuentes"] = json.dumps(params.fuentes)
        if params.evidencias is not None:
            fields["evidencias"] = json.dumps(params.evidencias)
        updated = update_denuncia(conn, params.denuncia_id, fields)
        if not updated:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})
        denuncia = get_denuncia(conn, params.denuncia_id)
        return json.dumps(denuncia, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 4: list_denuncias
# ============================================================
@mcp.tool(
    name="list_denuncias",
    annotations={"title": "List Denuncias with Filters", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def list_denuncias_tool(params: ListDenunciasInput) -> str:
    """List denuncias with optional filters for category, status, severity. Returns paginated results."""
    try:
        conn = get_conn()
        results = list_denuncias(conn, category=params.category, status=params.status, severity=params.severity, limit=params.limit, offset=params.offset)
        return json.dumps({"denuncias": results, "count": len(results), "limit": params.limit, "offset": params.offset}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 5: get_denuncia
# ============================================================
@mcp.tool(
    name="get_denuncia",
    annotations={"title": "Get Single Denuncia", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def get_denuncia_tool(params: GetDenunciaInput) -> str:
    """Get full details of a single denuncia by expediente_id or row id."""
    try:
        conn = get_conn()
        denuncia = get_denuncia(conn, params.denuncia_id)
        if not denuncia:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})
        return json.dumps(denuncia, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 6: update_denuncia
# ============================================================
@mcp.tool(
    name="update_denuncia",
    annotations={"title": "Update Denuncia Fields", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def update_denuncia_tool(params: UpdateDenunciaInput) -> str:
    """Update any field of a denuncia (category, severity, dossier fields, tags, etc.)."""
    try:
        conn = get_conn()
        serialized = {}
        for k, v in params.fields.items():
            if k in ('fuentes', 'evidencias', 'tags') and isinstance(v, list):
                serialized[k] = json.dumps(v)
            else:
                serialized[k] = v
        updated = update_denuncia(conn, params.denuncia_id, serialized)
        if not updated:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})
        denuncia = get_denuncia(conn, params.denuncia_id)
        return json.dumps(denuncia, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 7: publish_denuncia
# ============================================================
@mcp.tool(
    name="publish_denuncia",
    annotations={"title": "Publish a Denuncia", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def publish_denuncia_tool(params: PublishDenunciaInput) -> str:
    """Mark a denuncia as published and export to JSON for the frontend."""
    try:
        conn = get_conn()
        result = publish_denuncia(conn, params.denuncia_id)
        if not result:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})
        result["export_path"] = str(EXPORT_PATH)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 8: unpublish_denuncia
# ============================================================
@mcp.tool(
    name="unpublish_denuncia",
    annotations={"title": "Unpublish a Denuncia", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def unpublish_denuncia_tool(params: UnpublishDenunciaInput) -> str:
    """Remove a denuncia from public view (sets status back to draft)."""
    try:
        conn = get_conn()
        success = unpublish_denuncia(conn, params.denuncia_id)
        if not success:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})
        denuncia = get_denuncia(conn, params.denuncia_id)
        return json.dumps({"success": True, "denuncia": denuncia}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 9: stats
# ============================================================
@mcp.tool(
    name="stats",
    annotations={"title": "Get Database Statistics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def stats_tool() -> str:
    """Get database statistics: total, by_category, by_severity, published_count, draft_count."""
    try:
        conn = get_conn()
        result = get_stats(conn)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 10: run_cycle
# ============================================================
EXPEDIENTE_QUERIES = [
    "denuncia venezuela",
    "corrupcion venezuela",
    "abuso poder denuncia",
    "extorsion venezuela",
    "desalojo ilegal venezuela",
    "represion protesta venezuela",
    "maltrato policia venezuela",
    "injusticia venezuela",
]


@mcp.tool(
    name="run_cycle",
    annotations={"title": "Run Full Automated Scraping Cycle", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def run_cycle(params: RunCycleInput) -> str:
    """Run a full scrape cycle: search X/Twitter → store in SQLite → return new denuncias for categorization."""
    try:
        queries = params.queries or EXPEDIENTE_QUERIES
        search_result = await asyncio.to_thread(_scraper_search, queries=queries, max_results=params.max_results)
        conn = get_conn()
        stats = get_stats(conn)
        return json.dumps({
            "new_denuncias": search_result.get("new_denuncias", []),
            "total_new": search_result.get("skipped", 0),
            "skipped": search_result.get("skipped", 0),
            "queries_used": search_result.get("queries_used", []),
            "stats": stats,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "new_denuncias": []})


@mcp.tool(
    name="backfill_media",
    annotations={"title": "Backfill Missing Media", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def backfill_media_tool(max_denuncias: int = 30) -> str:
    """Visit tweet URLs for denuncias with no media and extract images/videos.

    Use this when many denuncias are missing images or videos.
    Visits each tweet URL directly to grab media that the search scraper missed.
    """
    try:
        result = await asyncio.to_thread(_backfill_media, max_denuncias=max_denuncias)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# RESOURCES
# ============================================================

@mcp.resource("denuncias://published")
async def get_published_denuncias() -> str:
    """All published denuncias as JSON (for frontend slider)."""
    try:
        conn = get_conn()
        result = export_to_json(conn)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("denuncias://stats")
async def get_denuncias_stats() -> str:
    """Database statistics summary."""
    try:
        conn = get_conn()
        result = get_stats(conn)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("denuncias://categories")
async def get_denuncias_categories() -> str:
    """Categories with counts."""
    try:
        conn = get_conn()
        stats = get_stats(conn)
        return json.dumps({"categories": stats.get("by_category", {}), "labels": CATEGORY_LABELS}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    init_db()
    mcp.run()
