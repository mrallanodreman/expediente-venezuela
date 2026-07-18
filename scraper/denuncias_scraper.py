#!/usr/bin/env python3
"""X/Twitter Denuncias Scraper — v4 (Playwright + SQLite).

Usage as CLI:  python3 denuncias_scraper.py
Usage as import: from denuncias_scraper import run_scrape, search_denuncias
"""
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from urllib.parse import quote

try:
    from .denuncias_db import init_db, insert_denuncia, export_to_json, _topic_fingerprint, update_denuncia
except ImportError:
    from denuncias_db import init_db, insert_denuncia, export_to_json, _topic_fingerprint, update_denuncia

# --- Config ---
FERDIUM_SNAPSHOTS_DIR = Path.home() / ".ferdium-inject/snapshots"
DATA_DIR = Path(__file__).parent / "data"
LOG_FILE = DATA_DIR / "scraper.log"
SEEN_IDS_FILE = DATA_DIR / "seen_ids.json"

DEFAULT_SEARCH_QUERIES = [
    "denuncia venezuela",
    "denuncia gente abuso",
    "corrupcion venezuela",
    "injusticia venezuela",
    "extorsion venezuela",
    "maltrato policia",
    "desalojo ilegal",
    "reten militar venezuela",
    "abuso poder denuncia",
    "venezuela denuncia video",
]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_x_cookies():
    """Load X cookies from Ferdium snapshot -> Playwright format."""
    candidates = [
        FERDIUM_SNAPSHOTS_DIR / "service-0fe1114b-1587-4983-9284-5c4d63eced08.latest.json",
        FERDIUM_SNAPSHOTS_DIR / "general-session.latest.json",
    ]
    for snap_path in candidates:
        if not snap_path.exists():
            continue
        try:
            with open(snap_path) as f:
                snap = json.load(f)
            cookies = snap.get("cookies", [])
            x_cookies = [c for c in cookies if "x.com" in c.get("domain", "") or "twitter.com" in c.get("domain", "")]
            if x_cookies:
                log(f"Loaded {len(x_cookies)} X cookies from {snap_path.name}")
                pw_cookies = []
                for c in x_cookies:
                    pw_cookies.append({
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".x.com"),
                        "path": c.get("path", "/"),
                        "secure": c.get("secure", True),
                        "httpOnly": c.get("httpOnly", False),
                    })
                return pw_cookies
        except Exception as e:
            log(f"Error loading {snap_path}: {e}")
    return []


def load_seen_ids():
    if SEEN_IDS_FILE.exists():
        try:
            with open(SEEN_IDS_FILE) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen_ids(ids):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    recent = list(ids)[-500:]
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(recent, f)


def _is_venezuela_related(text: str) -> bool:
    """Check if tweet text is a Venezuelan DENUNCIA — strict filter.

    Must be about Venezuela AND about a denuncia/abuse/corruption/help topic.
    Generic mentions of Venezuela from foreign accounts → filtered out.
    """
    if not text:
        return False

    t = text.lower()

    # ── HARD REJECT: non-Venezuela content ──
    reject_place = [
        'mexico', 'méxico', 'oaxaca', 'guadalajara', 'monterrey',
        'colombia', 'bogotá', 'bogota', 'cali', 'medellin',
        'ecuador', 'guayaquil', 'quito',
        'españa', 'madrid', 'barcelona',
        'argentina', 'buenos aires',
        'chile', 'santiago',
        'perú', 'peru', 'lima',
        'brasil', 'são paulo',
        'usa', 'eeuu', 'united states', 'texas', 'florida', 'california',
        'china', 'russia', 'israel', 'palestine', 'gaza', 'iran',
        'uruguay', 'paraguay', 'bolivia', 'panama', 'cuba',
        'bangladesh', 'india', 'pakistan',
    ]
    reject_kw = [
        'westcol', 'mrbeast', 'super bowl', 'burger king', 'el xokas',
        'jake paul', 'john cena', 'hailey bieber', 'kylie jenner', 'justin bieber',
        'jlo', 'jennifer lopez', 'opera', 'verdi', 'traviata',
        'meme', 'pov:', 'vibe coding', 'chatgpt', 'ai art',
        'crypto', 'bitcoin', 'roxom', 'gift cards', 'esim',
        'magnesiacore', 'link building', 'seo',
        'kubernetes', 'cron job', 'tesla', 'huawei', 'localiza rent',
        'ford gt', 'blue angels', 'robot fight', 'humanoid robot',
        'clinton', 'trump', 'cia', 'fbi', 'nsa',
        'palestina', 'gaza', 'lebanon', 'hezbollah',
        'cliff jumping', 'phone run', 'exam',
        'burgers', 'hot dog', 'recipe', 'cooking', 'gym', 'workout',
        'tattoo', 'piercing', 'makeup', 'fashion', 'outfit',
        'soccer', 'football', 'mundial', 'world cup', 'gol de',
        'weather', 'flat earth', 'moon',
        'concierto', 'baladas', 'pop-rock', 'album', 'spotify',
        'juego', 'video game', 'playstation', 'xbox', 'nintendo',
        'perrito', 'mascota', 'gatito', 'hamster',
        'lit killah', 'la velada', 'streamer', 'twitch',
        'selena', 'taylor swift',
    ]
    for kw in reject_place + reject_kw:
        if kw in t:
            return False

    # ── HARD REJECT: too short or no media context ──
    if len(t.strip()) < 30:
        return False

    # ── MUST MATCH: Venezuelan denuncia/abuse/help keywords ──
    # The tweet must mention Venezuela AND a denuncia-related topic
    venezuela_terms = [
        'venezuela', 'venezolano', 'venezolana', 'venezolanos', 'venezolanas',
        'caracas', 'guaira', 'la guaira', 'catia', 'maracaibo', 'valencia',
        'barquisimeto', 'maracay', 'táchira', 'tachira', 'aragua', 'bolívar',
        'bolivar', 'zulia', 'miranda', 'vargas', 'distrito capital',
        'maduro', 'cabello', 'diosdado', 'delcy', 'psuv', 'chavismo', 'chavez',
        'famb', 'fanb', 'colectivos',
        '#venezuela', '#vargas', '#laguaira', '#caracas', '#maracaibo',
        'lguaira', 'laguaira', 'san bernardino', 'puerto cabello',
        'san mateo', 'aceiteira',
    ]
    denuncia_terms = [
        'denuncia', 'abuso', 'corrupción', 'corrupcion', 'extorsion', 'extorsión',
        'desalojo', 'reten', 'detención', 'detencion', 'tortura', 'desaparecido',
        'desaparecida', 'desaparecidos', 'preso político', 'preso politico',
        'presa política', 'presa politica', 'presos políticos',
        'persecución', 'persecucion', 'represión', 'represion', 'censura',
        'sicariato', 'homicidio', 'asesinato', 'muerte',
        'terremoto', 'sismo', 'emergencia', 'víctima', 'victima', 'escombros',
        'ayuda', 'humanitaria', 'donación', 'donacion', 'reconstrucción',
        'ddhh', 'derechos humanos', 'libertad', 'justicia', 'solidaridad',
        'robo', 'robó', 'huracán', 'huracan', 'guaidó', 'guaido', 'oposición',
        'oposicion', 'preso', 'presos', 'rescate', 'rescatista',
        'sin luz', 'sin agua', 'racionamiento', 'apagón', 'apagon',
        'militar', 'policial', 'policía', 'policia', 'gnb', 'pnb',
        'discapacidad', 'enfermedad', 'hospital', 'médico', 'medico',
        'bono', 'examen', 'tratamiento', 'cirugía', 'cirugia',
        'fallecido', 'fallecida', 'víctimas', 'victimas',
        'bloqueo', 'escasez', 'colapso', 'emergencia',
        'protección', 'proteccion', 'menor', 'niño', 'niña', 'trata',
        'femicidio', 'violación', 'violacion', 'violencia',
        'arresto', 'arrestada', 'arrestado', 'captura', 'capturado',
        'injusticia', 'abuso de poder', 'exilio', 'proscripción',
    ]

    has_venezuela = any(kw in t for kw in venezuela_terms)
    has_denuncia = any(kw in t for kw in denuncia_terms)

    # Path 1: Venezuela + denuncia = pass
    if has_venezuela and has_denuncia:
        return True

    # Path 2: High-confidence Venezuela names = pass (they only tweet about VZ)
    high_confidence_vz = ['maduro', 'diosdado', 'delcy', 'cabello', 'psuv', 'chavismo']
    if any(kw in t for kw in high_confidence_vz):
        return True

    # Path 3: Strong denuncia keywords = pass (terremoto, presa política, etc.)
    strong_denuncia = [
        'terremoto', 'sismo', 'escombros', 'desaparecido', 'desaparecida',
        'desaparecidos', 'preso político', 'presa política', 'presos políticos',
        'extorsión', 'extorsion', 'ddhh', 'derechos humanos',
        'sin luz', 'sin agua', 'apagón', 'racionamiento',
        'trata de personas', 'tráfico de niños', 'femicidio',
        'bono', 'discapacidad', 'enfermedad', 'hospital',
        'urgente', 'difundir', 'auxilio', 'socorro',
    ]
    if any(kw in t for kw in strong_denuncia):
        return True

    # Path 4: Known Venezuelan denuncia accounts = pass
    known_denuncia_accounts = [
        'freddyzur', 'cristiancrespoj', 'fantasma_956', 'elpitazotv',
        'caraotadigital', 'noticiasvzla', 'aborde', 'provea',
        'centrode ddhh', 'vision24tv', 'vpitv', '2001online',
        'informaorwell', 'sinmordaza', 'latvcalle', 'revistacicpc',
        'contrapoder30', 'periodicodeuna', 'elinformadorve', 'gabyarocha',
        'difundeloya', 'libre_oposicion', 'valvulapolitica', 'ari1979_v',
        'shelbykrisel', 'tamara_suju', 'ntn24', 'luzmelyreyes',
        'sergiohdzguerra', 'estebanoria', 'joshkr1441', 'robortecarlo14',
        'gabygabygg', 'orlavision', 'uhn_plus', 'elganadorhenry',
        'sosvenezuelaj', 'vr_vzlalibre_3', 'pipogonza',
    ]
    for acct in known_denuncia_accounts:
        if acct in t:
            return True

    # Path 5: Missing person pattern (family member + full name)
    import re
    if re.search(r'señora|señor|hijo|madre|padre|familia|hermano|hermana', t):
        # Check if there's a full name (title case OR ALL CAPS)
        if re.search(r'[A-ZÁÉÍÓÚ][a-záéíóú]+ [A-ZÁÉÍÓÚ][a-záéíóú]+', text) or \
           re.search(r'[A-ZÁÉÍÓÚ]{2,} [A-ZÁÉÍÓÚ]{2,}', text):
            return True

    return False


def _save_to_db(all_tweets: List[Dict], conn, venezuela_only: bool = True) -> Dict[str, Any]:
    """Save scraped tweets to SQLite. Merges same-topic tweets as sources.

    Returns counts of inserted, merged, skipped.
    """
    seen = set()
    unique = []
    for t in all_tweets:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)

    inserted = 0
    merged = 0
    skipped = 0
    filtered = 0
    expediente_ids = []

    for t in unique:
        topic_hash = _topic_fingerprint(t.get("text", ""))
        result = insert_denuncia(conn, {
            "tweet_id": t["id"],
            "username": t["username"],
            "display_name": t.get("name", t["username"]),
            "text": t.get("text", ""),
            "video_url": t.get("video_url"),
            "images": t.get("images", []),
            "retweets": t.get("retweets", 0),
            "likes": t.get("likes", 0),
            "replies": t.get("replies", 0),
            "created_at": t.get("created_at", ""),
            "source_url": t.get("url", ""),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "topic_hash": topic_hash,
        })
        if result["is_new"]:
            inserted += 1
            expediente_ids.append(result["expediente_id"])
        elif result.get("merged_into"):
            merged += 1
            log(f"  Merged @{t['username']} into {result['merged_into']} (source #{result.get('source_count', '?')})")
        else:
            skipped += 1

    return {"inserted": inserted, "merged": merged, "skipped": skipped, "filtered": filtered, "expediente_ids": expediente_ids}


async def _wait_for_media(page, timeout_ms=5000):
    """Wait for media elements (images/videos) to appear in tweet articles after scrolling."""
    # Try each selector with partial timeout
    per_selector = max(timeout_ms // 5, 1000)
    selectors = [
        "video",
        "[data-testid='videoPlayer']",
        "img[src*='pbs.twimg.com/media']",
        "img[src*='ton.twimg.com']",
        "img[alt*='Image']",
    ]
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=per_selector)
            # If we found a video or player, good enough
            if sel in ("video", "[data-testid='videoPlayer']"):
                return
        except Exception:
            continue


class _VideoUrlCapture:
    """Intercepts network requests to capture actual video file URLs."""

    def __init__(self):
        self.captured = []  # list of captured video URLs in order

    def install(self, page):
        self._page = page
        page.on("response", self._on_response)

    def _on_response(self, response):
        url = response.url
        # Capture any video-related response from twimg domains
        if any(d in url for d in ["video.twimg.com", "ext_tw_video", "amplify_video"]):
            self.captured.append(url)
        # Also capture from pbs if it looks like video
        elif "pbs.twimg.com" in url and "/ext_tw_video" in url:
            self.captured.append(url)

    def pop_latest(self):
        """Pop the most recently captured video URL."""
        if self.captured:
            return self.captured.pop(0)
        return None


async def extract_tweets_from_page(page):
    """Extract tweet data from the currently loaded X search page."""
    tweets = []
    articles = await page.query_selector_all("article[data-testid='tweet']")
    log(f"  Found {len(articles)} tweet articles in DOM")

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

            # --- Media extraction (improved) ---
            video_url = None

            # 1. Try <video> with direct src
            video_el = await art.query_selector("video")
            if video_el:
                src = await video_el.get_attribute("src")
                if src and "video" in src and not src.startswith("blob:"):
                    video_url = src

            # 2. Try <video><source> child
            if not video_url:
                source_el = await art.query_selector("video source")
                if source_el:
                    src = await source_el.get_attribute("src")
                    if src and not src.startswith("blob:"):
                        video_url = src

            # 3. Try videoPlayer testid (video present but src not exposed)
            if not video_url:
                play_btn = await art.query_selector("[data-testid='videoPlayer']")
                if play_btn:
                    video_url = "HAS_VIDEO"

            # 4. Try aria-label on video container (some tweet layouts)
            if not video_url:
                vid_container = await art.query_selector("[aria-label*='video' i]")
                if vid_container:
                    video_url = "HAS_VIDEO"

            # --- Image extraction (multiple strategies) ---
            images = []

            # Strategy A: standard pbs.twimg.com media images
            img_els = await art.query_selector_all("img[src*='pbs.twimg.com/media']")
            for img in img_els[:4]:
                src = await img.get_attribute("src")
                if src:
                    # Upgrade to larger size: name=small -> name=large
                    high = src.replace("name=small", "name=large").replace("name=medium", "name=large")
                    if "name=" not in high:
                        high += "&name=large"
                    images.append(high)

            # Strategy B: ton.twimg.com images
            if not images:
                ton_imgs = await art.query_selector_all("img[src*='ton.twimg.com']")
                for img in ton_imgs[:4]:
                    src = await img.get_attribute("src")
                    if src:
                        images.append(src)

            # Strategy C: any img with alt text suggesting it's a tweet image
            if not images:
                alt_imgs = await art.query_selector_all("img[alt*='Image' i]")
                for img in alt_imgs[:4]:
                    src = await img.get_attribute("src")
                    if src and ("twimg" in src or "pbs" in src or "ton" in src):
                        images.append(src)

            time_el = await art.query_selector("time")
            created_at = ""
            if time_el:
                created_at = await time_el.get_attribute("datetime") or ""

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
                "created_at": created_at,
                "video_url": video_url if video_url != "HAS_VIDEO" else None,
                "has_video": video_url is not None,
                "images": images,
                "retweets": stats.get("retweet", 0),
                "likes": stats.get("favorite", 0),
                "replies": stats.get("reply", 0),
            }
            tweets.append(tweet)

        except Exception as e:
            log(f"  Error extracting tweet: {e}")
            continue

    return tweets


async def _extract_with_video_capture(page):
    """Extract tweets from page, capturing actual video URLs via network interception.

    For each tweet with a video player, scrolls to it and captures the network
    video URL that loads, replacing the useless blob: URL.
    """
    tweets = []
    articles = await page.query_selector_all("article[data-testid='tweet']")
    log(f"  Found {len(articles)} tweet articles in DOM")

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

            # --- Media extraction with video capture ---
            video_url = None
            has_video = False

            # Check if this tweet has a video player
            play_btn = await art.query_selector("[data-testid='videoPlayer']")
            vid_container = await art.query_selector("[aria-label*='video' i]")
            video_el = await art.query_selector("video")
            has_video = play_btn is not None or vid_container is not None or video_el is not None

            if has_video:
                # Set up one-shot network capture for this tweet's video
                captured_urls = []

                def on_response(response):
                    u = response.url
                    if any(d in u for d in ["video.twimg.com", "ext_tw_video", "amplify_video"]):
                        captured_urls.append(u)
                    elif "pbs.twimg.com" in u and "amplify_video" in u:
                        captured_urls.append(u)

                page.on("response", on_response)

                # Scroll this tweet into view to trigger video loading
                try:
                    await art.scroll_into_view_if_needed()
                    await asyncio.sleep(2.5)
                except Exception:
                    pass

                # Remove listener
                page.remove_listener("response", on_response)

                # Use captured URL if available
                if captured_urls:
                    # Prefer .m3u8 or .mp4 over thumbnails
                    for u in captured_urls:
                        if ".m3u8" in u or ".mp4" in u:
                            video_url = u
                            break
                    if not video_url and captured_urls:
                        video_url = captured_urls[0]

            # Also try direct DOM extraction (non-blob)
            if not video_url:
                if video_el:
                    src = await video_el.get_attribute("src")
                    if src and "video" in src and not src.startswith("blob:"):
                        video_url = src
                if not video_url:
                    source_el = await art.query_selector("video source")
                    if source_el:
                        src = await source_el.get_attribute("src")
                        if src and not src.startswith("blob:"):
                            video_url = src

            # --- Image extraction ---
            images = []

            # Strategy A: standard pbs.twimg.com media images
            img_els = await art.query_selector_all("img[src*='pbs.twimg.com/media']")
            for img in img_els[:4]:
                src = await img.get_attribute("src")
                if src:
                    high = src.replace("name=small", "name=large").replace("name=medium", "name=large")
                    if "name=" not in high:
                        high += "&name=large"
                    images.append(high)

            # Strategy B: ton.twimg.com images
            if not images:
                ton_imgs = await art.query_selector_all("img[src*='ton.twimg.com']")
                for img in ton_imgs[:4]:
                    src = await img.get_attribute("src")
                    if src:
                        images.append(src)

            # Strategy C: any img with alt text suggesting it's a tweet image
            if not images:
                alt_imgs = await art.query_selector_all("img[alt*='Image' i]")
                for img in alt_imgs[:4]:
                    src = await img.get_attribute("src")
                    if src and ("twimg" in src or "pbs" in src or "ton" in src):
                        images.append(src)

            time_el = await art.query_selector("time")
            created_at = ""
            if time_el:
                created_at = await time_el.get_attribute("datetime") or ""

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
                "created_at": created_at,
                "video_url": video_url,
                "has_video": has_video,
                "images": images,
                "retweets": stats.get("retweet", 0),
                "likes": stats.get("favorite", 0),
                "replies": stats.get("reply", 0),
            }
            tweets.append(tweet)

        except Exception as e:
            log(f"  Error extracting tweet: {e}")
            continue

    return tweets
    """Navigate to X search for a query, scroll, and extract tweets."""
    url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
    log(f"  Navigating to: {query}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(3)

        if "login" in (page.url or "").lower():
            log("  ERROR: Session expired, redirected to login")
            return []

        all_tweets = []
        for i in range(4):
            # Wait for lazy-loaded media to appear in DOM
            await _wait_for_media(page, timeout_ms=4000)

            # Scroll each tweet into view to trigger media loading
            articles = await page.query_selector_all("article[data-testid='tweet']")
            for art in articles[:8]:
                try:
                    await art.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

            # Brief pause for media to finish loading
            await asyncio.sleep(1.5)

            # Extract tweets with video capture per-article
            tweets = await _extract_with_video_capture(page)

            new_count = 0
            for t in tweets:
                if t["id"] not in seen_ids:
                    all_tweets.append(t)
                    seen_ids.add(t["id"])
                    new_count += 1

            log(f"  Scroll {i+1}/4: {len(tweets)} found, {new_count} new")

            await page.evaluate("window.scrollBy(0, 1200)")
            await asyncio.sleep(random.uniform(2.0, 4.0))

            if i % 3 == 2:
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(1)
                await page.evaluate("window.scrollBy(0, 400)")
                await asyncio.sleep(1.5)

        return all_tweets

    except Exception as e:
        log(f"  Error scraping '{query}': {e}")
        return []


async def run_scrape(
    queries: Optional[List[str]] = None,
    max_results: int = 60,
    scroll_rounds: int = 4,
) -> Dict[str, Any]:
    """Main scrape cycle. Saves to SQLite via denuncias_db.

    Args:
        queries: Custom search queries. If None, uses default SEARCH_QUERIES.
        max_results: Max tweets to return (default 60).
        scroll_rounds: Scroll cycles per query (default 4).

    Returns:
        Dict with 'inserted', 'skipped', 'expediente_ids', 'queries_used'.
    """
    from playwright.async_api import async_playwright

    log("=== Starting scrape cycle (Playwright) ===")

    pw_cookies = load_x_cookies()
    if not pw_cookies:
        log("No cookies available. Exiting.")
        return {"inserted": 0, "skipped": 0, "expediente_ids": [], "queries_used": []}

    seen_ids = load_seen_ids()
    all_tweets = []

    search_queries = queries or DEFAULT_SEARCH_QUERIES
    queries_used = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--window-position=100,100"]
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/128.0",
            viewport={"width": 1280, "height": 900},
            locale="es-VE",
        )

        await ctx.add_cookies(pw_cookies)
        page = await ctx.new_page()

        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        if "login" in (page.url or "").lower():
            log("ERROR: Not logged in. Cookies may be expired.")
            await browser.close()
            return {"inserted": 0, "skipped": 0, "expediente_ids": [], "queries_used": []}

        log("Logged in successfully. Starting searches.")

        # Rotate queries — pick a random subset each run
        q = list(search_queries)
        random.shuffle(q)
        q = q[:3]  # Run 3 queries per cycle (fast)

        for query in q:
            # Navigate to X search
            search_url = f"https://x.com/search?q={query.replace(' ', '%20')}&src=typed_query&f=live"
            log(f"  Searching: '{query}'")
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)
            except Exception as e:
                log(f"  ERROR navigating to search: {e}")
                continue

            # Scroll to load more tweets
            for scroll_round in range(scroll_rounds):
                await page.evaluate("window.scrollBy(0, 1200)")
                await asyncio.sleep(1.5)

            # Extract tweets
            tweets = await extract_tweets_from_page(page)
            # Filter out already seen IDs
            new_tweets = [t for t in tweets if t["id"] not in seen_ids]
            for t in new_tweets:
                seen_ids.add(t["id"])
            all_tweets.extend(new_tweets)
            queries_used.append(query)
            log(f"  Query '{query}': {len(new_tweets)} new, {len(tweets)} total on page")
            save_seen_ids(seen_ids)
            await asyncio.sleep(random.uniform(2.0, 4.0))

        await browser.close()

    # Deduplicate by ID
    seen = set()
    unique = []
    for t in all_tweets:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)

    # Only keep tweets with video
    with_video = [t for t in unique if t.get("has_video")]
    with_video.sort(key=lambda t: t.get("likes", 0) + t.get("retweets", 0), reverse=True)
    combined = with_video[:max_results]

    log(f"=== Results: {len(with_video)} with video, {len(combined)} total ===")

    save_seen_ids(seen_ids)

    # Save to SQLite
    conn = init_db()
    result = _save_to_db(combined, conn, venezuela_only=False)
    export_to_json(conn)
    conn.close()

    result["queries_used"] = queries_used
    log(f"=== DB: {result['inserted']} inserted, {result['merged']} merged, {result['skipped']} skipped ===")

    return result


def search_denuncias(
    queries: List[str],
    max_results: int = 20,
) -> Dict[str, Any]:
    """Synchronous wrapper for MCP tool. Runs the async scrape and returns results.

    This is the primary entry point for the MCP server's search_denuncias tool.

    Args:
        queries: Search queries to run on X/Twitter.
        max_results: Max results per query.

    Returns:
        Dict with 'new_denuncias', 'skipped', 'queries_used'.
    """
    result = asyncio.run(run_scrape(queries=queries, max_results=max_results))
    return {
        "new_denuncias": result.get("expediente_ids", []),
        "merged": result.get("merged", 0),
        "skipped": result.get("skipped", 0),
        "queries_used": result.get("queries_used", []),
    }


async def _backfill_media_async(max_denuncias: int = 50) -> Dict[str, Any]:
    """Visit tweet URLs for denuncias with no media and extract images/videos.

    Uses per-tweet video capture to get actual video URLs (not blob:).
    Returns dict with 'updated', 'failed', 'skipped' counts.
    """
    from playwright.async_api import async_playwright

    conn = init_db()
    rows = conn.execute(
        "SELECT expediente_id, tweet_id, source_url, images, video_url "
        "FROM denuncias "
        "WHERE (images = '[]' AND video_url IS NULL) OR video_url LIKE 'blob:%' "
        "ORDER BY id DESC "
        f"LIMIT {max_denuncias}"
    ).fetchall()
    conn.close()

    if not rows:
        log("Backfill: no denuncias need media.")
        return {"updated": 0, "failed": 0, "skipped": 0}

    log(f"Backfill: {len(rows)} denuncias need media extraction.")

    pw_cookies = load_x_cookies()
    if not pw_cookies:
        log("Backfill: no cookies available.")
        return {"updated": 0, "failed": 0, "skipped": len(rows)}

    updated = 0
    failed = 0

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

        # Login check
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        if "login" in (page.url or "").lower():
            log("Backfill: not logged in.")
            await browser.close()
            return {"updated": 0, "failed": 0, "skipped": len(rows)}

        for row in rows:
            exp_id, tweet_id, source_url, existing_images, existing_video = row
            if not source_url:
                failed += 1
                continue

            try:
                log(f"  Backfill {exp_id}: {source_url}")
                await page.goto(source_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)

                # Use per-tweet video capture
                tweets = await _extract_with_video_capture(page)

                if not tweets:
                    failed += 1
                    log(f"  Backfill {exp_id}: no tweet found on page")
                    continue

                # Use the first tweet (the main one on the page)
                t = tweets[0]
                images = t.get("images", [])
                video_url = t.get("video_url")

                # Update DB if we found something
                if images or video_url:
                    conn = init_db()
                    fields = {}
                    if images:
                        fields["images"] = images
                    if video_url:
                        fields["video_url"] = video_url
                    update_denuncia(conn, exp_id, fields)
                    export_to_json(conn)
                    conn.close()
                    updated += 1
                    log(f"  Backfill {exp_id}: updated ({len(images)} images, video={'yes' if video_url else 'no'})")
                else:
                    failed += 1
                    log(f"  Backfill {exp_id}: no media found")

                await asyncio.sleep(random.uniform(1.5, 3.0))

            except Exception as e:
                log(f"  Backfill {exp_id}: error - {e}")
                failed += 1

        await browser.close()

    log(f"Backfill complete: {updated} updated, {failed} failed")
    return {"updated": updated, "failed": failed, "skipped": 0}


def backfill_media(max_denuncias: int = 50) -> Dict[str, Any]:
    """Synchronous wrapper for backfill_media. Visit tweet URLs to grab missed media."""
    return asyncio.run(_backfill_media_async(max_denuncias=max_denuncias))


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run_scrape())
    print(f"\nDone. Inserted: {result['inserted']}, Skipped: {result['skipped']}")
