#!/usr/bin/env python3
"""Review pending tweets — shows unclassified drafts for AI agent review."""
import json, sqlite3, sys
from pathlib import Path

DB = Path(__file__).parent / "data" / "denuncias.db"

def get_pending(limit=30):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, expediente_id, tweet_id, username, text, video_url, created_at, scraped_at "
        "FROM denuncias WHERE status = 'draft' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def classify(did, category, severity, resumen=None, status='published'):
    conn = sqlite3.connect(str(DB))
    conn.execute(
        "UPDATE denuncias SET category=?, severity=?, status=?, resumen=COALESCE(?, resumen) WHERE id=?",
        (category, severity, status, resumen, int(did))
    )
    conn.commit()
    conn.close()
    return True

def show(limit=30):
    pend = get_pending(limit)
    if not pend:
        print("No pending tweets.")
        return
    for t in pend:
        vid = "🎬" if t["video_url"] else "  "
        txt = (t["text"] or "")[:100].replace("\n", " ")
        print(f'{vid} #{t["id"]:>4} | @{t["username"]:<20} | {txt}')
        if t["video_url"]:
            print(f'     video: {t["video_url"][:80]}')
        print()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        show(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif len(sys.argv) > 2:
        did = sys.argv[2]
        cat = sys.argv[3] if len(sys.argv) > 3 else 'general'
        sev = sys.argv[4] if len(sys.argv) > 4 else 'info'
        res = sys.argv[5] if len(sys.argv) > 5 else None
        stat = sys.argv[6] if len(sys.argv) > 6 else 'published'
        classify(did, cat, sev, res, stat)
        print(f"#{did} → {cat}/{sev} [{stat}]")
    else:
        show()
