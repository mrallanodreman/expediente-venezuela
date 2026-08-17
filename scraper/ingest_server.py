#!/usr/bin/env python3
"""HTTP intake API for Expediente Venezuela.

This service receives citizen submissions and scraper batches, stores them as
DRAFT records, and exposes a very small operational API. Publication remains a
separate editorial action: receiving a report never means it has been verified.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent))

from denuncias_db import (  # noqa: E402
    export_to_json,
    get_stats,
    init_db,
    insert_denuncia,
    list_denuncias,
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8787"))
PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", "https://edgemarketing.art")
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(256 * 1024)))
MAX_DESCRIPTION_CHARS = 12_000
MAX_EVIDENCE_CHARS = 4_000
MAX_DOMAIN_CHARS = 255
SNAPSHOT_PATH = Path(__file__).parent / "data" / "denuncias.json"

ALLOWED_SEVERITIES = {"critical", "high", "medium", "info"}
ALLOWED_TYPES = {
    "vuln_web",
    "data_leak",
    "credencial",
    "git_exposure",
    "admin_panel",
    "corrupcion",
    "ddhh",
    "censura",
    "persecucion",
    "servicios",
    "otro",
}


def _clean_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    return text[:max_chars]


def _tweet_id_from_url(url: str) -> str:
    if "/status/" not in url:
        return ""
    value = url.rstrip("/").split("/status/", 1)[-1].split("?", 1)[0].split("/", 1)[0]
    return value if value.isdigit() else ""


def _snapshot_source_id(item: Dict[str, Any], expediente_id: str, source_url: str) -> str:
    """Return a deterministic internal source id when a row is not an X status URL.

    The database schema still calls this legacy identity column ``tweet_id``.
    Historical snapshots, however, contain non-X and missing URLs. Those rows
    must not disappear merely because they are not tweets, so restoration uses
    a stable synthetic identifier without pretending that an X post exists.
    """
    native = _tweet_id_from_url(source_url)
    if native:
        return native
    seed = "|".join(
        [
            expediente_id,
            source_url,
            _clean_text(item.get("username"), 120),
            _clean_text(item.get("text"), 500),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"snapshot-{digest}"


def _snapshot_sources(item: Dict[str, Any], primary_source_id: str, primary_url: str) -> list:
    """Preserve source references from the publication snapshot without merging cases."""
    sources = []
    raw_sources = item.get("sources", [])
    if isinstance(raw_sources, list):
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            url = _clean_text(source.get("url"), 2000)
            if not url:
                continue
            sources.append(
                {
                    "tweet_id": _tweet_id_from_url(url),
                    "username": _clean_text(source.get("username"), 120),
                    "text": "",
                    "url": url,
                }
            )

    if not sources:
        sources.append(
            {
                "tweet_id": primary_source_id if primary_source_id.isdigit() else "",
                "username": _clean_text(item.get("username"), 120),
                "text": _clean_text(item.get("text"), 200),
                "url": primary_url,
            }
        )
    return sources


def bootstrap_database() -> int:
    """Seed a brand-new operational DB from the canonical published snapshot.

    This is a restoration path, not an ingestion path. Every canonical snapshot
    row is restored one-to-one, preserving its expediente_id. Topic-based
    merging is intentionally bypassed here. Non-X rows receive deterministic
    internal source ids because the legacy schema requires an identity value.
    """
    conn = init_db()
    try:
        if get_stats(conn).get("total", 0) > 0 or not SNAPSHOT_PATH.exists():
            return 0

        try:
            payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0

        rows = payload.get("denuncias", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return 0

        identities = []
        native_x = 0
        for item in rows:
            if not isinstance(item, dict):
                continue
            expediente_id = _clean_text(item.get("expediente_id"), 64)
            if not expediente_id:
                continue
            source_url = _clean_text(item.get("url"), 2000)
            native_x += 1 if _tweet_id_from_url(source_url) else 0
            identities.append((expediente_id, _snapshot_source_id(item, expediente_id, source_url)))

        unique_expedientes = {expediente_id for expediente_id, _ in identities}
        unique_source_ids = {source_id for _, source_id in identities}
        print(
            "Snapshot identity diagnostics: "
            f"rows={len(rows)} restorable={len(identities)} "
            f"unique_expedientes={len(unique_expedientes)} "
            f"native_x_ids={native_x} synthetic_ids={len(identities) - native_x} "
            f"unique_source_ids={len(unique_source_ids)}"
        )

        snapshot_time = _clean_text(payload.get("updated_at"), 80) or datetime.now(timezone.utc).isoformat()
        inserted = 0

        for item in rows:
            if not isinstance(item, dict):
                continue

            expediente_id = _clean_text(item.get("expediente_id"), 64)
            if not expediente_id:
                continue
            source_url = _clean_text(item.get("url"), 2000)
            source_id = _snapshot_source_id(item, expediente_id, source_url)

            images = item.get("images", []) if isinstance(item.get("images", []), list) else []
            sources = _snapshot_sources(item, source_id, source_url)
            source_count = max(1, int(item.get("source_count", len(sources)) or len(sources) or 1))

            cur = conn.execute(
                """INSERT OR IGNORE INTO denuncias
                (expediente_id, tweet_id, username, display_name, text, category, severity,
                 status, video_url, images, retweets, likes, replies, created_at, scraped_at,
                 published_at, source_url, resumen, topic_hash, source_tweets, source_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    expediente_id,
                    source_id,
                    _clean_text(item.get("username"), 120),
                    _clean_text(item.get("name") or item.get("username"), 180),
                    _clean_text(item.get("text"), MAX_DESCRIPTION_CHARS),
                    _clean_text(item.get("category") or "general", 64),
                    _clean_text(item.get("severity") or "info", 32),
                    _clean_text(item.get("video_url"), 2000) or None,
                    json.dumps(images, ensure_ascii=False),
                    int(item.get("retweets", 0) or 0),
                    int(item.get("likes", 0) or 0),
                    int(item.get("replies", 0) or 0),
                    _clean_text(item.get("created_at"), 80),
                    snapshot_time,
                    snapshot_time,
                    source_url,
                    _clean_text(item.get("resumen"), 2000) or None,
                    f"snapshot-{source_id}",
                    json.dumps(sources, ensure_ascii=False),
                    source_count,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1

        conn.commit()
        return inserted
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "ExpedienteVenezuela/2"

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in {"/health", "/api/health"}:
            return self._json(
                200,
                {
                    "ok": True,
                    "service": "expediente-venezuela-intake",
                    "time": datetime.now(timezone.utc).isoformat(),
                },
            )

        if path == "/api/stats":
            conn = init_db()
            try:
                stats = get_stats(conn)
            finally:
                conn.close()
            return self._json(200, {"ok": True, "stats": stats})

        if path == "/api/denuncias/pending":
            token = os.getenv("ADMIN_READ_TOKEN")
            if not token or self.headers.get("Authorization") != f"Bearer {token}":
                return self._json(401, {"ok": False, "error": "Unauthorized"})
            conn = init_db()
            try:
                rows = list_denuncias(conn, status="draft", limit=200)
            finally:
                conn.close()
            return self._json(200, {"ok": True, "items": rows})

        return self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/denuncias/form":
            return self._handle_form()
        if path == "/api/denuncias/ingest":
            return self._handle_ingest()
        return self._json(404, {"ok": False, "error": "Not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "600")
        self._security_headers()
        self.end_headers()

    def _read_json(self) -> Dict[str, Any] | list | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _handle_ingest(self):
        ingest_token = os.getenv("INGEST_TOKEN")
        if not ingest_token or self.headers.get("Authorization") != f"Bearer {ingest_token}":
            return self._json(401, {"ok": False, "error": "Unauthorized"})

        data = self._read_json()
        if data is None:
            return self._json(400, {"ok": False, "error": "Invalid JSON or body too large"})

        tweets = data if isinstance(data, list) else data.get("tweets", [])
        if not isinstance(tweets, list) or not tweets:
            return self._json(400, {"ok": False, "error": "No items"})

        conn = init_db()
        results = []
        try:
            for item in tweets[:500]:
                tweet_id = _clean_text(item.get("id"), 128)
                source_url = _clean_text(item.get("url"), 2000)
                if not tweet_id or not source_url:
                    continue
                result = insert_denuncia(
                    conn,
                    {
                        "tweet_id": tweet_id,
                        "username": _clean_text(item.get("username"), 120),
                        "display_name": _clean_text(item.get("name") or item.get("username"), 180),
                        "text": _clean_text(item.get("text"), MAX_DESCRIPTION_CHARS),
                        "video_url": _clean_text(item.get("video_url"), 2000) or None,
                        "images": item.get("images", []) if isinstance(item.get("images", []), list) else [],
                        "retweets": int(item.get("retweets", 0) or 0),
                        "likes": int(item.get("likes", 0) or 0),
                        "replies": int(item.get("replies", 0) or 0),
                        "created_at": _clean_text(item.get("created_at"), 80),
                        "source_url": source_url,
                        "status": "draft",
                    },
                )
                results.append(result)
            export_to_json(conn)
        finally:
            conn.close()

        new_ids = [r["expediente_id"] for r in results if r.get("is_new")]
        return self._json(
            200,
            {
                "ok": True,
                "received": len(results),
                "inserted": len(new_ids),
                "expediente_ids": new_ids,
            },
        )

    def _handle_form(self):
        data = self._read_json()
        if not isinstance(data, dict):
            return self._json(400, {"ok": False, "error": "Invalid JSON or body too large"})

        tipo = _clean_text(data.get("tipo") or "otro", 64)
        dominio = _clean_text(data.get("dominio"), MAX_DOMAIN_CHARS)
        descripcion = _clean_text(data.get("descripcion"), MAX_DESCRIPTION_CHARS)
        evidencia = _clean_text(data.get("evidencia"), MAX_EVIDENCE_CHARS)
        severidad = _clean_text(data.get("severidad") or "info", 32)
        fecha = _clean_text(data.get("fecha"), 32)

        if tipo not in ALLOWED_TYPES:
            tipo = "otro"
        if severidad not in ALLOWED_SEVERITIES:
            severidad = "info"
        if len(descripcion) < 30:
            return self._json(400, {"ok": False, "error": "La descripción debe tener al menos 30 caracteres"})

        now = datetime.now(timezone.utc).isoformat()
        nonce = secrets.token_hex(8)
        evidence_items = []
        if evidencia:
            evidence_items.append({"type": "submitted_reference", "value": evidencia})

        conn = init_db()
        try:
            result = insert_denuncia(
                conn,
                {
                    "tweet_id": f"form-{nonce}",
                    "username": "anonimo",
                    "display_name": "Aporte ciudadano",
                    "text": descripcion,
                    "category": tipo,
                    "severity": severidad,
                    "status": "draft",
                    "created_at": fecha or now[:10],
                    "scraped_at": now,
                    "source_url": dominio or "formulario-ciudadano",
                    "tags": ["formulario", tipo] + ([dominio] if dominio else []),
                    "resumen": descripcion[:240],
                    "evidencias": evidence_items,
                },
            )
        finally:
            conn.close()

        return self._json(
            202,
            {
                "ok": True,
                "status": "received",
                "expediente_id": result["expediente_id"],
                "message": "Aporte recibido para revisión. La recepción no implica verificación ni publicación.",
            },
        )

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") == PUBLIC_ORIGIN.rstrip("/"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _json(self, status: int, data: Any):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args):
        sys.stderr.write("%s\n" % (fmt % args))


def main():
    seeded = bootstrap_database()
    if seeded:
        print(f"Bootstrapped operational DB from canonical snapshot: {seeded} published records")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Expediente Venezuela intake API listening on {HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
