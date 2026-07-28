#!/usr/bin/env python3
"""Batch download missing videos using yt-dlp + Ferdium cookies."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

VIDEOS_DIR = Path("/media/hobeat/CC06471F06470A42/app/expediente-venezuela/media/videos")
DB_PATH = Path(__file__).parent / "data" / "denuncias.db"
COOKIE_FILE = "/tmp/x_cookies.txt"
FERDIUM_DIR = Path.home() / ".ferdium-inject/snapshots"

def create_cookie_file():
    """Convert Ferdium cookies to Netscape format for yt-dlp."""
    snap = FERDIUM_DIR / "service-0fe1114b-1587-4983-9284-5c4d63eced08.latest.json"
    if not snap.exists():
        print("No Ferdium snapshot found")
        return False
    with open(snap) as f:
        data = json.load(f)
    cookies = [c for c in data.get("cookies", []) if "x.com" in c.get("domain", "") or "twitter.com" in c.get("domain", "")]
    if not cookies:
        print("No X cookies found")
        return False
    with open(COOKIE_FILE, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", ".x.com")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expiry = str(c.get("expiry", "0") or "0")
            f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{c['name']}\t{c['value']}\n")
    return True

def download_video(exp_id, url, output_path):
    """Download a single video using yt-dlp."""
    cmd = [
        "yt-dlp",
        "--cookies", COOKIE_FILE,
        "-o", str(output_path),
        "--format", "best[ext=mp4]/best",
        "--no-playlist",
        "--quiet",
        url
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1000:
            return True, output_path.stat().st_size
        else:
            err = result.stderr[-200:] if result.stderr else "unknown error"
            return False, err
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)

def generate_poster(video_path, poster_path):
    """Generate poster image from first frame."""
    if poster_path.exists():
        return True
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "5",
        str(poster_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0 and poster_path.exists()
    except:
        return False

def main():
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    (VIDEOS_DIR.parent / "posters").mkdir(parents=True, exist_ok=True)

    if not create_cookie_file():
        print("Cannot create cookie file. Exiting.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT expediente_id, source_url FROM denuncias "
        "WHERE source_url IS NOT NULL AND source_url != '' "
        "ORDER BY id ASC"
    ).fetchall()

    total = len(rows)
    downloaded = 0
    skipped = 0
    failed = 0

    print(f"Found {total} denuncias with source URLs")

    for exp_id, source_url in rows:
        video_path = VIDEOS_DIR / f"{exp_id}.mp4"
        poster_path = VIDEOS_DIR.parent / "posters" / f"{exp_id}.jpg"

        if video_path.exists() and video_path.stat().st_size > 1000:
            skipped += 1
            if skipped % 10 == 0:
                print(f"  Skipped {skipped} (already downloaded)...")
            # Generate poster if missing
            if not poster_path.exists():
                generate_poster(video_path, poster_path)
            continue

        print(f"  [{downloaded + failed + 1}/{total - skipped}] {exp_id}: {source_url[:70]}...")
        success, info = download_video(exp_id, source_url, video_path)

        if success:
            size_mb = info / (1024 * 1024)
            print(f"    OK: {size_mb:.1f} MB")
            downloaded += 1
            generate_poster(video_path, poster_path)
        else:
            print(f"    FAIL: {info}")
            failed += 1

    conn.close()
    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {failed} failed, {total} total")

if __name__ == "__main__":
    main()
