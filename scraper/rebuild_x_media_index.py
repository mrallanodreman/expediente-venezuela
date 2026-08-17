#!/usr/bin/env python3
"""Rebuild scraper/data/x-media-index.json from BrowserOS capture batches.

The public archive reads one canonical media index. BrowserOS may add multiple
`videos_nuevos*.json` batch files; this script folds them into a single,
deduplicated index keyed by X post ID.
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


# Keywords used to decide whether a liked post is a relevant Venezuela denuncia.
# Retweet channels are always treated as relevant (the owner chose to amplify them).
VEN_KEYWORDS = (
    "venezuela", "denuncia", "denunci", "prision", "prisiones", "presos",
    "represión", "represion", "golpiza", "maltrato", "aeropuerto", "táchira",
    "tachira", "funcionario", "funcionarios", "malandro", "uniforme",
)


def _is_relevant_denuncia(item: dict) -> bool:
    text = str(item.get("text") or "").lower()
    return any(kw in text for kw in VEN_KEYWORDS)


def _channel_rows(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


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
            has_video = item.get("has_video") is True
            record = {
                "tweet_id": tweet_id,
                "username": item.get("username") or username,
                "url": canonical_url,
                "category": item.get("categoria") or item.get("category") or "por-clasificar",
                "has_video": has_video,
                "media_type": "video" if has_video else "post",
                "captured_at": captured_at,
                "source_status": "captured",
            }
            # Later capture batches win when a post is re-observed with richer metadata.
            merged[tweet_id] = record

    # Integrate personal X channels (likes / retweets) as an additional denuncia source.
    # Retweets are always relevant (the owner chose to amplify them). Likes are only
    # folded in when the post text matches Venezuela denuncia keywords.
    for path in sorted(DATA_DIR.glob("canal-*.json")):
        is_retweet = "retweets" in path.name
        for item in _channel_rows(path):
            if not is_retweet and not _is_relevant_denuncia(item):
                continue
            post = parse_post(item.get("url", ""))
            if not post:
                continue
            username, tweet_id, canonical_url = post
            has_video = item.get("has_video") is True
            record = {
                "tweet_id": tweet_id,
                "username": item.get("username") or username,
                "name": item.get("name") or "",
                "url": canonical_url,
                "category": "denuncia" if is_retweet else "por-clasificar",
                "has_video": has_video,
                "media_type": "video" if has_video else "post",
                "captured_at": "2026-08-16",
                "source_status": "canal-" + ("retweet" if is_retweet else "like"),
            }
            merged[tweet_id] = record

    sources = sorted(
        merged.values(),
        key=lambda item: (item.get("captured_at") or "", item.get("tweet_id") or ""),
        reverse=True,
    )
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(sources),
        "sources": sources,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "count": len(sources), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
