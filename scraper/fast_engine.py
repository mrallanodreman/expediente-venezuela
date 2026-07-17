#!/usr/bin/env python3
"""Expediente Venezuela — Timeline Engine v2.

Scrapes YOUR X/Twitter timeline for denuncias with video/images.
Uses yt-dlp for reliable video download from tweet URLs.
"""
import asyncio
import json
import random
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from denuncias_db import init_db, get_stats, list_denuncias, publish_denuncia, export_to_json, update_denuncia
from denuncias_scraper import log, load_x_cookies

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
CYCLE_INTERVAL = 1800  # 30 minutes
VIDEOS_DIR = Path('/mnt/sdb1/MoneyMakers_webops/Web1/expediente-venezuela/media/videos')
LOG_FILE = Path(__file__).parent / "data" / "engine.log"

running = True
# Don't handle SIGTERM - let the engine keep running
# Only handle SIGINT (Ctrl+C) for graceful shutdown
signal.signal(signal.SIGINT, lambda s, f: None)

def log_engine(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ═══════════════════════════════════════════════════════════════════
# TIMELINE SCRAPER — Scroll your home feed
# ═══════════════════════════════════════════════════════════════════
async def scrape_timeline(scroll_count=15):
    """Scroll your X timeline and extract tweets with video/images."""
    from playwright.async_api import async_playwright

    pw_cookies = load_x_cookies()
    if not pw_cookies:
        log_engine("No X cookies!")
        return []

    all_tweets = []
    seen_ids = set()

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

        # Go to home timeline
        log_engine("Opening X timeline...")
        try:
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
        except Exception as e:
            log_engine(f"Error loading timeline: {e}")
            await browser.close()
            return []

        # Check login
        if "login" in page.url.lower():
            log_engine("Not logged in!")
            await browser.close()
            return []

        # Wait for tweets
        try:
            await page.wait_for_selector("article[data-testid='tweet']", timeout=10000)
        except:
            log_engine("No tweets found, trying scroll anyway...")

        log_engine("Timeline loaded. Scrolling...")

        # Scroll and extract
        for scroll_num in range(scroll_count):
            try:
                tweets = await _extract_tweets_from_page(page)

                new_count = 0
                for t in tweets:
                    if t["id"] in seen_ids:
                        continue
                    seen_ids.add(t["id"])

                    has_video = t.get("has_video", False)
                    has_images = bool(t.get("images"))

                    if has_video or has_images:
                        all_tweets.append(t)
                        new_count += 1

                log_engine(f"  Scroll {scroll_num + 1}/{scroll_count}: {new_count} new media tweets")

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(2.5, 4))
            except Exception as e:
                log_engine(f"  Scroll error: {e}")
                continue

        # Enrich: visit each tweet individually to get full text
        if all_tweets:
            log_engine(f"Enriching {len(all_tweets)} tweets with full text...")
            await _enrich_full_text(page, all_tweets)

        await browser.close()

    log_engine(f"Timeline scrape complete: {len(all_tweets)} tweets with media")
    return all_tweets


async def _extract_tweets_from_page(page):
    """Extract tweets from the currently loaded page."""
    tweets = []
    articles = await page.query_selector_all("article[data-testid='tweet']")

    for art in articles:
        try:
            link_el = await art.query_selector("a[href*='/status/']")
            if not link_el:
                continue
            href = await link_el.get_attribute("href")
            if not href or "/status/" not in href:
                continue

            parts = href.strip("/").split("/")
            if len(parts) < 3:
                continue
            username = parts[0]
            tweet_id = parts[2]

            text_el = await art.query_selector("[data-testid='tweetText']")
            text = ""
            if text_el:
                text = (await text_el.inner_text()).strip()

            name_el = await art.query_selector("[data-testid='User-Name']")
            display_name = username
            if name_el:
                name_text = await name_el.inner_text()
                if name_text:
                    display_name = name_text.split("\n")[0].strip()

            # Check for video
            play_btn = await art.query_selector("[data-testid='videoPlayer']")
            vid_container = await art.query_selector("[aria-label*='video' i]")
            video_el = await art.query_selector("video")
            has_video = play_btn is not None or vid_container is not None or video_el is not None

            # Extract images
            images = []
            img_els = await art.query_selector_all("img[src*='pbs.twimg.com']")
            for img in img_els[:4]:
                src = await img.get_attribute("src")
                if src:
                    high = src.replace("name=small", "name=large").replace("name=medium", "name=large")
                    if "name=" not in high:
                        high += "&name=large"
                    images.append(high)

            if not images:
                ton_imgs = await art.query_selector_all("img[src*='ton.twimg.com']")
                for img in ton_imgs[:4]:
                    src = await img.get_attribute("src")
                    if src:
                        images.append(src)

            # Stats
            stats = {}
            for sel in ["retweetCount", "favoriteCount", "replyCount"]:
                el = await art.query_selector(f"[data-testid='{sel}']")
                if el:
                    val = (await el.inner_text()).strip().replace(",", "").replace(".", "")
                    try:
                        stats[sel.replace("Count", "").lower()] = int(val) if val else 0
                    except ValueError:
                        pass

            tweet = {
                "id": tweet_id,
                "username": username,
                "name": display_name,
                "url": f"https://x.com{href}",
                "text": text,
                "created_at": "",
                "has_video": has_video,
                "images": images,
                "retweets": stats.get("retweet", 0),
                "likes": stats.get("favorite", 0),
                "replies": stats.get("reply", 0),
            }
            tweets.append(tweet)

        except Exception:
            continue

    return tweets


async def _enrich_full_text(page, tweets):
    """Visit each tweet individually to get the full (non-truncated) text."""
    for i, t in enumerate(tweets):
        tweet_url = t.get("url", "")
        if not tweet_url:
            continue

        try:
            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Extract full text from individual tweet page
            text_el = await page.query_selector("[data-testid='tweetText']")
            if text_el:
                full_text = (await text_el.inner_text()).strip()
                if len(full_text) > len(t.get("text", "")):
                    t["text"] = full_text

            # Go back to timeline
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Re-scroll to continue from where we left off
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

            if (i + 1) % 5 == 0:
                log_engine(f"  Enriched {i + 1}/{len(tweets)} tweets")

        except Exception as e:
            log_engine(f"  Enrich error for {tweet_url}: {e}")
            # Try to go back to timeline
            try:
                await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            except:
                pass


# ═══════════════════════════════════════════════════════════════════
# VIDEO DOWNLOAD — yt-dlp (much more reliable)
# ═══════════════════════════════════════════════════════════════════
def download_video_ytdlp(exp_id, tweet_url):
    """Download video from tweet URL using yt-dlp. Returns True if successful."""
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    local_file = VIDEOS_DIR / f"{exp_id}.mp4"

    if local_file.exists() and local_file.stat().st_size > 1000:
        return True

    try:
        # yt-dlp with cookies from Ferdium
        cookie_file = _get_cookie_file()
        cmd = [
            'yt-dlp',
            '--no-warnings',
            '-f', 'best[ext=mp4]/best',
            '-o', str(local_file),
            '--no-playlist',
        ]
        if cookie_file:
            cmd.extend(['--cookies', cookie_file])
        cmd.append(tweet_url)

        result = subprocess.run(cmd, capture_output=True, timeout=120)

        if result.returncode == 0 and local_file.exists() and local_file.stat().st_size > 1000:
            size_kb = local_file.stat().st_size / 1024
            log_engine(f"    Downloaded {exp_id}: {size_kb:.0f} KB")
            return True
        else:
            if local_file.exists():
                local_file.unlink()
            return False
    except Exception as e:
        if local_file.exists():
            local_file.unlink()
        return False


def _get_cookie_file():
    """Convert Ferdium cookies to Netscape cookie file for yt-dlp."""
    from denuncias_scraper import FERDIUM_SNAPSHOTS_DIR

    cookie_file = Path('/tmp/x_cookies.txt')
    candidates = [
        FERDIUM_SNAPSHOTS_DIR / "service-0fe1114b-1587-4983-9284-5c4d63eced08.latest.json",
    ]

    for snap_path in candidates:
        if not snap_path.exists():
            continue
        try:
            with open(snap_path) as f:
                snap = json.load(f)
            cookies = snap.get("cookies", [])
            x_cookies = [c for c in cookies if "x.com" in c.get("domain", "") or "twitter.com" in c.get("domain", "")]
            if not x_cookies:
                continue

            # Write Netscape format
            with open(cookie_file, "w") as f:
                f.write("# Netscape HTTP Cookie File\n")
                for c in x_cookies:
                    domain = c.get("domain", ".x.com")
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", True) else "FALSE"
                    expires = str(int(c.get("expires", 0)))
                    name = c["name"]
                    value = c["value"]
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

            return str(cookie_file)
        except Exception:
            continue

    return None


def download_all_pending():
    """Download all videos with HTTP URLs using yt-dlp."""
    conn = init_db()
    rows = conn.execute('''
        SELECT expediente_id, source_url, video_url
        FROM denuncias 
        WHERE video_url LIKE 'http%'
        AND video_url NOT LIKE 'blob:%'
        AND video_url NOT LIKE 'media/%'
        ORDER BY id DESC
    ''').fetchall()
    conn.close()

    if not rows:
        return 0

    downloaded = 0
    for exp_id, source_url, video_url in rows:
        # Use source_url (tweet URL) for yt-dlp, not the video URL
        tweet_url = source_url if source_url else video_url
        if not tweet_url:
            continue

        if download_video_ytdlp(exp_id, tweet_url):
            conn = init_db()
            update_denuncia(conn, exp_id, {'video_url': f'media/videos/{exp_id}.mp4'})
            export_to_json(conn)
            conn.close()
            downloaded += 1

    return downloaded


# ═══════════════════════════════════════════════════════════════════
# SAVE TO DB
# ═══════════════════════════════════════════════════════════════════
def save_tweets_to_db(tweets):
    """Save tweets to DB. Only saves those with media AND Venezuela-related."""
    from denuncias_db import insert_denuncia
    from denuncias_scraper import _is_venezuela_related

    conn = init_db()
    inserted = 0
    merged = 0
    skipped = 0
    filtered = 0

    for t in tweets:
        has_video = t.get("has_video", False)
        has_images = bool(t.get("images"))

        if not has_video and not has_images:
            skipped += 1
            continue

        # Filter non-Venezuela content
        if not _is_venezuela_related(t.get("text", "")):
            filtered += 1
            continue

        result = insert_denuncia(conn, {
            "tweet_id": t["id"],
            "username": t["username"],
            "display_name": t.get("name", t["username"]),
            "text": t["text"],
            "source_url": t["url"],
            "video_url": None,  # Will be filled by yt-dlp download
            "images": t.get("images", []),
            "retweets": t.get("retweets", 0),
            "likes": t.get("likes", 0),
            "replies": t.get("replies", 0),
            "created_at": t.get("created_at", ""),
        })

        if result == "inserted":
            inserted += 1
        elif result == "merged":
            merged += 1
        else:
            skipped += 1

    export_to_json(conn)
    conn.close()

    return {"inserted": inserted, "merged": merged, "skipped": skipped, "filtered": filtered}


# ═══════════════════════════════════════════════════════════════════
# AUTO-PUBLISH
# ═══════════════════════════════════════════════════════════════════
def auto_publish():
    """Auto-publish all draft denuncias with media."""
    conn = init_db()
    drafts = list_denuncias(conn, status='draft', limit=100)
    published = 0
    for d in drafts:
        has_video = bool(d.get('video_url') and d['video_url'] != '')
        has_images = bool(d.get('images') and d['images'] != '[]')
        if has_video or has_images:
            result = publish_denuncia(conn, d['expediente_id'])
            if result:
                published += 1
    if published:
        export_to_json(conn)
    conn.close()
    return published


# ═══════════════════════════════════════════════════════════════════
# CYCLE
# ═══════════════════════════════════════════════════════════════════
def run_cycle():
    """Execute one full cycle: scroll timeline → save → download → publish."""
    log_engine("=== Starting cycle ===")

    # 1. Scroll timeline
    tweets = asyncio.run(scrape_timeline(scroll_count=15))
    log_engine(f"Scraped {len(tweets)} tweets with media from timeline")

    # 2. Save to DB
    if tweets:
        save_result = save_tweets_to_db(tweets)
        log_engine(f"DB: {save_result['inserted']} new, {save_result['merged']} merged, {save_result['skipped']} skipped, {save_result.get('filtered', 0)} filtered")
    else:
        log_engine("No media tweets found in timeline")

    # 3. Download pending videos (yt-dlp)
    log_engine("Downloading pending videos...")
    downloaded = download_all_pending()
    log_engine(f"Downloaded: {downloaded} videos")

    # 4. Auto-publish
    published = auto_publish()
    if published:
        log_engine(f"Published: {published} denuncias")

    # 5. Stats
    conn = init_db()
    stats = get_stats(conn)
    conn.close()

    video_count = len(list(VIDEOS_DIR.glob('*.mp4'))) if VIDEOS_DIR.exists() else 0

    log_engine(f"Stats: {stats.get('total', 0)} total, {stats.get('published', 0)} published, {video_count} videos on disk")
    log_engine("=== Cycle complete ===")

    return {
        "tweets_scraped": len(tweets),
        "inserted": save_result.get("inserted", 0) if tweets else 0,
        "downloaded": downloaded,
        "published": published,
        "videos_on_disk": video_count,
    }


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    global running

    log_engine("========================================")
    log_engine("  EXPEDIENTE VENEZUELA - TIMELINE ENGINE ")
    log_engine("========================================")
    log_engine(f"Interval: {CYCLE_INTERVAL}s")
    log_engine("")

    while running:
        try:
            result = run_cycle()
        except Exception as e:
            log_engine(f"ERROR: {e}")

        if not running:
            break

        log_engine(f"Sleeping {CYCLE_INTERVAL}s...")
        for _ in range(CYCLE_INTERVAL):
            if not running:
                break
            time.sleep(1)

    log_engine("Engine stopped.")


if __name__ == "__main__":
    main()
