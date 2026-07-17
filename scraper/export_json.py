#!/usr/bin/env python3
"""Standalone JSON export for Expediente Venezuela.

Run: python3 export_json.py
Output: scraper/data/denuncias.json
"""
import json
from pathlib import Path
try:
    from .denuncias_db import init_db, export_to_json, EXPORT_PATH
except ImportError:
    from denuncias_db import init_db, export_to_json, EXPORT_PATH


def run_export() -> dict:
    """Full export: init DB, export published denuncias to JSON."""
    conn = init_db()
    result = export_to_json(conn)
    conn.close()
    return result


if __name__ == "__main__":
    result = run_export()
    print(f"Exported {result['count']} denuncias -> {EXPORT_PATH}")
    print(f"Categories: {result['categories']}")
