#!/usr/bin/env python3
"""Bulk-import public X post URLs into scraper/data/fuentes-x.json.

Usage:
    python3 scraper/import_x_links.py links.txt
    cat links.txt | python3 scraper/import_x_links.py -

The importer stores references only. It does not claim that a submitted post is
verified, and it does not download media. The archive frontend can merge these
references with the canonical denuncias.json dataset and lazy-load the original
X post when a reader asks to view it.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DATA_PATH = Path(__file__).parent / "data" / "fuentes-x.json"
X_STATUS_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)", re.I)


def read_lines(source: str) -> Iterable[str]:
    if source == "-":
        yield from sys.stdin
        return
    with open(source, encoding="utf-8") as handle:
        yield from handle


def normalize_url(raw: str):
    raw = raw.strip().strip('"\'<>[](){}.,;')
    if not raw:
        return None
    match = X_STATUS_RE.search(raw)
    if not match:
        return None
    username, post_id = match.groups()
    return {
        "tweet_id": post_id,
        "username": username,
        "url": f"https://x.com/{username}/status/{post_id}",
    }


def load_feed():
    if not DATA_PATH.exists():
        return {"updated_at": None, "count": 0, "sources": []}
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    sources = data.get("sources") if isinstance(data, dict) else []
    if not isinstance(sources, list):
        sources = []
    return {"updated_at": data.get("updated_at") if isinstance(data, dict) else None, "count": len(sources), "sources": sources}


def main():
    if len(sys.argv) != 2:
        print("Uso: python3 scraper/import_x_links.py links.txt | -", file=sys.stderr)
        raise SystemExit(2)

    feed = load_feed()
    existing_ids = {str(item.get("tweet_id")) for item in feed["sources"] if item.get("tweet_id")}
    existing_urls = {item.get("url") for item in feed["sources"] if item.get("url")}

    added = 0
    rejected = 0
    for line in read_lines(sys.argv[1]):
        source = normalize_url(line)
        if not source:
            if line.strip():
                rejected += 1
            continue
        if source["tweet_id"] in existing_ids or source["url"] in existing_urls:
            continue
        source.update({
            "category": "por-clasificar",
            "status": "source-recorded",
            "added_at": datetime.now(timezone.utc).isoformat(),
            "note": "Fuente incorporada manualmente; requiere clasificación y revisión editorial."
        })
        feed["sources"].append(source)
        existing_ids.add(source["tweet_id"])
        existing_urls.add(source["url"])
        added += 1

    feed["sources"].sort(key=lambda item: item.get("added_at") or "", reverse=True)
    feed["count"] = len(feed["sources"])
    feed["updated_at"] = datetime.now(timezone.utc).isoformat()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"ok": True, "added": added, "rejected": rejected, "total": feed["count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
