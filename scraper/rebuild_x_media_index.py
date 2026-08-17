#!/usr/bin/env python3
"""Rebuild scraper/data/x-media-index.json from BrowserOS capture batches.

The archive treats X posts as evidence attached to an expediente. BrowserOS may
add multiple `videos_nuevos*.json` batches; this script folds them into a single
deduplicated media index and preserves thumbnail/poster metadata when captured.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
OUTPUT = DATA_DIR / "x-media-index.json"
STATUS_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)", re.I)


def parse_post(url: str):
    match = STATUS_RE.search(str(url or ""))
    if not match:
        return None
    username, tweet_id = match.groups()
    return username, tweet_id, f"https://x.com/{username}/status/{tweet_id}"


def batch_date(payload: dict, path: Path) -> str:
    value = str(payload.get("fecha") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    name_match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return name_match.group(1) if name_match else ""


def first_media_image(item: dict) -> str:
    """Return the best captured poster/thumbnail without inventing one."""
    for key in ("thumbnail_url", "poster_url", "preview_url", "thumbnail", "image"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://", "/")):
            return value
    images = item.get("images") or item.get("image_urls") or []
    if isinstance(images, str):
        images = [images]
    if isinstance(images, list):
        for value in images:
            if isinstance(value, dict):
                value = value.get("url") or value.get("src") or value.get("image")
            if isinstance(value, str) and value.startswith(("http://", "https://", "/")):
                return value
    return ""


VEN_KEYWORDS = (
    "venezuela", "denuncia", "denunci", "prision", "prisiones", "presos",
    "represión", "represion", "golpiza", "maltrato", "aeropuerto", "táchira",
    "tachira", "funcionario", "funcionarios", "malandro", "uniforme",
)


def _is_relevant_denuncia(item: dict) -> bool:
    text = str(item.get("text") or item.get("texto") or "").lower()
    return any(kw in text for kw in VEN_KEYWORDS)


def _channel_rows(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def make_record(item: dict, canonical_url: str, username: str, tweet_id: str, captured_at: str, status: str, default_category: str = "por-clasificar") -> dict:
    has_video = item.get("has_video") is True or bool(item.get("video_url"))
    record = {
        "tweet_id": tweet_id,
        "username": item.get("username") or username,
        "name": item.get("name") or item.get("display_name") or "",
        "url": canonical_url,
        "category": item.get("categoria") or item.get("category") or default_category,
        "has_video": has_video,
        "media_type": item.get("media_type") or ("video" if has_video else ("image" if first_media_image(item) else "post")),
        "captured_at": item.get("captured_at") or captured_at,
        "tweet_created_at": item.get("tweet_created_at") or item.get("created_at") or "",
        "thumbnail_url": first_media_image(item),
        "video_url": item.get("video_url") or "",
        "text": item.get("texto") or item.get("text") or "",
        "expediente_id": item.get("expediente_id") or item.get("case_id") or "",
        "source_status": item.get("source_status") or status,
    }
    # Keep optional evidence-level editorial labels when the capture process has them.
    for key in ("evidence_id", "label"):
        if item.get(key):
            record[key] = item[key]
    return record


def main() -> None:
    merged: dict[str, dict] = {}

    for path in sorted(DATA_DIR.glob("videos_nuevos*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        captured_at = batch_date(payload, path)
        rows = payload.get("denuncias", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            post = parse_post(item.get("url", ""))
            if not post:
                continue
            username, tweet_id, canonical_url = post
            merged[tweet_id] = make_record(item, canonical_url, username, tweet_id, captured_at, "captured")

    for path in sorted(DATA_DIR.glob("canal-*.json")):
        is_retweet = "retweets" in path.name
        for item in _channel_rows(path):
            if not is_retweet and not _is_relevant_denuncia(item):
                continue
            post = parse_post(item.get("url", ""))
            if not post:
                continue
            username, tweet_id, canonical_url = post
            merged[tweet_id] = make_record(
                item, canonical_url, username, tweet_id,
                item.get("captured_at") or "2026-08-16",
                "canal-" + ("retweet" if is_retweet else "like"),
                "denuncia" if is_retweet else "por-clasificar",
            )

    sources = sorted(merged.values(), key=lambda item: (item.get("captured_at") or "", item.get("tweet_id") or ""), reverse=True)
    output = {"updated_at": datetime.now(timezone.utc).isoformat(), "count": len(sources), "sources": sources}
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "count": len(sources), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
