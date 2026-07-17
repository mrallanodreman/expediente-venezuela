# MCP Server Implementation Plan: Expediente Venezuela Denuncia Agent

**Date:** 2026-07-16
**Scope:** Full MCP server (FastMCP/Python) + SQLite DB + refactored scraper + JSON export
**Transport:** JSON-RPC over stdio (local agent use)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  AI Agent (OpenCode / Claude / any MCP client)      │
│  Calls: search_denuncias, categorize_denuncia, ...  │
└────────────────────┬────────────────────────────────┘
                     │ stdio (JSON-RPC)
┌────────────────────▼────────────────────────────────┐
│  mcp_server.py  (FastMCP)                           │
│  10 tools + 3 resources                              │
├─────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ denuncias_db  │  │denuncias_     │                │
│  │ .py (SQLite) │  │scraper.py     │                │
│  │ CRUD+export  │  │(Playwright)   │                │
│  └──────────────┘  └──────────────┘                 │
├─────────────────────────────────────────────────────┤
│  export_json.py  (videos-first JSON for frontend)   │
└─────────────────────────────────────────────────────┘
```

---

## Execution Order

```
Plan 1: denuncias_db.py          (SQLite layer)        Wave 1
Plan 2: denuncias_scraper.py     (refactor → module)   Wave 1 (parallel)
Plan 3: export_json.py           (JSON export)         Wave 2 (depends on 1)
Plan 4: mcp_server.py            (MCP server)          Wave 3 (depends on 1+2+3)
Plan 5: requirements.txt         (dependencies)        Wave 1 (parallel)
Plan 6: index.html modifications (dynamic frontend)    Wave 4 (depends on 3)
```

---

## Plan 1: `scraper/denuncias_db.py` — SQLite Database Layer

**File:** `scraper/denuncias_db.py` (rewrite from scratch)
**Wave:** 1
**Dependencies:** None

### 1.1 Schema

```sql
CREATE TABLE IF NOT EXISTS denuncias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id TEXT UNIQUE NOT NULL,      -- EV-YYYY-NNNN
    tweet_id TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    text TEXT,
    category TEXT DEFAULT 'general',
    severity TEXT DEFAULT 'info',            -- critical/high/medium/info
    status TEXT DEFAULT 'draft',             -- draft/published
    video_url TEXT,
    images TEXT,                             -- JSON array
    retweets INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    created_at TEXT,                         -- tweet timestamp
    scraped_at TEXT NOT NULL,                -- when scraped
    published_at TEXT,                       -- when published
    source_url TEXT NOT NULL,                -- original X URL
    query_used TEXT,                         -- which query found it
    tags TEXT,                               -- JSON array of tags
    -- Dossier fields (agent-populated)
    resumen TEXT,
    contexto TEXT,
    fuentes TEXT,                            -- JSON array
    evidencias TEXT,                         -- JSON array
    conclusion TEXT
);

CREATE INDEX IF NOT EXISTS idx_denuncias_category ON denuncias(category);
CREATE INDEX IF NOT EXISTS idx_denuncias_status ON denuncias(status);
CREATE INDEX IF NOT EXISTS idx_denuncias_severity ON denuncias(severity);
CREATE INDEX IF NOT EXISTS idx_denuncias_tweet_id ON denuncias(tweet_id);
CREATE INDEX IF NOT EXISTS idx_denuncias_expediente ON denuncias(expediente_id);
```

### 1.2 Function Signatures

```python
"""SQLite database layer for Expediente Venezuela denuncias."""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "denuncias.db"
EXPORT_PATH = DB_DIR / "denuncias.json"

CATEGORY_LABELS = {
    "corrupcion": "Corrupción",
    "abuso": "Abuso",
    "extorsion": "Extorsión",
    "desalojo": "Desalojo",
    "represion": "Represión",
    "servicios": "Servicios",
    "salud": "Salud",
    "censura": "Censura",
    "persecucion": "Persecución",
    "general": "General",
}


def init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create/open SQLite DB, apply schema, return connection.
    
    Args:
        db_path: Optional custom path. Defaults to DB_PATH.
    
    Returns:
        sqlite3.Connection with row_factory set to Row.
    """
    ...


def get_next_expediente_id(conn: sqlite3.Connection) -> str:
    """Generate next sequential expediente_id: EV-YYYY-NNNN.
    
    Queries the highest existing number for the current year,
    increments by 1. Returns 'EV-2026-0001' on fresh DB.
    
    Returns:
        str: Next expediente ID (e.g., 'EV-2026-0042')
    """
    ...


def insert_denuncia(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a denuncia row. Deduplicates by tweet_id (INSERT OR IGNORE).
    
    Auto-generates expediente_id if not provided.
    Auto-sets scraped_at to now if not provided.
    
    Args:
        data: Dict with keys matching schema columns. Required: tweet_id, username,
              display_name, source_url. Optional: expediente_id, category, etc.
    
    Returns:
        Dict with 'expediente_id', 'id' (row id), and 'is_new' (bool).
        If tweet_id already exists, returns existing row info with is_new=False.
    """
    ...


def get_denuncia(conn: sqlite3.Connection, denuncia_id: str) -> Optional[Dict[str, Any]]:
    """Get a single denuncia by expediente_id or row id.
    
    Args:
        denuncia_id: Either 'EV-2026-NNNN' expediente_id or integer row id.
    
    Returns:
        Dict with all columns, or None if not found.
    """
    ...


def list_denuncias(
    conn: sqlite3.Connection,
    category: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List denuncias with optional filters.
    
    Args:
        category: Filter by category (e.g., 'corrupcion').
        status: Filter by status ('draft' or 'published').
        severity: Filter by severity ('critical'/'high'/'medium'/'info').
        limit: Max results (default 50, max 200).
        offset: Pagination offset.
    
    Returns:
        List of dicts, ordered by scraped_at DESC.
    """
    ...


def update_denuncia(
    conn: sqlite3.Connection,
    denuncia_id: str,
    fields: Dict[str, Any],
) -> bool:
    """Update specific fields of a denuncia.
    
    Args:
        denuncia_id: expediente_id (e.g., 'EV-2026-0001') or integer row id.
        fields: Dict of {column_name: new_value}. Only valid columns accepted.
    
    Returns:
        True if updated, False if not found.
    """
    ...


def publish_denuncia(conn: sqlite3.Connection, denuncia_id: str) -> Optional[Dict[str, Any]]:
    """Set status='published', published_at=now, export JSON, return published record.
    
    Args:
        denuncia_id: expediente_id or row id.
    
    Returns:
        Published denuncia dict, or None if not found.
    """
    ...


def unpublish_denuncia(conn: sqlite3.Connection, denuncia_id: str) -> bool:
    """Set status='draft', published_at=None.
    
    Args:
        denuncia_id: expediente_id or row id.
    
    Returns:
        True if updated, False if not found.
    """
    ...


def get_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Get database statistics.
    
    Returns:
        {
            "total": int,
            "by_category": {"corrupcion": 8, ...},
            "by_severity": {"high": 5, ...},
            "published_count": int,
            "draft_count": int
        }
    """
    ...


def export_to_json(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Export published denuncias to JSON with videos-first ordering.
    
    Writes to EXPORT_PATH (scraper/data/denuncias.json).
    Returns the exported data dict.
    """
    ...


def migrate_from_json(conn: sqlite3.Connection, json_path: Path) -> int:
    """Import existing denuncias.json into SQLite. Returns count imported."""
    ...


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert sqlite3.Row to plain dict. Parses JSON fields (images, tags, fuentes, evidencias)."""
    ...


def _parse_json_field(value: Optional[str]) -> Any:
    """Safely parse a JSON string field. Returns None on failure."""
    ...


def _serialize_json_field(value: Any) -> Optional[str]:
    """Serialize a Python object to JSON string for storage."""
    ...
```

### 1.3 Key Implementation Details

**dedup logic in `insert_denuncia`:**
```python
# INSERT OR IGNORE — if tweet_id exists, skip silently
# Return is_new=False with existing expediente_id
```

**`publish_denuncia` triggers JSON export:**
```python
def publish_denuncia(conn, denuncia_id):
    # 1. UPDATE status='published', published_at=now
    # 2. Call export_to_json(conn)
    # 3. Return the published record
```

**`export_to_json` ordering:**
```python
# 1. SELECT all published denuncias ORDER BY scraped_at DESC
# 2. Split: with_video (video_url IS NOT NULL) vs no_video
# 3. Within each group: sorted by scraped_at DESC (already from query)
# 4. Concatenate: with_video + no_video
# 5. Count categories
# 6. Map each row to export format with category_label
# 7. Write to EXPORT_PATH
# 8. Return dict
```

**JSON export format:**
```json
{
  "updated_at": "2026-07-16T15:34:39Z",
  "count": 25,
  "categories": {"corrupcion": 8, "abuso": 5},
  "denuncias": [
    {
      "expediente_id": "EV-2026-0001",
      "username": "estebanoria",
      "name": "Esteban Oria",
      "text": "...",
      "category": "corrupcion",
      "category_label": "Corrupción",
      "severity": "high",
      "resumen": "...",
      "video_url": "...",
      "images": [],
      "likes": 123,
      "retweets": 45,
      "replies": 12,
      "created_at": "...",
      "url": "https://x.com/..."
    }
  ]
}
```

### 1.4 Verification

```bash
# Test 1: DB init
python3 -c "from denuncias_db import init_db; conn=init_db(); print('OK')"

# Test 2: Expediente ID generation
python3 -c "
from denuncias_db import init_db, get_next_expediente_id
conn = init_db()
print(get_next_expediente_id(conn))  # EV-2026-0001
"

# Test 3: Insert + dedup
python3 -c "
from denuncias_db import init_db, insert_denuncia, get_denuncia
conn = init_db()
r = insert_denuncia(conn, {'tweet_id': 'test1', 'username': 'test', 'display_name': 'Test', 'source_url': 'https://x.com/test/status/1'})
print(r)  # {'expediente_id': 'EV-2026-0001', 'id': 1, 'is_new': True}
r2 = insert_denuncia(conn, {'tweet_id': 'test1', 'username': 'test', 'display_name': 'Test', 'source_url': 'https://x.com/test/status/1'})
print(r2)  # {'is_new': False, ...}
"

# Test 4: Migration from existing JSON
python3 -c "
from denuncias_db import init_db, migrate_from_json
from pathlib import Path
conn = init_db()
count = migrate_from_json(conn, Path('data/denuncias.json'))
print(f'Migrated {count} denuncias')
"

# Test 5: Full CRUD cycle
python3 -c "
from denuncias_db import *
conn = init_db()
r = insert_denuncia(conn, {'tweet_id': 'test2', 'username': 'user', 'display_name': 'User', 'source_url': 'https://x.com/user/status/2'})
update_denuncia(conn, r['expediente_id'], {'category': 'corrupcion', 'severity': 'high'})
d = get_denuncia(conn, r['expediente_id'])
print(d['category'], d['severity'])  # corrupcion high
publish_denuncia(conn, r['expediente_id'])
stats = get_stats(conn)
print(stats)  # {'total': 2, 'published_count': 1, ...}
"
```

---

## Plan 2: `scraper/denuncias_scraper.py` — Refactor to Importable Module

**File:** `scraper/denuncias_scraper.py` (modify existing)
**Wave:** 1 (parallel with Plan 1)
**Dependencies:** None (but imports denuncias_db after Plan 1)

### 2.1 Changes

The current scraper saves to JSON file. Refactor to:
1. Keep all existing scraping logic (Playwright, cookies, extraction) intact
2. Replace `save_denuncias()` JSON writer with `denuncias_db.insert_denuncia()` calls
3. Extract `run_scrape()` to accept optional parameters (queries, max_results)
4. Make `run_scrape()` callable both from CLI and as imported function
5. Add `search_denuncias()` wrapper that returns structured results for MCP

### 2.2 New Function Signatures

```python
"""X/Twitter Denuncias Scraper — v4 (Playwright + SQLite).

Usage as CLI:  python3 denuncias_scraper.py
Usage as import: from denuncias_scraper import run_scrape, search_denuncias
"""

# Existing functions preserved (internal):
#   load_x_cookies(), load_seen_ids(), save_seen_ids()
#   extract_tweets_from_page(), scrape_search()

# MODIFIED functions:
async def run_scrape(
    queries: Optional[List[str]] = None,
    max_results: int = 60,
    scroll_rounds: int = 4,
) -> List[Dict[str, Any]]:
    """Main scrape cycle. Saves to SQLite via denuncias_db.
    
    Args:
        queries: Custom search queries. If None, uses default SEARCH_QUERIES.
        max_results: Max tweets to return (default 60).
        scroll_rounds: Scroll cycles per query (default 4).
    
    Returns:
        List of scraped tweet dicts (not DB records — raw tweet format).
    """


def search_denuncias(
    queries: List[str],
    max_results: int = 20,
) -> Dict[str, Any]:
    """Synchronous wrapper for MCP tool. Runs the async scrape and returns results.
    
    This is the primary entry point for the MCP server's search_denuncias tool.
    
    Args:
        queries: Search queries to run on X/Twitter.
        max_results: Max results per query.
    
    Returns:
        {
            "new_denuncias": [...],  # Newly scraped + stored
            "skipped": int,          # Already-seen tweets skipped
            "queries_used": [...],   # Which queries were run
        }
    """
    ...


def _save_to_db(all_tweets: List[Dict], conn: sqlite3.Connection) -> Dict[str, Any]:
    """Save scraped tweets to SQLite. Returns counts.
    
    Returns:
        {"inserted": int, "skipped": int, "expediente_ids": [...]}
    """
    ...


# CLI entry point preserved:
if __name__ == "__main__":
    ...
```

### 2.3 Key Changes

**Remove these functions:**
- `save_denuncias()` — replaced by `export_to_json()` in denuncias_db
- `_save_intermediate()` — replaced by `_save_to_db()`

**Modify `run_scrape()`:**
```python
async def run_scrape(queries=None, max_results=60, scroll_rounds=4):
    # ... existing Playwright logic ...
    
    # NEW: Use denuncias_db instead of JSON
    conn = init_db()
    result = _save_to_db(combined, conn)
    
    # Export JSON snapshot
    export_to_json(conn)
    conn.close()
    
    return result
```

**Add `search_denuncias()` sync wrapper:**
```python
def search_denuncias(queries, max_results=20):
    """Sync wrapper for MCP tool."""
    # Run async scrape in event loop
    new_tweets = asyncio.run(run_scrape(queries=queries, max_results=max_results))
    
    # Store in DB
    conn = init_db()
    result = _save_to_db(new_tweets, conn)
    export_to_json(conn)
    conn.close()
    
    return result
```

### 2.4 Verification

```bash
# Test 1: Import works
python3 -c "from denuncias_scraper import run_scrape, search_denuncias; print('OK')"

# Test 2: CLI still works
python3 denuncias_scraper.py

# Test 3: Function call works
python3 -c "
from denuncias_scraper import search_denuncias
result = search_denuncias(queries=['test query'], max_results=5)
print(result)
"
```

---

## Plan 3: `scraper/export_json.py` — Standalone JSON Export

**File:** `scraper/export_json.py` (new)
**Wave:** 2 (depends on Plan 1)
**Dependencies:** denuncias_db.py

### 3.1 Purpose

Standalone script that can be run independently to regenerate the JSON export.
Also used by the MCP server's publish/unpublish tools.

### 3.2 Function Signatures

```python
#!/usr/bin/env python3
"""Standalone JSON export for Expediente Venezuela.

Run: python3 export_json.py
Output: scraper/data/denuncias.json
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from denuncias_db import init_db, export_to_json, EXPORT_PATH


def run_export() -> dict:
    """Full export: init DB, export published denuncias to JSON.
    
    Returns:
        Exported data dict with updated_at, count, categories, denuncias.
    """
    conn = init_db()
    result = export_to_json(conn)
    conn.close()
    return result


if __name__ == "__main__":
    result = run_export()
    print(f"Exported {result['count']} denuncias → {EXPORT_PATH}")
    print(f"Categories: {result['categories']}")
```

### 3.3 Verification

```bash
python3 scraper/export_json.py
python3 -c "
import json
with open('scraper/data/denuncias.json') as f:
    data = json.load(f)
print(f'Count: {data[\"count\"]}')
print(f'Categories: {data[\"categories\"]}')
# Verify videos-first: if any have video_url, first item should have one
if data['denuncias'] and any(d.get('video_url') for d in data['denuncias']):
    assert data['denuncias'][0].get('video_url'), 'First item should have video'
    print('Videos-first ordering: OK')
"
```

---

## Plan 4: `scraper/mcp_server.py` — MCP Server (Core)

**File:** `scraper/mcp_server.py` (new)
**Wave:** 3 (depends on Plans 1+2+3)
**Dependencies:** denuncias_db, denuncias_scraper, export_json

### 4.1 Server Setup

```python
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

# Import local modules
from denuncias_db import (
    init_db, get_denuncia, list_denuncias, insert_denuncia,
    update_denuncia, publish_denuncia, unpublish_denuncia,
    get_stats, export_to_json, get_next_expediente_id,
    DB_PATH, CATEGORY_LABELS
)
from denuncias_scraper import search_denuncias as _scraper_search

# Initialize MCP server
mcp = FastMCP("expediente_venezuela_mcp")

# Database connection (lifespan-managed)
_db_conn = None


def get_conn():
    """Get or initialize database connection."""
    global _db_conn
    if _db_conn is None:
        _db_conn = init_db()
    return _db_conn
```

### 4.2 Pydantic Input Models

```python
# --- Tool Input Models ---

class SearchDenunciasInput(BaseModel):
    """Input for searching X/Twitter for denuncias."""
    model_config = ConfigDict(str_strip_whitespace=True)
    
    queries: List[str] = Field(
        ...,
        description="List of search queries to run on X/Twitter (e.g., ['corrupción Venezuela', 'abuso policial'])",
        min_length=1,
        max_length=10,
    )
    max_results: int = Field(
        default=20,
        description="Maximum results to return per query",
        ge=1,
        le=100,
    )


class CategorizeDenunciaInput(BaseModel):
    """Input for categorizing a single denuncia."""
    model_config = ConfigDict(str_strip_whitespace=True)
    
    denuncia_id: str = Field(
        ...,
        description="Expediente ID (e.g., 'EV-2026-0001') or row ID",
        min_length=1,
    )
    category: str = Field(
        ...,
        description="Category: corrupcion, abuso, extorsion, desalojo, represion, servicios, salud, censura, persecucion, general",
        min_length=1,
    )
    severity: str = Field(
        default="info",
        description="Severity level: critical, high, medium, info",
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="Optional tags for the denuncia",
    )

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        valid = list(CATEGORY_LABELS.keys())
        if v not in valid:
            raise ValueError(f"Invalid category '{v}'. Must be one of: {', '.join(valid)}")
        return v

    @field_validator('severity')
    @classmethod
    def validate_severity(cls, v):
        valid = ['critical', 'high', 'medium', 'info']
        if v not in valid:
            raise ValueError(f"Invalid severity '{v}'. Must be one of: {', '.join(valid)}")
        return v


class GenerateDossierInput(BaseModel):
    """Input for generating dossier content for a denuncia."""
    model_config = ConfigDict(str_strip_whitespace=True)
    
    denuncia_id: str = Field(
        ...,
        description="Expediente ID (e.g., 'EV-2026-0001') or row ID",
        min_length=1,
    )
    resumen: str = Field(
        ...,
        description="Executive summary of the denuncia (2-4 sentences)",
        min_length=10,
    )
    contexto: str = Field(
        default="",
        description="Background context and circumstances",
    )
    fuentes: Optional[List[str]] = Field(
        default=None,
        description="List of source URLs or references",
    )
    evidencias: Optional[List[str]] = Field(
        default=None,
        description="List of evidence descriptions or URLs",
    )
    conclusion: str = Field(
        default="",
        description="Conclusions or findings",
    )


class ListDenunciasInput(BaseModel):
    """Input for listing denuncias with filters."""
    category: Optional[str] = Field(default=None, description="Filter by category")
    status: Optional[str] = Field(default=None, description="Filter by status: draft or published")
    severity: Optional[str] = Field(default=None, description="Filter by severity")
    limit: int = Field(default=20, description="Max results", ge=1, le=200)
    offset: int = Field(default=0, description="Pagination offset", ge=0)


class GetDenunciaInput(BaseModel):
    """Input for getting a single denuncia."""
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)


class UpdateDenunciaInput(BaseModel):
    """Input for updating denuncia fields."""
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)
    fields: Dict[str, Any] = Field(
        ...,
        description="Dict of fields to update. Valid: category, severity, status, tags, resumen, contexto, fuentes, evidencias, conclusion",
    )


class PublishDenunciaInput(BaseModel):
    """Input for publishing a denuncia."""
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)


class UnpublishDenunciaInput(BaseModel):
    """Input for unpublishing a denuncia."""
    denuncia_id: str = Field(..., description="Expediente ID or row ID", min_length=1)


class RunCycleInput(BaseModel):
    """Input for running a full automated cycle."""
    queries: Optional[List[str]] = Field(
        default=None,
        description="Custom queries. If None, uses default EXPEDIENTE_QUERIES.",
    )
    max_results: int = Field(default=20, description="Max results per query", ge=1, le=100)
```

### 4.3 Tool Implementations

```python
# ============================================================
# TOOL 1: search_denuncias
# ============================================================
@mcp.tool(
    name="search_denuncias",
    annotations={
        "title": "Search X/Twitter for Denuncias",
        "readOnlyHint": False,   # Writes to DB
        "destructiveHint": False,
        "idempotentHint": True,  # Deduplicates by tweet_id
        "openWorldHint": True,
    },
)
async def search_denuncias(params: SearchDenunciasInput) -> str:
    """Search X/Twitter for denuncias using Playwright + Ferdium cookies.

    Navigates X search with provided queries, scrolls to collect tweets,
    and stores new results in SQLite. Returns list of new denuncias
    that need categorization.

    Args:
        params: Search parameters containing queries and max_results.

    Returns:
        JSON with:
        - new_denuncias: list of new tweets (with expediente_id, tweet_id, text, username)
        - skipped: count of already-seen tweets
        - queries_used: which queries were executed
        - total_new: count of new denuncias

    Error cases:
        - Returns error if Ferdium cookies are expired/missing
        - Returns error if X login check fails
        - Returns empty new_denuncias if no new results found
    """
    try:
        # Run the scraper synchronously (it uses Playwright async internally)
        result = await asyncio.to_thread(
            _scraper_search,
            queries=params.queries,
            max_results=params.max_results,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "new_denuncias": []})


# ============================================================
# TOOL 2: categorize_denuncia
# ============================================================
@mcp.tool(
    name="categorize_denuncia",
    annotations={
        "title": "Categorize a Denuncia",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def categorize_denuncia(params: CategorizeDenunciaInput) -> str:
    """Categorize a single denuncia with category, severity, and tags.

    The AI agent decides the classification based on reading the denuncia text.
    This tool stores the agent's decision in the database.

    Args:
        params: Categorization including denuncia_id, category, severity, tags.

    Returns:
        JSON with the updated denuncia record.

    Error cases:
        - Returns error if denuncia_id not found
        - Returns error if invalid category or severity
    """
    try:
        conn = get_conn()
        fields = {
            "category": params.category,
            "severity": params.severity,
        }
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
    annotations={
        "title": "Generate Dossier for a Denuncia",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,  # Overwrites previous dossier
        "openWorldHint": False,
    },
)
async def generate_dossier(params: GenerateDossierInput) -> str:
    """Generate full dossier content for a denuncia.

    The AI agent writes all dossier content (resumen, contexto, fuentes,
    evidencias, conclusion). This tool stores it in the database.

    Args:
        params: Dossier content including denuncia_id and all text fields.

    Returns:
        JSON with the updated denuncia record including dossier fields.

    Error cases:
        - Returns error if denuncia_id not found
    """
    try:
        conn = get_conn()
        fields = {
            "resumen": params.resumen,
            "contexto": params.contexto,
            "conclusion": params.conclusion,
        }
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
    annotations={
        "title": "List Denuncias with Filters",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def list_denuncias_tool(params: ListDenunciasInput) -> str:
    """List denuncias with optional filters for category, status, severity.

    Returns paginated results with metadata. Useful for browsing the
    database or finding denuncias that need attention.

    Args:
        params: Filter and pagination parameters.

    Returns:
        JSON with:
        - denuncias: list of matching records
        - total: total matching count
        - limit/offset: current pagination
    """
    try:
        conn = get_conn()
        results = list_denuncias(
            conn,
            category=params.category,
            status=params.status,
            severity=params.severity,
            limit=params.limit,
            offset=params.offset,
        )
        return json.dumps({
            "denuncias": results,
            "count": len(results),
            "limit": params.limit,
            "offset": params.offset,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 5: get_denuncia
# ============================================================
@mcp.tool(
    name="get_denuncia",
    annotations={
        "title": "Get Single Denuncia",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def get_denuncia_tool(params: GetDenunciaInput) -> str:
    """Get full details of a single denuncia by ID.

    Returns the complete record including all metadata, dossier fields,
    tags, and engagement stats.

    Args:
        params: Denuncia identifier (expediente_id or row id).

    Returns:
        JSON with full denuncia record, or error if not found.
    """
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
    annotations={
        "title": "Update Denuncia Fields",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def update_denuncia_tool(params: UpdateDenunciaInput) -> str:
    """Update any field of a denuncia.

    Flexible update tool that can modify category, severity, status,
    dossier fields, tags, or any other writable column.

    Args:
        params: denuncia_id and dict of fields to update.

    Returns:
        JSON with the updated denuncia record.

    Error cases:
        - Returns error if denuncia_id not found
        - Silently ignores invalid field names
    """
    try:
        conn = get_conn()
        # Serialize complex fields
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
    annotations={
        "title": "Publish a Denuncia",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def publish_denuncia_tool(params: PublishDenunciaInput) -> str:
    """Mark a denuncia as published and export to JSON.

    Sets status to 'published', records publish timestamp,
    and triggers JSON export for the frontend slider.

    Args:
        params: denuncia_id to publish.

    Returns:
        JSON with published denuncia record and export path.

    Error cases:
        - Returns error if denuncia_id not found
        - Returns error if already published
    """
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
    annotations={
        "title": "Unpublish a Denuncia",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def unpublish_denuncia_tool(params: UnpublishDenunciaInput) -> str:
    """Remove a denuncia from public view.

    Sets status back to 'draft' and re-exports JSON (denuncia removed from frontend).

    Args:
        params: denuncia_id to unpublish.

    Returns:
        JSON with success status and updated record.
    """
    try:
        conn = get_conn()
        success = unpublish_denuncia(conn, params.denuncia_id)
        if not success:
            return json.dumps({"error": f"Denuncia {params.denuncia_id} not found"})

        # Re-export JSON to remove from public
        export_to_json(conn)
        denuncia = get_denuncia(conn, params.denuncia_id)
        return json.dumps({"success": True, "denuncia": denuncia}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# TOOL 9: stats
# ============================================================
@mcp.tool(
    name="stats",
    annotations={
        "title": "Get Database Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def stats_tool() -> str:
    """Get database statistics.

    Returns counts by category, severity, and publication status.

    Returns:
        JSON with:
        - total: total denuncias
        - by_category: count per category
        - by_severity: count per severity
        - published_count: published denuncias
        - draft_count: draft denuncias
    """
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
    annotations={
        "title": "Run Full Automated Scraping Cycle",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def run_cycle(params: RunCycleInput) -> str:
    """Run a full automated scrape cycle: search → store → return for categorization.

    Searches X/Twitter with provided or default queries, stores new results
    in SQLite, and returns the list of new denuncias for the agent to
    categorize and generate dossiers for.

    Does NOT auto-categorize — the agent decides categories.

    Args:
        params: Optional custom queries and max_results.

    Returns:
        JSON with:
        - new_denuncias: list of new records needing categorization
        - total_new: count
        - queries_used: which queries were executed
        - stats: current database stats after insert
    """
    try:
        queries = params.queries or EXPEDIENTE_QUERIES

        # Run scraper
        search_result = await asyncio.to_thread(
            _scraper_search,
            queries=queries,
            max_results=params.max_results,
        )

        # Get stats after insert
        conn = get_conn()
        stats = get_stats(conn)

        return json.dumps({
            "new_denuncias": search_result.get("new_denuncias", []),
            "total_new": search_result.get("total_new", 0),
            "skipped": search_result.get("skipped", 0),
            "queries_used": search_result.get("queries_used", []),
            "stats": stats,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "new_denuncias": []})
```

### 4.4 Resource Implementations

```python
# ============================================================
# RESOURCE 1: denuncias://published
# ============================================================
@mcp.resource("denuncias://published")
async def get_published_denuncias() -> str:
    """All published denuncias as JSON (for frontend slider consumption).

    Returns the full exported JSON including all published records
    with videos-first ordering.
    """
    try:
        conn = get_conn()
        result = export_to_json(conn)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# RESOURCE 2: denuncias://stats
# ============================================================
@mcp.resource("denuncias://stats")
async def get_denuncias_stats() -> str:
    """Database statistics summary.

    Returns total counts, breakdown by category and severity,
    publication status counts.
    """
    try:
        conn = get_conn()
        result = get_stats(conn)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ============================================================
# RESOURCE 3: denuncias://categories
# ============================================================
@mcp.resource("denuncias://categories")
async def get_denuncias_categories() -> str:
    """Categories with counts.

    Returns all categories and how many denuncias are in each.
    """
    try:
        conn = get_conn()
        stats = get_stats(conn)
        return json.dumps({
            "categories": stats.get("by_category", {}),
            "labels": CATEGORY_LABELS,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

### 4.5 Entry Point

```python
if __name__ == "__main__":
    # Initialize DB on startup
    init_db()
    # Run MCP server over stdio
    mcp.run()
```

### 4.6 Verification

```bash
# Test 1: Server starts without error
python3 -c "from mcp_server import mcp; print(f'Server: {mcp.name}')"

# Test 2: All tools registered
python3 -c "
from mcp_server import mcp
# FastMCP stores tools internally
print('Tools registered')
"

# Test 3: Syntax check
python3 -m py_compile scraper/mcp_server.py && echo "Syntax OK"

# Test 4: MCP Inspector test
npx @modelcontextprotocol/inspector python3 scraper/mcp_server.py
# Then in inspector: call stats, list_denuncias, etc.
```

---

## Plan 5: `scraper/requirements.txt` — Dependencies

**File:** `scraper/requirements.txt` (new)
**Wave:** 1 (parallel)
**Dependencies:** None

```
mcp>=1.0.0
fastmcp>=0.1.0
pydantic>=2.0.0
playwright>=1.40.0
```

### Verification

```bash
pip install -r scraper/requirements.txt
python3 -c "from mcp.server.fastmcp import FastMCP; print('MCP SDK OK')"
python3 -c "from pydantic import BaseModel; print('Pydantic OK')"
python3 -c "from playwright.async_api import async_playwright; print('Playwright OK')"
```

---

## Plan 6: `index.html` — Dynamic Frontend with Infinite Scroll Timeline

**File:** `index.html` (modify existing)
**Wave:** 4 (depends on Plan 3)
**Dependencies:** export_json.py producing `scraper/data/denuncias.json`

### 6.1 Sections to Modify

| Section | Current State | New State |
|---------|--------------|-----------|
| **Stats** (lines 289-312) | Hardcoded numbers (247, 1834, etc.) | Dynamic from JSON `count` + `categories` |
| **Expedientes grid** (lines 314-451) | 6 hardcoded cards | **Slider** with auto-rotate, loading from JSON |
| **Timeline** (lines 494-533) | 5 hardcoded events | **Infinite scroll** loading denuncias chronologically |
| **Categories** (lines 537-558) | 12 hardcoded cards | Dynamic from JSON `categories` counts |
| **Featured** (lines 562-644) | Hardcoded dossier EV-2026-0087 | Dynamic: latest published denuncia with full dossier |

### 6.2 Section A: Dynamic Stats

Replace hardcoded stat numbers with values loaded from JSON:

```javascript
function updateStats(data) {
    const cats = data.categories || {};
    const total = data.count || 0;
    const catCount = Object.keys(cats).length;
    const published = total; // JSON only contains published
    
    // Find stat elements and update
    const nums = document.querySelectorAll('.stat-num');
    if (nums[0]) nums[0].textContent = total;
    if (nums[1]) nums[1].textContent = Object.values(cats).reduce((a, b) => a + b, 0);
    if (nums[2]) nums[2].textContent = '—'; // States: can't derive from JSON
    if (nums[3]) nums[3].textContent = total;
    if (nums[4]) nums[4].textContent = catCount;
}
```

### 6.3 Section B: Denuncias Slider (replaces static cards)

The 6 hardcoded `.expediente-card` divs get replaced by a single slider component:

```html
<section id="expedientes">
  <div class="section-inner">
    <div class="section-header reveal">
      <div class="section-eyebrow">Denuncias en tiempo real</div>
      <h2 class="section-title">Últimas denuncias recibidas</h2>
      <p class="section-desc">Denuncias recopiladas automáticamente desde fuentes públicas. Cada una es un documento verificado.</p>
    </div>

    <div class="slider-container" id="slider-container">
      <div class="slider-track" id="slider-track">
        <!-- JS-rendered slides -->
      </div>
      <button class="slider-arrow slider-prev" id="slider-prev">‹</button>
      <button class="slider-arrow slider-next" id="slider-next">›</button>
      <div class="slider-dots" id="slider-dots"></div>
      <div class="slider-status" id="slider-status"></div>
    </div>
  </div>
</section>
```

**Slider CSS** (added to `<style>` block):

```css
/* --- SLIDER --- */
.slider-container{position:relative;overflow:hidden;background:var(--bg-card);border:1px solid rgba(196,184,152,.06);border-radius:var(--radius);padding:2rem}
.slider-track{display:flex;transition:transform .5s cubic-bezier(.16,1,.3,1);gap:1.5rem}
.slide{min-width:100%;display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
.slide-media{background:var(--bg-elevated);border-radius:var(--radius);overflow:hidden;min-height:280px;display:flex;align-items:center;justify-content:center}
.slide-media img{width:100%;height:100%;object-fit:cover}
.slide-media video{width:100%;height:100%;object-fit:contain}
.slide-media-placeholder{font-family:var(--mono);font-size:.7rem;color:var(--text-dim);text-align:center;padding:1rem}
.slide-content{display:flex;flex-direction:column;justify-content:space-between}
.slide-user{display:flex;align-items:center;gap:.75rem;margin-bottom:.75rem}
.slide-avatar{width:36px;height:36px;border-radius:50%;background:var(--bg-elevated);border:1px solid rgba(196,184,152,.1);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:.6rem;color:var(--yellow)}
.slide-username{font-family:var(--mono);font-size:.75rem;color:var(--beige)}
.slide-display-name{font-family:var(--serif);font-size:.85rem;color:var(--text-bright)}
.slide-expediente{font-family:var(--mono);font-size:.6rem;letter-spacing:.1em;color:var(--yellow);margin-bottom:.4rem}
.slide-text{font-size:.9rem;color:var(--text);line-height:1.6;margin-bottom:1rem;display:-webkit-box;-webkit-line-clamp:6;-webkit-box-orient:vertical;overflow:hidden}
.slide-meta{display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
.slide-stat{font-family:var(--mono);font-size:.65rem;color:var(--text-dim);display:flex;align-items:center;gap:.25rem}
.slide-link{font-family:var(--mono);font-size:.65rem;color:var(--beige);text-decoration:none}
.slider-arrow{position:absolute;top:50%;transform:translateY(-50%);background:rgba(10,10,10,.8);border:1px solid rgba(196,184,152,.15);color:var(--beige);width:40px;height:40px;font-size:1.2rem;cursor:pointer;z-index:10;transition:all .2s}
.slider-arrow:hover{border-color:var(--beige);background:rgba(10,10,10,.95)}
.slider-prev{left:1rem}.slider-next{right:1rem}
.slider-dots{display:flex;justify-content:center;gap:.5rem;margin-top:1.25rem}
.slider-dot{width:6px;height:6px;border-radius:50%;background:rgba(196,184,152,.2);cursor:pointer;transition:all .2s;border:none}
.slider-dot.active{background:var(--yellow);transform:scale(1.3)}
.slider-status{font-family:var(--mono);font-size:.55rem;color:var(--text-dim);text-align:center;margin-top:.75rem;letter-spacing:.05em}
@media(max-width:768px){.slide{grid-template-columns:1fr}.slide-media{min-height:200px}.slider-prev{left:.5rem}.slider-next{right:.5rem}}
```

**Slider JS**:

```javascript
// --- Denuncias Slider ---
function initSlider(denuncias) {
    const track = document.getElementById('slider-track');
    const dots = document.getElementById('slider-dots');
    const status = document.getElementById('slider-status');
    if (!track || !denuncias || !denuncias.length) {
        if (status) status.textContent = 'Sin denuncias disponibles';
        return;
    }

    let current = 0;
    let autoTimer = null;
    const INTERVAL = 9000;

    // Render slides
    track.innerHTML = denuncias.map(t => {
        const media = t.video_url
            ? `<video src="${esc(t.video_url)}" controls muted playsinline preload="metadata"></video>`
            : t.images && t.images.length
                ? `<img src="${esc(t.images[0])}" alt="Evidencia" loading="lazy">`
                : `<div class="slide-media-placeholder">SIN MEDIA</div>`;
        const initial = (t.username || '?')[0].toUpperCase();
        const timeAgo = timeSince(t.created_at);
        return `
            <div class="slide">
                <div class="slide-media">${media}</div>
                <div class="slide-content">
                    <div>
                        <div class="slide-expediente">${esc(t.expediente_id || '')}</div>
                        <div class="slide-user">
                            <div class="slide-avatar">${initial}</div>
                            <div>
                                <div class="slide-display-name">${esc(t.name)}</div>
                                <div class="slide-username">@${esc(t.username)}</div>
                            </div>
                        </div>
                        <div class="slide-text">${esc(t.text)}</div>
                    </div>
                    <div class="slide-meta">
                        <span class="slide-stat">♥ ${fmt(t.likes)}</span>
                        <span class="slide-stat">↻ ${fmt(t.retweets)}</span>
                        <span class="slide-stat">↩ ${fmt(t.replies)}</span>
                        <span class="slide-stat">⏱ ${timeAgo}</span>
                        <a href="${esc(t.url)}" target="_blank" rel="noopener" class="slide-link">Ver denuncia →</a>
                    </div>
                </div>
            </div>`;
    }).join('');

    // Render dots
    dots.innerHTML = denuncias.map((_, i) =>
        `<button class="slider-dot${i === 0 ? ' active' : ''}" data-idx="${i}"></button>`
    ).join('');

    function goTo(idx) {
        current = ((idx % denuncias.length) + denuncias.length) % denuncias.length;
        track.style.transform = `translateX(-${current * 100}%)`;
        dots.querySelectorAll('.slider-dot').forEach((d, i) => d.classList.toggle('active', i === current));
        resetAuto();
    }

    document.getElementById('slider-prev')?.addEventListener('click', () => goTo(current - 1));
    document.getElementById('slider-next')?.addEventListener('click', () => goTo(current + 1));
    dots.addEventListener('click', e => { if (e.target.dataset.idx !== undefined) goTo(+e.target.dataset.idx); });
    document.addEventListener('keydown', e => {
        if (e.key === 'ArrowLeft') goTo(current - 1);
        if (e.key === 'ArrowRight') goTo(current + 1);
    });

    function resetAuto() { clearInterval(autoTimer); autoTimer = setInterval(() => goTo(current + 1), INTERVAL); }
    resetAuto();

    const container = document.querySelector('.slider-container');
    container?.addEventListener('mouseenter', () => clearInterval(autoTimer));
    container?.addEventListener('mouseleave', resetAuto);
}
```

### 6.4 Section C: Infinite Scroll Timeline (replaces static timeline)

The 5 hardcoded `.timeline-item` divs get replaced by a dynamically loaded infinite scroll:

```html
<section id="cronologia">
  <div class="section-inner">
    <div class="section-header reveal">
      <div class="section-eyebrow">Línea de tiempo</div>
      <h2 class="section-title">Cronología de denuncias</h2>
      <p class="section-desc">Scroll infinito con todas las denuncias recibidas, de más reciente a más antigua.</p>
    </div>

    <div class="timeline" id="timeline">
      <!-- JS-rendered timeline items -->
    </div>

    <div class="timeline-loader" id="timeline-loader">
      <div class="timeline-spinner"></div>
      <span class="timeline-loader-text">Cargando más denuncias...</span>
    </div>

    <div class="timeline-end" id="timeline-end" style="display:none">
      <span>No hay más denuncias</span>
    </div>
  </div>
</section>
```

**Timeline CSS** (replaces existing timeline styles):

```css
/* --- INFINITE SCROLL TIMELINE --- */
.timeline{position:relative;padding-left:2rem}
.timeline::before{content:'';position:absolute;left:0;top:0;bottom:0;width:1px;background:rgba(196,184,152,.1)}
.timeline-item{position:relative;padding:1.5rem 0 1.5rem 1.5rem;border-bottom:1px solid rgba(196,184,152,.04);opacity:0;transform:translateY(20px);transition:opacity .5s,transform .5s}
.timeline-item.visible{opacity:1;transform:none}
.timeline-item::before{content:'';position:absolute;left:-2rem;top:1.75rem;width:8px;height:8px;border:1.5px solid var(--beige);background:var(--bg);transform:translateX(-3.5px)}
.timeline-item.has-video::before{background:var(--red);border-color:var(--red)}
.timeline-expediente{font-family:var(--mono);font-size:.55rem;letter-spacing:.12em;color:var(--yellow);margin-bottom:.3rem}
.timeline-category{display:inline-block;font-family:var(--mono);font-size:.5rem;letter-spacing:.08em;text-transform:uppercase;padding:.15rem .5rem;border-radius:1px;margin-bottom:.4rem}
.timeline-date{font-family:var(--mono);font-size:.6rem;letter-spacing:.1em;color:var(--text-dim);margin-bottom:.4rem}
.timeline-title{font-family:var(--serif);font-size:1rem;font-weight:600;color:var(--text-bright);margin-bottom:.3rem}
.timeline-excerpt{font-size:.8rem;color:var(--text-dim);line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.timeline-meta{display:flex;gap:.75rem;margin-top:.5rem;flex-wrap:wrap}
.timeline-stat{font-family:var(--mono);font-size:.55rem;color:var(--text-dim)}
.timeline-link{font-family:var(--mono);font-size:.55rem;color:var(--beige);text-decoration:none}
.timeline-link:hover{color:var(--yellow)}
.timeline-media{margin-top:.75rem;border-radius:var(--radius);overflow:hidden;max-height:200px}
.timeline-media img{width:100%;height:100%;object-fit:cover;opacity:.8}
.timeline-media video{width:100%;max-height:200px;object-fit:contain}

/* Loader */
.timeline-loader{display:flex;align-items:center;justify-content:center;gap:.75rem;padding:2rem;opacity:.6}
.timeline-spinner{width:16px;height:16px;border:1.5px solid rgba(196,184,152,.15);border-top-color:var(--yellow);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.timeline-loader-text{font-family:var(--mono);font-size:.65rem;color:var(--text-dim);letter-spacing:.05em}
.timeline-end{text-align:center;padding:2rem;font-family:var(--mono);font-size:.65rem;color:var(--text-dim)}
```

**Infinite Scroll JS**:

```javascript
// --- Infinite Scroll Timeline ---
const TIMELINE_BATCH = 8; // items per load
let timelineData = [];
let timelineIndex = 0;
let timelineLoading = false;

function initTimeline(denuncias) {
    const container = document.getElementById('timeline');
    const loader = document.getElementById('timeline-loader');
    const endMsg = document.getElementById('timeline-end');
    if (!container || !denuncias || !denuncias.length) {
        if (loader) loader.style.display = 'none';
        if (endMsg) endMsg.style.display = 'block';
        return;
    }

    // Sort: most recent first
    timelineData = [...denuncias].sort((a, b) =>
        new Date(b.created_at || b.scraped_at) - new Date(a.created_at || a.scraped_at)
    );
    timelineIndex = 0;

    // Initial load
    loadTimelineBatch();

    // IntersectionObserver for infinite scroll
    const sentinel = document.createElement('div');
    sentinel.id = 'timeline-sentinel';
    container.after(sentinel);

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && !timelineLoading) {
            loadTimelineBatch();
        }
    }, { rootMargin: '200px' });
    observer.observe(sentinel);
}

function loadTimelineBatch() {
    if (timelineLoading || timelineIndex >= timelineData.length) {
        const loader = document.getElementById('timeline-loader');
        const endMsg = document.getElementById('timeline-end');
        if (loader) loader.style.display = 'none';
        if (endMsg && timelineIndex >= timelineData.length) endMsg.style.display = 'block';
        return;
    }

    timelineLoading = true;
    const container = document.getElementById('timeline');
    const batch = timelineData.slice(timelineIndex, timelineIndex + TIMELINE_BATCH);

    batch.forEach((t, i) => {
        const item = document.createElement('div');
        item.className = 'timeline-item' + (t.video_url ? ' has-video' : '');

        const catColor = getCategoryColor(t.category);
        const catLabel = t.category_label || t.category || 'General';
        const date = t.created_at ? new Date(t.created_at).toLocaleDateString('es-VE', { day: '2-digit', month: 'short', year: 'numeric' }) : '';
        const timeAgo = timeSince(t.created_at);

        let media = '';
        if (t.video_url) {
            media = `<div class="timeline-media"><video src="${esc(t.video_url)}" controls muted playsinline preload="metadata"></video></div>`;
        } else if (t.images && t.images.length) {
            media = `<div class="timeline-media"><img src="${esc(t.images[0])}" alt="Evidencia" loading="lazy"></div>`;
        }

        item.innerHTML = `
            <div class="timeline-expediente">${esc(t.expediente_id || '')}</div>
            <span class="timeline-category" style="color:${catColor};border:1px solid ${catColor}30">${catLabel}</span>
            <div class="timeline-date">${date} · hace ${timeAgo}</div>
            <h3 class="timeline-title">${esc(t.name)} (@${esc(t.username)})</h3>
            <p class="timeline-excerpt">${esc(t.text)}</p>
            ${media}
            <div class="timeline-meta">
                <span class="timeline-stat">♥ ${fmt(t.likes)}</span>
                <span class="timeline-stat">↻ ${fmt(t.retweets)}</span>
                <span class="timeline-stat">↩ ${fmt(t.replies)}</span>
                <a href="${esc(t.url)}" target="_blank" rel="noopener" class="timeline-link">Ver denuncia →</a>
            </div>`;

        container.appendChild(item);

        // Animate in with stagger
        setTimeout(() => item.classList.add('visible'), i * 80);
    });

    timelineIndex += batch.length;
    timelineLoading = false;
}

function getCategoryColor(cat) {
    const colors = {
        corrupcion: '#8b2500', abuso: '#a63200', extorsion: '#c9a227',
        desalojo: '#6b6352', represion: '#e63946', servicios: '#3a86ff',
        salud: '#00e87b', censura: '#9b5de5', persecucion: '#f77f00', general: '#7a7568'
    };
    return colors[cat] || colors.general;
}
```

### 6.5 Section D: Dynamic Categories

Replace hardcoded category cards with real counts:

```javascript
function updateCategories(categories) {
    const grid = document.querySelector('.categories-grid');
    if (!grid || !categories) return;

    const labels = {
        corrupcion: 'Corrupción', abuso: 'Abuso policial o militar',
        extorsion: 'Extorsión', desalojo: 'Desalojo', represion: 'Represión',
        servicios: 'Servicios públicos', salud: 'Salud', censura: 'Censura',
        persecucion: 'Persecución política', general: 'General'
    };
    const colors = {
        corrupcion: '#8b2500', abuso: '#a63200', extorsion: '#c9a227',
        desalojo: '#6b6352', represion: '#e63946', servicios: '#3a86ff',
        salud: '#00e87b', censura: '#9b5de5', persecucion: '#f77f00', general: '#7a7568'
    };

    grid.innerHTML = Object.entries(categories)
        .filter(([_, count]) => count > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([cat, count]) => `
            <div class="category-card reveal visible">
                <div class="category-icon" style="color:${colors[cat] || colors.general}">◈</div>
                <div class="category-name">${labels[cat] || cat}</div>
                <div class="category-count">${count} denuncias</div>
            </div>
        ).join('');
}
```

### 6.6 Main Init Script

```javascript
// --- Main Init ---
(async function initExpediente() {
    try {
        const res = await fetch('scraper/data/denuncias.json');
        if (!res.ok) return;
        const data = await res.json();
        const denuncias = data.denuncias || [];

        updateStats(data);
        initSlider(denuncias);
        initTimeline(denuncias);
        updateCategories(data.categories);

        // Update status text
        const status = document.getElementById('slider-status');
        if (status && denuncias.length) {
            status.textContent = `${denuncias.length} denuncias · actualizado ${new Date(data.updated_at).toLocaleString('es-VE')}`;
        }
    } catch (e) {
        console.log('Expediente Venezuela: could not load data', e);
    }
})();

// Helpers
function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function fmt(n) { return (n || 0).toLocaleString('es-VE'); }
function timeSince(iso) {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h`;
    return `${Math.floor(hrs / 24)}d`;
}
```

### 6.7 Category Color CSS Variables

```css
:root {
    --cat-corrupcion: #8b2500;
    --cat-abuso: #a63200;
    --cat-extorsion: #c9a227;
    --cat-desalojo: #6b6352;
    --cat-represion: #e63946;
    --cat-servicios: #3a86ff;
    --cat-salud: #00e87b;
    --cat-censura: #9b5de5;
    --cat-persecucion: #f77f00;
    --cat-general: #7a7568;
}
```

### 6.8 What Gets Removed

- All 6 `.expediente-card` divs (lines 324-449)
- All 5 hardcoded `.timeline-item` divs (lines 501-532)
- Hardcoded `.expedientes-grid` CSS (replaced by slider styles)
- Existing timeline CSS (replaced by infinite scroll styles)

### 6.9 What Stays Unchanged

- Header, Hero, Map, Featured Investigation, Form, Methodology, Footer
- All existing CSS variables and base styles
- IntersectionObserver for `.reveal` animations
- Map filter toggle logic
- Form submission handler

### 6.10 Verification

```bash
# Open in browser, check:
# 1. Stats load dynamically from JSON (not hardcoded)
# 2. Slider shows denuncias with video-first ordering
# 3. Auto-rotates every 9 seconds
# 4. Arrow/dot/keyboard navigation works
# 5. Timeline loads 8 items initially
# 6. Scrolling down loads more (infinite scroll)
# 7. Each timeline item animates in
# 8. Category cards show real counts
# 9. Mobile responsive (stacked layout)
```

---

## Complete File Inventory

| File | Action | Wave | Size Est. |
|------|--------|------|-----------|
| `scraper/denuncias_db.py` | Create | 1 | ~300 lines |
| `scraper/denuncias_scraper.py` | Modify | 1 | ~400 lines (was 367) |
| `scraper/export_json.py` | Create | 2 | ~60 lines |
| `scraper/mcp_server.py` | Create | 3 | ~500 lines |
| `scraper/requirements.txt` | Create | 1 | ~5 lines |
| `index.html` | Modify | 4 | +50 lines of JS |
| `scraper/data/denuncias.db` | Auto-created | 1 | SQLite |
| `scraper/data/denuncias.json` | Auto-updated | 2 | JSON export |

---

## Dependency Graph

```
Plan 1 (denuncias_db.py)  ──┐
Plan 2 (scraper refactor) ──┤──→ Plan 3 (export_json.py) ──→ Plan 4 (mcp_server.py) ──→ Plan 6 (index.html)
Plan 5 (requirements.txt) ──┘
```

**Wave 1 (parallel):** Plans 1, 2, 5
**Wave 2:** Plan 3
**Wave 3:** Plan 4
**Wave 4:** Plan 6

---

## Agent Workflow (End-to-End)

### Manual Workflow
```
1. Agent → search_denuncias(queries=["corrupción Venezuela", "abuso policial"])
   Server → Playwright scrapes X, stores 15 new tweets in SQLite
   Server → returns list with expediente_ids

2. Agent → get_denuncia(denuncia_id="EV-2026-0001")
   Server → returns full record (tweet text, username, etc.)

3. Agent → categorize_denuncia(
       denuncia_id="EV-2026-0001",
       category="corrupcion",
       severity="high",
       tags=["ministerio", "fondos-publicos"]
   )
   Server → updates DB row

4. Agent → generate_dossier(
       denuncia_id="EV-2026-0001",
       resumen="Funcionarios del Ministerio de X desviaron fondos...",
       contexto="Según fuentes públicas...",
       fuentes=["https://x.com/user/status/123", "https://news.com/..."],
       evidencias=["Captura del tweet original", "Registro de transacciones"],
       conclusion="La denuncia presenta indicios consistentes..."
   )
   Server → stores dossier fields in DB

5. Agent → publish_denuncia(denuncia_id="EV-2026-0001")
   Server → sets status='published', exports JSON
   Server → frontend slider auto-updates
```

### Full Auto Cycle
```
1. Agent → run_cycle(queries=["corrupción Venezuela", "extorsión"], max_results=20)
   Server → scrapes X, stores 15 new denuncias
   Server → returns new_denuncias list + stats

2. Agent → For each denuncia in new_denuncias:
   a. get_denuncia(denuncia_id=...)
   b. Read the tweet text
   c. categorize_denuncia(denuncia_id=..., category=..., severity=...)
   d. generate_dossier(denuncia_id=..., resumen=..., contexto=..., ...)
   e. publish_denuncia(denuncia_id=...)
```

---

## opencode.json Configuration

```json
{
  "mcp": {
    "expediente": {
      "type": "stdio",
      "command": "python3",
      "args": ["/home/pctorre/expediente-venezuela/scraper/mcp_server.py"],
      "env": {}
    }
  }
}
```

---

## Migration: Existing Data

The existing `scraper/data/denuncias.json` has 20 records without expediente_ids. The `migrate_from_json()` function in denuncias_db.py handles this:

```python
from denuncias_db import init_db, migrate_from_json
from pathlib import Path

conn = init_db()
count = migrate_from_json(conn, Path("scraper/data/denuncias.json"))
print(f"Migrated {count} records")
conn.close()
```

This assigns sequential EV-2026-NNNN IDs to all existing records and stores them in SQLite.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Ferdium cookies expired | `search_denuncias` returns error with clear message; agent can ask user to refresh cookies |
| Playwright not installed | `requirements.txt` includes it; first run auto-installs browsers via `playwright install chromium` |
| SQLite locked during concurrent access | SQLite handles this with WAL mode; MCP server uses single connection |
| Large dataset slow queries | Indexes on category, status, severity; LIMIT on all list queries |
| JSON export corruption | Write to temp file first, then atomic rename |
| Scraper detected by X | Random delays, scroll patterns, human-like behavior already in scraper |
