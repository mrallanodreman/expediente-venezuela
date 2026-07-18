#!/usr/bin/env python3
"""Ingest server — receives scraped denuncias + form submissions."""
import json
import sys
import time
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from denuncias_db import init_db, insert_denuncia, list_denuncias, export_to_json

HOST = "0.0.0.0"
PORT = 8787


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/denuncias/pending":
            conn = init_db()
            rows = list_denuncias(conn, status="draft", limit=200)
            conn.close()
            return self._json(200, rows)
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/denuncias/form":
            return self._handle_form()
        if self.path != "/api/denuncias/ingest":
            return self.send_error(404)

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            return self.send_error(400, "Invalid JSON")

        tweets = data if isinstance(data, list) else data.get("tweets", [])
        if not tweets:
            return self.send_error(400, "No tweets")

        conn = init_db()
        results = []
        for t in tweets:
            r = insert_denuncia(conn, {
                "tweet_id": t["id"],
                "username": t.get("username", ""),
                "display_name": t.get("name", t.get("username", "")),
                "text": t.get("text", ""),
                "video_url": t.get("video_url"),
                "images": t.get("images", []),
                "retweets": t.get("retweets", 0),
                "likes": t.get("likes", 0),
                "replies": t.get("replies", 0),
                "created_at": t.get("created_at", ""),
                "source_url": t.get("url", ""),
            })
            results.append(r)
        export_to_json(conn)
        conn.close()
        new_ids = [r["expediente_id"] for r in results if r.get("is_new")]
        return self._json(200, {"ok": True, "inserted": len(new_ids), "expediente_ids": new_ids})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_form(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            return self._json(400, {"ok": False, "error": "Invalid JSON"})

        tipo = data.get("tipo", "otro")
        dominio = data.get("dominio", "")
        descripcion = data.get("descripcion", "")
        evidencia = data.get("evidencia", "")
        severidad = data.get("severidad", "high")

        if not descripcion:
            return self._json(400, {"ok": False, "error": "Descripción requerida"})

        mid = hashlib.md5(descripcion.encode()[:64]).hexdigest()[:8]
        eid = f"EV-2026-{int(time.time())}"
        now = datetime.now(timezone.utc).isoformat()

        conn = init_db()
        conn.execute(
            """INSERT INTO denuncias
            (expediente_id, tweet_id, username, display_name, text, category, severity,
             status, created_at, scraped_at, source_url, tags, resumen, evidencias)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, f"form-{int(time.time())}-{mid}", "anónimo", "Anónimo",
             descripcion, tipo, severidad, "draft",
             data.get("fecha") or now[:10], now,
             dominio or "formulario",
             json.dumps({"tipo": tipo, "dominio": dominio}, ensure_ascii=False),
             descripcion[:200], evidencia or ""),
        )
        conn.commit()
        conn.close()

        return self._json(200, {"ok": True, "expediente_id": eid})

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Ingest server on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
