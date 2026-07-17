#!/usr/bin/env python3
"""Visit tweet URLs, capture video m3u8 from network, download as mp4 immediately."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from .denuncias_db import init_db, update_denuncia, export_to_json
    from .denuncias_scraper import load_x_cookies, _wait_for_media
except ImportError:
    from denuncias_db import init_db, update_denuncia, export_to_json
    from denuncias_scraper import load_x_cookies, _wait_for_media

VIDEOS_DIR = Path("/mnt/sdb1/MoneyMakers_webops/Web1/expediente-venezuela/media/videos")
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


def download_m3u8_as_mp4(m3u8_url: str, output_path: Path) -> bool:
    """Download m3u8 stream to mp4 using ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", m3u8_url, "-c", "copy", "-movflags", "+faststart", str(output_path)],
            capture_output=True, timeout=180
        )
        return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1000
    except Exception as e:
        print(f"  ffmpeg error: {e}")
        return False


async def capture_and_download(max_videos: int = 50):
    """Capture and download videos from tweet pages.
    
    Targets:
    1. Denuncias with no video URL (NULL or 'None')
    2. Denuncias with HTTP video URLs (expired m3u8/mp4)
    3. Denuncias with blob: video URLs (expired)
    """
    from playwright.async_api import async_playwright

    conn = init_db()
    rows = conn.execute(
        "SELECT expediente_id, tweet_id, source_url, video_url "
        "FROM denuncias "
        "WHERE (video_url IS NULL OR video_url = 'None' OR video_url LIKE 'http%' OR video_url LIKE 'blob:%') "
        "AND source_url IS NOT NULL "
        "ORDER BY id DESC "
        f"LIMIT {max_videos}"
    ).fetchall()
    conn.close()

    if not rows:
        print("No denuncias need video capture.")
        return

    print(f"Checking {len(rows)} denuncias for videos...")

    pw_cookies = load_x_cookies()
    if not pw_cookies:
        print("No cookies.")
        return

    downloaded = 0
    skipped = 0
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

        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        if "login" in (page.url or "").lower():
            print("Not logged in.")
            await browser.close()
            return

        for exp_id, tweet_id, source_url, existing_video in rows:
            if not source_url:
                continue

            # Skip if already downloaded locally
            local_file = VIDEOS_DIR / f"{exp_id}.mp4"
            if local_file.exists() and local_file.stat().st_size > 1000:
                print(f"  {exp_id}: already downloaded, skipping")
                continue

            print(f"  {exp_id}: {source_url}")

            # Capture video URLs from network
            captured_video_urls = []

            def on_response(response):
                url = response.url
                if any(d in url for d in ["video.twimg.com", "ext_tw_video", "amplify_video"]):
                    if ".m3u8" in url or ".mp4" in url:
                        captured_video_urls.append(url)

            page.on("response", on_response)

            try:
                await page.goto(source_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)

                # Check if page has video
                has_video_el = await page.query_selector("video")
                has_player = await page.query_selector("[data-testid='videoPlayer']")

                if not has_video_el and not has_player:
                    print(f"    No video on page, skipping")
                    page.remove_listener("response", on_response)
                    skipped += 1
                    continue

                # Scroll to trigger video loading
                await page.evaluate("window.scrollBy(0, 300)")
                await asyncio.sleep(2)

                # Wait for video to load
                try:
                    await page.wait_for_selector("video", timeout=5000)
                except Exception:
                    pass

                await asyncio.sleep(3)

                page.remove_listener("response", on_response)

                if not captured_video_urls:
                    print(f"    No video URL captured")
                    failed += 1
                    continue

                # Find the best m3u8 URL (prefer variant playlists with resolution)
                best_url = None
                for url in captured_video_urls:
                    if "variant_branch" in url or "360x" in url or "480x" in url or "720x" in url:
                        best_url = url
                        break
                if not best_url:
                    # Just pick the first m3u8
                    for url in captured_video_urls:
                        if ".m3u8" in url:
                            best_url = url
                            break
                if not best_url:
                    best_url = captured_video_urls[0]

                print(f"    Captured: {best_url[:80]}...")

                # Download immediately
                success = download_m3u8_as_mp4(best_url, local_file)
                if success:
                    size_kb = local_file.stat().st_size / 1024
                    print(f"    Downloaded: {size_kb:.0f} KB")

                    # Update DB with local path
                    conn = init_db()
                    update_denuncia(conn, exp_id, {
                        "video_url": f"media/videos/{exp_id}.mp4"
                    })
                    export_to_json(conn)
                    conn.close()
                    downloaded += 1
                else:
                    print(f"    Download failed")
                    failed += 1

            except Exception as e:
                print(f"    Error: {e}")
                page.remove_listener("response", on_response)
                failed += 1

            await asyncio.sleep(2)

        await browser.close()

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    asyncio.run(capture_and_download())
