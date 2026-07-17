#!/usr/bin/env python3
"""Autonomous Denuncias Loop — runs continuously, scrapes, merges, downloads, publishes."""
import asyncio
import json
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from denuncias_db import init_db, get_stats, list_denuncias, publish_denuncia, export_to_json, update_denuncia
from denuncias_scraper import search_denuncias, backfill_media, log
from auto_dossier import process_pending as auto_generate_dossiers

# Config
CYCLE_INTERVAL = 1800  # 30 minutes between cycles
MAX_PER_CYCLE = 40     # Max new denuncias per cycle
AUTO_PUBLISH = True    # Auto-publish after scraping

# Expanded queries — Venezuela-focused denuncias
QUERIES = [
    # Core denuncia queries
    'denuncia venezuela video',
    'venezuela abuso policia',
    'venezuela corrupcion denuncia',
    'venezuela extorsion',
    'venezuela desalojo',
    'venezuela reten militar',
    'venezuela detencion arbitraria',
    'venezuela persecucion politica',
    'venezuela censura',
    'venezuela servicios publicos',
    'venezuela blackout',
    'venezuela agua luz',
    'venezuela hospital',
    'venezuela escuela',
    'venezuela sicariato',
    'venezuela motopirateria',
    'venezuela robo armas',
    'venezuela colectivos armados',
    'venezuela FANB abuso',
    'venezuela gobernador corrupcion',
    'venezuela alcalde',
    'venezuela diputado',
    'venezuela ministro',
    'venezuela denuncia ciudadana',
    'venezuela DDHH',
    'venezuela desaparecido',
    'venezuela tortura',
    'venezuela preso politico',
    'venezuela exilio',
    'venezuela migracion',
    # Account-focused
    '@ABOREDE denuncia',
    '@ElPitazoTV',
    '@NoticiasVzla',
    '@CaraotaDigital denuncia',
    '@ReporteYa venezuela',
    # Hashtag-focused
    '#DenunciaVenezuela',
    '#VenezuelaLibre',
    '#JusticiaVenezuela',
    '#DDHH',
    '#CorrupcionVenezuela',
    # Trending topics
    'opinion publica venezuela',
    'crisis venezuela',
    'emergencia humanitaria',
    'migracion venezuela',
]

LOG_FILE = Path(__file__).parent / "data" / "loop.log"

running = True

def signal_handler(sig, frame):
    global running
    log("Shutdown signal received, finishing current cycle...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def log_loop(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def auto_publish_new():
    """Auto-publish all draft denuncias."""
    conn = init_db()
    drafts = list_denuncias(conn, status='draft', limit=100)
    published = 0
    for d in drafts:
        result = publish_denuncia(conn, d['expediente_id'])
        if result:
            published += 1
    if published:
        export_to_json(conn)
    conn.close()
    return published

def run_cycle():
    """Execute one full scrape cycle."""
    log_loop("=== Starting cycle ===")
    
    # 1. Scrape
    random.shuffle(QUERIES)
    cycle_queries = QUERIES[:5]  # 5 queries per cycle
    
    log_loop(f"Scraping {len(cycle_queries)} queries...")
    result = search_denuncias(queries=cycle_queries, max_results=MAX_PER_CYCLE)
    
    new_count = len(result.get('new_denuncias', []))
    merged_count = result.get('merged', 0)
    skipped_count = result.get('skipped', 0)
    
    log_loop(f"Scrape: {new_count} new, {merged_count} merged, {skipped_count} skipped")
    
    # 2. Backfill media for new denuncias
    if new_count > 0:
        log_loop("Backfilling media...")
        backfill_result = backfill_media(max_denuncias=min(new_count + 5, 20))
        log_loop(f"Backfill: {backfill_result.get('updated', 0)} updated")
    
    # 2b. Download captured videos
    log_loop("Downloading videos...")
    try:
        from download_videos import capture_and_download
        asyncio.run(capture_and_download(max_videos=20))
    except Exception as e:
        log_loop(f"Download error: {e}")
    
    # 3. Auto-generate dossiers for denuncias without content
    try:
        log_loop("Generating dossiers...")
        auto_generate_dossiers()
    except Exception as e:
        log_loop(f"Dossier generation error: {e}")
    
    # 4. Auto-publish
    if AUTO_PUBLISH:
        published = auto_publish_new()
        if published:
            log_loop(f"Published {published} denuncias")
    
    # 5. Stats
    conn = init_db()
    stats = get_stats(conn)
    conn.close()
    
    log_loop(f"Stats: {stats.get('total', 0)} total, {stats.get('published_count', 0)} published, {stats.get('draft_count', 0)} drafts")
    log_loop("=== Cycle complete ===")
    
    return {
        'new': new_count,
        'merged': merged_count,
        'published': published if AUTO_PUBLISH else 0,
        'stats': stats
    }

def main():
    global running
    
    log_loop("========================================")
    log_loop("  EXPEDIENTE VENEZUELA - LOOP AUTÓNOMO  ")
    log_loop("========================================")
    log_loop(f"Interval: {CYCLE_INTERVAL}s ({CYCLE_INTERVAL//60} min)")
    log_loop(f"Queries: {len(QUERIES)} total")
    log_loop(f"Auto-publish: {AUTO_PUBLISH}")
    log_loop("")
    
    cycle_count = 0
    total_new = 0
    total_merged = 0
    
    while running:
        try:
            result = run_cycle()
            cycle_count += 1
            total_new += result.get('new', 0)
            total_merged += result.get('merged', 0)
            
            log_loop(f"Session: {cycle_count} cycles, {total_new} new, {total_merged} merged")
        except Exception as e:
            log_loop(f"ERROR: {e}")
        
        if not running:
            break
            
        log_loop(f"Sleeping {CYCLE_INTERVAL}s until next cycle...")
        # Sleep in small chunks to allow signal handling
        for _ in range(CYCLE_INTERVAL):
            if not running:
                break
            time.sleep(1)
    
    log_loop("Loop stopped.")

if __name__ == "__main__":
    main()
