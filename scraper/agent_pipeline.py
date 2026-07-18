#!/usr/bin/env python3
"""Expediente Venezuela — Agent Pipeline v1.

Infinite scroll → capture → agent review → download.

The agent reads context (text, video, engagement, account) and decides
if a tweet is worth keeping as a denuncia.
"""
import asyncio
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from denuncias_db import init_db, insert_denuncia, update_denuncia, export_to_json, get_stats
from denuncias_scraper import log, load_x_cookies, _is_venezuela_related
from fast_engine import (
    _extract_tweets_from_page, _enrich_full_text, _has_video_track,
    download_video_ytdlp, VIDEOS_DIR, YT_DLP, log_engine
)

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
MIN_SCORE = 4          # Minimum agent score to keep a tweet
MAX_SCROLLS = 50       # Max scrolls per cycle (infinite until stale)
STALE_THRESHOLD = 5    # Consecutive scrolls with 0 new tweets → stop
ENRICH_BATCH = 10      # Enrich N tweets at a time before reviewing

# Known Venezuelan denuncia accounts (high trust)
KNOWN_ACCOUNTS = {
    'estebanoria', 'Fantasma_956', 'LuzMelyReyes', 'shelbykrisel',
    'VenezuelaLive', 'ReporteYa', 'InfoVenezuela', 'DDHH_Venezuela',
    'CoalicionDDHH', 'JusticiaVzla', 'TransparenciaVE', 'ElPitazo',
    'EfectoCocuyo', 'TalCualDigital', 'Runrunes', 'Prodavinci',
    'Noticias24', 'VenezuelaAnalitica', 'UltimasNoticias', 'ElUniversal',
    'CorrupcionVE', 'SinMordazaVE', 'AccesoALaJusticia', 'ForoPenal',
    'MonitorDC', 'Provea', 'Cecodhap', 'ObservatorioVZLA',
}


# ═══════════════════════════════════════════════════════════════════
# AGENT SCORING
# ═══════════════════════════════════════════════════════════════════
def agent_score(tweet: dict) -> dict:
    """Score a tweet on multiple dimensions. Returns {score, reasons, verdict}.
    
    Dimensions:
      - media_type: video=4, images=2, none=0
      - denuncia_signal: strong patterns in text
      - engagement: retweets + likes + replies
      - account_trust: known denuncia account
      - urgency: emergency/help language
      - specificity: location, names, details
    """
    text = (tweet.get('text') or '').lower()
    score = 0
    reasons = []

    # ── 1. MEDIA TYPE (0-4 pts) ──
    has_video = tweet.get('has_video', False)
    has_images = bool(tweet.get('images'))
    if has_video:
        score += 4
        reasons.append('video')
    elif has_images:
        score += 2
        reasons.append('images')

    # ── 2. DENUNCIA SIGNALS (0-3 pts) ──
    denuncia_strong = [
        'denuncia', 'abuso', 'corrupción', 'corrupcion', 'extorsión', 'extorsion',
        'desalojo', 'detención', 'detencion', 'tortura', 'desaparecido',
        'desaparecida', 'preso político', 'presa política', 'asesinato',
        'homicidio', 'femicidio', 'sicariato', 'trata', 'violación',
        'represión', 'represion', 'censura', 'persecución', 'persecucion',
    ]
    denuncia_moderate = [
        'robo', 'hurto', 'amenaza', 'injusticia', 'abuso de poder',
        'sin luz', 'sin agua', 'apagón', 'apagon', 'colapso',
        'hospital', 'médico', 'medico', 'medicamento', 'bono',
        'discapacidad', 'enfermedad', 'tratamiento',
        'bloqueo', 'escasez', 'racionamiento',
        'militar', 'policial', 'policía', 'policia', 'gnb', 'pnb',
        'colectivos', 'arresto', 'captura',
    ]
    strong_hits = sum(1 for kw in denuncia_strong if kw in text)
    moderate_hits = sum(1 for kw in denuncia_moderate if kw in text)
    if strong_hits >= 2:
        score += 3
        reasons.append(f'denuncia×{strong_hits}')
    elif strong_hits == 1:
        score += 2
        reasons.append('denuncia')
    elif moderate_hits >= 2:
        score += 1
        reasons.append(f'denuncia-weak×{moderate_hits}')

    # ── 3. ENGAGEMENT (0-2 pts) ──
    retweets = tweet.get('retweets', 0)
    likes = tweet.get('likes', 0)
    replies = tweet.get('replies', 0)
    engagement = retweets + likes + replies
    if engagement > 500:
        score += 2
        reasons.append(f'high-engagement({engagement})')
    elif engagement > 50:
        score += 1
        reasons.append(f'mid-engagement({engagement})')

    # ── 4. ACCOUNT TRUST (0-2 pts) ──
    username = (tweet.get('username') or '').lower()
    if username in KNOWN_ACCOUNTS:
        score += 2
        reasons.append('trusted-account')
    elif any(kw in username for kw in ['ddhh', 'ddh', 'justicia', 'dderechos', 'ddhh', 'coalicion']):
        score += 1
        reasons.append('human-rights-account')

    # ── 5. URGENCY (0-2 pts) ──
    urgency_terms = [
        'urgente', 'auxilio', 'socorro', 'ayuda', 'emergencia',
        'difundir', 'rt', 'favor', 'compartir', 'atención',
        'última hora', 'ultima hora', 'breaking', 'just in',
    ]
    urgency_hits = sum(1 for kw in urgency_terms if kw in text)
    if urgency_hits >= 2:
        score += 2
        reasons.append(f'urgent×{urgency_hits}')
    elif urgency_hits == 1:
        score += 1
        reasons.append('urgent')

    # ── 6. SPECIFICITY (0-2 pts) ──
    # Location mentions
    locations = [
        'caracas', 'maracaibo', 'valencia', 'barquisimeto', 'maracay',
        'ciudad guayana', 'puerto ordaz', 'mérida', 'merida', 'táchira', 'tachira',
        'zulia', 'aragua', 'bolívar', 'bolivar', 'miranda', 'vargas',
        'la guaira', 'guaira', 'catia', 'san bernardino', 'petare',
        'los teques', 'puerto la cruz', 'barcelona', 'cumaná', 'cumana',
        'san cristóbal', 'san cristobal', 'punto fijo', 'barinas',
    ]
    loc_hits = sum(1 for loc in locations if loc in text)
    if loc_hits >= 2:
        score += 2
        reasons.append(f'specific-location×{loc_hits}')
    elif loc_hits == 1:
        score += 1
        reasons.append('location')

    # Named victims or specific details
    if re.search(r'[A-Z][a-z]+ [A-Z][a-z]+', tweet.get('text', '')):
        score += 1
        reasons.append('named-person')

    # ── HARD REJECT: non-Venezuela ──
    if not _is_venezuela_related(tweet.get('text', '')):
        return {'score': 0, 'reasons': ['not-venezuela'], 'verdict': 'reject'}

    # ── HARD REJECT: too short ──
    if len(text) < 30:
        return {'score': 0, 'reasons': ['too-short'], 'verdict': 'reject'}

    # ── VERDICT ──
    verdict = 'keep' if score >= MIN_SCORE else 'reject'
    return {'score': score, 'reasons': reasons, 'verdict': verdict}


# ═══════════════════════════════════════════════════════════════════
# INFINITE SCROLL
# ═══════════════════════════════════════════════════════════════════
async def infinite_scroll(max_scrolls=MAX_SCROLLS, stale_threshold=STALE_THRESHOLD):
    """Scroll timeline until exhausted or stale. Returns all captured tweets."""
    from playwright.async_api import async_playwright

    pw_cookies = load_x_cookies()
    if not pw_cookies:
        log_engine("No X cookies!")
        return []

    all_tweets = []
    seen_ids = set()
    stale_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/128.0",
            viewport={"width": 1280, "height": 900},
            locale="es-VE",
        )
        await ctx.add_cookies(pw_cookies)
        page = await ctx.new_page()

        log_engine("Opening X timeline (infinite scroll)...")
        try:
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
        except Exception as e:
            log_engine(f"Error loading timeline: {e}")
            await browser.close()
            return []

        if "login" in page.url.lower():
            log_engine("Not logged in!")
            await browser.close()
            return []

        try:
            await page.wait_for_selector("article[data-testid='tweet']", timeout=10000)
        except:
            log_engine("No tweets found, trying scroll anyway...")

        log_engine("Timeline loaded. Scrolling infinitely...")

        for scroll_num in range(max_scrolls):
            try:
                tweets = await _extract_tweets_from_page(page)

                new_count = 0
                for t in tweets:
                    if t["id"] in seen_ids:
                        continue
                    seen_ids.add(t["id"])

                    # Collect ALL tweets with media (not just Venezuela — agent will filter)
                    has_video = t.get("has_video", False)
                    has_images = bool(t.get("images"))

                    if has_video or has_images:
                        all_tweets.append(t)
                        new_count += 1

                if new_count == 0:
                    stale_count += 1
                    if stale_count >= stale_threshold:
                        log_engine(f"  Stale after {scroll_num + 1} scrolls ({stale_count} consecutive empty). Stopping.")
                        break
                else:
                    stale_count = 0

                log_engine(f"  Scroll {scroll_num + 1}/{max_scrolls}: {new_count} new media tweets (total: {len(all_tweets)}, stale: {stale_count}/{stale_threshold})")

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(2.5, 4.5))
            except Exception as e:
                log_engine(f"  Scroll error: {e}")
                continue

        # Enrich: visit each tweet individually to get full text + video
        if all_tweets:
            log_engine(f"Enriching {len(all_tweets)} tweets with full text...")
            await _enrich_full_text(page, all_tweets)

        await browser.close()

    log_engine(f"Infinite scroll complete: {len(all_tweets)} tweets captured")
    return all_tweets


# ═══════════════════════════════════════════════════════════════════
# AGENT REVIEW — Score and filter tweets
# ═══════════════════════════════════════════════════════════════════
def agent_review(tweets: list) -> dict:
    """Review all tweets with the agent. Returns {kept, rejected, stats}."""
    kept = []
    rejected = []

    for t in tweets:
        result = agent_score(t)
        t['_agent_score'] = result['score']
        t['_agent_reasons'] = result['reasons']
        t['_agent_verdict'] = result['verdict']

        if result['verdict'] == 'keep':
            kept.append(t)
        else:
            rejected.append(t)

    # Log summary
    score_dist = {}
    for t in tweets:
        s = t['_agent_score']
        score_dist[s] = score_dist.get(s, 0) + 1

    log_engine(f"Agent review: {len(kept)} kept, {len(rejected)} rejected")
    log_engine(f"  Score distribution: {dict(sorted(score_dist.items()))}")

    # Log top rejected reasons
    reject_reasons = {}
    for t in rejected:
        for r in t['_agent_reasons']:
            reject_reasons[r] = reject_reasons.get(r, 0) + 1
    if reject_reasons:
        top_reject = sorted(reject_reasons.items(), key=lambda x: -x[1])[:5]
        log_engine(f"  Top reject reasons: {top_reject}")

    return {
        'kept': kept,
        'rejected': rejected,
        'total': len(tweets),
        'kept_count': len(kept),
        'rejected_count': len(rejected),
    }


# ═══════════════════════════════════════════════════════════════════
# SAVE + DOWNLOAD
# ═══════════════════════════════════════════════════════════════════
def save_and_download(kept_tweets: list) -> dict:
    """Save kept tweets to DB and download videos."""
    conn = init_db()
    inserted = 0
    merged = 0
    skipped = 0
    downloaded = 0

    for t in kept_tweets:
        # Skip if already in DB
        existing = conn.execute(
            "SELECT expediente_id FROM denuncias WHERE tweet_id = ?", (t["id"],)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        result = insert_denuncia(conn, {
            "tweet_id": t["id"],
            "username": t["username"],
            "display_name": t.get("name", t["username"]),
            "text": t["text"],
            "source_url": t["url"],
            "video_url": None,  # Download later
            "images": t.get("images", []),
            "retweets": t.get("retweets", 0),
            "likes": t.get("likes", 0),
            "replies": t.get("replies", 0),
            "created_at": t.get("created_at", ""),
        })

        if result.get("is_new"):
            inserted += 1
            exp_id = result["expediente_id"]

            # Download video if tweet has one
            if t.get("has_video") and t.get("url"):
                log_engine(f"  Downloading video for {exp_id}...")
                if download_video_ytdlp(exp_id, t["url"]):
                    update_denuncia(conn, exp_id, {'video_url': f'media/videos/{exp_id}.mp4'})
                    downloaded += 1
        elif result.get("merged_into"):
            merged += 1
        else:
            skipped += 1

    # Auto-publish all with media
    from denuncias_db import list_denuncias, publish_denuncia
    drafts = list_denuncias(conn, status='draft', limit=200)
    published = 0
    for d in drafts:
        has_video = bool(d.get('video_url') and d['video_url'] != '')
        has_images = bool(d.get('images') and d['images'] != '[]')
        if has_video or has_images:
            if publish_denuncia(conn, d['expediente_id']):
                published += 1

    export_to_json(conn)
    conn.close()

    return {
        'inserted': inserted,
        'merged': merged,
        'skipped': skipped,
        'downloaded': downloaded,
        'published': published,
    }


# ═══════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════
def run_pipeline(max_scrolls=MAX_SCROLLS):
    """Run the full agent pipeline: scroll → review → download → DB."""
    log_engine("=" * 50)
    log_engine("  AGENT PIPELINE — Starting")
    log_engine("=" * 50)

    # 1. Infinite scroll
    tweets = asyncio.run(infinite_scroll(max_scrolls=max_scrolls))
    log_engine(f"Captured: {len(tweets)} tweets with media")

    if not tweets:
        log_engine("No tweets captured. Pipeline complete.")
        return {'captured': 0, 'kept': 0, 'downloaded': 0}

    # 2. Agent review
    review = agent_review(tweets)
    kept = review['kept']

    if not kept:
        log_engine("No tweets passed agent review. Pipeline complete.")
        return {'captured': len(tweets), 'kept': 0, 'downloaded': 0}

    # 3. Save + download
    log_engine(f"Saving {len(kept)} kept tweets to DB...")
    save_result = save_and_download(kept)

    # 4. Stats
    conn = init_db()
    stats = get_stats(conn)
    conn.close()

    video_count = len(list(VIDEOS_DIR.glob('*.mp4'))) if VIDEOS_DIR.exists() else 0

    result = {
        'captured': len(tweets),
        'kept': len(kept),
        'rejected': review['rejected_count'],
        'inserted': save_result['inserted'],
        'merged': save_result['merged'],
        'downloaded': save_result['downloaded'],
        'published': save_result['published'],
        'total_in_db': stats.get('total', 0),
        'videos_on_disk': video_count,
    }

    log_engine(f"Pipeline complete: {result}")
    log_engine("=" * 50)

    return result


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Expediente Venezuela Agent Pipeline")
    parser.add_argument('--scrolls', type=int, default=MAX_SCROLLS, help='Max scrolls (default: 50)')
    parser.add_argument('--min-score', type=int, default=MIN_SCORE, help='Min agent score (default: 4)')
    args = parser.parse_args()

    MIN_SCORE = args.min_score
    result = run_pipeline(max_scrolls=args.scrolls)
    print(json.dumps(result, indent=2))
