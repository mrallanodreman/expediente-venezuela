#!/usr/bin/env python3
"""
sync_kg.py: Push published denuncias to Knowledge Graph + Dossier notes.
Run via systemd timer every 15min or on-demand.
Usage: python3 sync_kg.py [--limit 100]
"""
import sqlite3, json, os, sys, subprocess, argparse
from pathlib import Path
from datetime import datetime

DB = Path(__file__).parent / "data" / "denuncias.db"
KG_ADD = "/home/pctorre/.opencode/bin/kg-add"
KG_PATH = "/home/pctorre/.understand-anything/knowledge-graph.json"
DOSSIER_NOTES = "/mnt/sdb1/FkSociety/CuRioso/findings"

SEV_MAP = {
    "critical": "CRÍTICO",
    "high": "ALTO",
    "medium": "ALTO",
    "info": "INFO",
}

def ensure_columns():
    conn = sqlite3.connect(str(DB))
    try:
        conn.execute("ALTER TABLE denuncias ADD COLUMN kg_synced INTEGER DEFAULT 0")
    except:
        pass
    try:
        conn.execute("ALTER TABLE denuncias ADD COLUMN dossier_synced INTEGER DEFAULT 0")
    except:
        pass
    conn.close()

def get_unsynced_kg(limit=100):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, expediente_id, tweet_id, username, display_name, text, "
        "category, severity, resumen, video_url, images, created_at "
        "FROM denuncias WHERE status='published' AND (kg_synced IS NULL OR kg_synced=0) "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_unsynced_dossier(limit=20):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, username, text, category, severity, resumen "
        "FROM denuncias WHERE status='published' AND severity IN ('critical','high') "
        "AND (dossier_synced IS NULL OR dossier_synced=0) "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_synced(table, ids):
    conn = sqlite3.connect(str(DB))
    conn.execute(f"UPDATE denuncias SET {table}=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
    conn.commit()
    conn.close()

def node_exists(label):
    try:
        r = subprocess.run(
            ["python3", "-c", f"""
import json
d = json.load(open('{KG_PATH}'))
print('EXISTS' if any(n['label']=='{label}' for n in d['nodes']) else '')
"""], capture_output=True, text=True, timeout=10)
        return 'EXISTS' in r.stdout
    except:
        return False

def kg_add_finding(d):
    label = f"DENUNCIA-{d['id']}"
    if node_exists(label):
        return False
    sev = SEV_MAP.get(d.get('severity','info'),'INFO')
    resumen = (d.get('resumen') or d.get('text') or '')[:120].replace('"',"'")
    username = d.get('username','')
    cmd = [KG_ADD, "--label", label, "--type", "finding", "--severity", sev, "--title", resumen]
    if username:
        cmd += ["--edge", f"{label} -> @{username}:publica"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return 'KG actualizado' in (r.stdout + r.stderr)

def add_person(username):
    if not username or node_exists(f"@{username}"):
        return
    subprocess.run([KG_ADD, "--label", f"@{username}", "--type", "person", "--severity", "info"],
                   capture_output=True, timeout=30)

def dossier_add(d):
    today = datetime.now().strftime("%Y-%m-%d")
    finding_id = f"X-{d['id']:04d}"
    sev = d.get('severity','info').upper()
    title = (d.get('resumen') or d.get('text') or '')[:100]
    body = f"Fuente: @{d['username']}\n{d.get('text','')[:500]}"
    note = {
        "finding_id": finding_id,
        "title": title,
        "severity": sev,
        "body": body,
        "date": today,
        "source": "x-scraper"
    }
    os.makedirs(DOSSIER_NOTES, exist_ok=True)
    path = os.path.join(DOSSIER_NOTES, f"x-{d['id']:04d}.json")
    with open(path, "w") as f:
        json.dump(note, f, indent=2, ensure_ascii=False)
    return True

def main():
    ensure_columns()
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=100)
    args = p.parse_args()

    # Sync to KG
    unsynced = get_unsynced_kg(args.limit)
    synced_kg = []
    for d in unsynced:
        if kg_add_finding(d):
            add_person(d['username'])
            synced_kg.append(d['id'])
    if synced_kg:
        mark_synced("kg_synced", synced_kg)
    print(f"KG: {len(synced_kg)} synced")

    # Sync to Dossier
    unsynced_d = get_unsynced_dossier(20)
    synced_d = []
    for d in unsynced_d:
        dossier_add(d)
        synced_d.append(d['id'])
    if synced_d:
        mark_synced("dossier_synced", synced_d)
    print(f"Dossier: {len(synced_d)} synced")

if __name__ == "__main__":
    main()
