"""SQLite database layer for Expediente Venezuela denuncias."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set, Tuple

# Spanish stop words (high frequency, low meaning)
STOP_WORDS: Set[str] = {
    "de", "la", "el", "en", "y", "a", "los", "del", "las", "un", "por",
    "con", "no", "una", "su", "para", "es", "al", "lo", "como", "más",
    "pero", "sus", "le", "ya", "o", "este", "sí", "porque", "esta",
    "entre", "cuando", "muy", "sin", "sobre", "también", "me", "hasta",
    "hay", "donde", "quien", "desde", "todo", "nos", "durante", "todos",
    "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante", "ellos",
    "e", "esto", "mí", "antes", "algunos", "qué", "unos", "yo", "otro",
    "otras", "otra", "él", "tanto", "esa", "estos", "mucho", "quienes",
    "nada", "muchos", "cual", "poco", "ella", "estar", "estas", "algunas",
    "algo", "nosotros", "mi", "mis", "tú", "te", "ti", "tu", "tus",
    "ellas", "nosotras", "vosotros", "vosotras", "os", "mío", "mía",
    "míos", "mías", "tuyo", "tuya", "tuyos", "tuyas", "suyo", "suya",
    "suyos", "suyas", "nuestro", "nuestra", "nuestros", "nuestras",
    "vuestro", "vuestra", "vuestros", "vuestras", "esos", "esas",
    "estoy", "estás", "está", "estamos", "estáis", "están", "esté",
    "estés", "estemos", "estéis", "estén", "estaré", "estarás", "estará",
    "estaremos", "estaréis", "estarán", "estaría", "estarías", "estaríamos",
    "estaríais", "estarían", "estaba", "estabas", "estábamos", "estabais",
    "estaban", "estuve", "estuviste", "estuvo", "estuvimos", "estuvisteis",
    "estuvieron", "estuviera", "estuvieras", "estuviéramos", "estuvierais",
    "estuvieran", "estuviese", "estuvieses", "estuviésemos", "estuvieseis",
    "estuviesen", "estando", "estado", "estada", "estados", "estadas",
    "estad", "he", "has", "ha", "hemos", "habéis", "han", "haya",
    "hayas", "hayamos", "hayáis", "hayan", "habré", "habrás", "habrá",
    "habremos", "habréis", "habrán", "habría", "habrías", "habríamos",
    "habríais", "habrían", "había", "habías", "habíamos", "habíais",
    "habían", "hube", "hubiste", "hubo", "hubimos", "hubisteis", "hubieron",
    "hubiera", "hubieras", "hubiéramos", "hubierais", "hubieran", "hubiese",
    "hubieses", "hubiésemos", "hubieseis", "hubiesen", "habiendo", "ido",
    "ida", "idos", "idas", "ser", "eres", "es", "somos", "sois", "son",
    "sea", "seas", "seamos", "seáis", "sean", "seré", "serás", "será",
    "seremos", "seréis", "serán", "sería", "serías", "seríamos", "seríais",
    "serían", "era", "eras", "éramos", "erais", "eran", "fui", "fuiste",
    "fue", "fuimos", "fuisteis", "fueron", "fuera", "fueras", "fuéramos",
    "fuerais", "fueran", "fuese", "fueses", "fuésemos", "fueseis", "fuesen",
    "sintiendo", "sentido", "sentida", "sentidos", "sentidas", "siente",
    "sentid", "tengo", "tienes", "tiene", "tenemos", "tenéis", "tienen",
    "tenga", "tengas", "tengamos", "tengáis", "tengan", "tendré", "tendrás",
    "tendrá", "tendremos", "tendréis", "tendrán", "tendría", "tendrías",
    "tendríamos", "tendríais", "tendrían", "tenía", "tenías", "teníamos",
    "teníais", "tenían", "tuve", "tuviste", "tuvo", "tuvimos", "tuvisteis",
    "tuvieron", "tuviera", "tuvieras", "tuviéramos", "tuvierais", "tuvieran",
    "tuviese", "tuvieses", "tuviésemos", "tuvieseis", "tuviesen", "teniendo",
    "tenido", "tenida", "tenidos", "tenidas", "tened",
    # Short words and URLs
    "rt", "https", "http", "amp", "via", "vs", "q", "d", "xq", "pq",
    "tb", "tn", "to", "ds", "dsps", " x ", " xq ", " xk ",
}


def _topic_fingerprint(text: str) -> str:
    """Extract a topic fingerprint from tweet text.

    Strips stop words, URLs, mentions, hashtags (keeping the word),
    and short tokens. Returns an MD5 hash of the sorted keywords.
    """
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r'https?://\S+', '', t)        # URLs
    t = re.sub(r'@\w+', '', t)                 # mentions
    t = re.sub(r'#(\w+)', r'\1', t)            # hashtag → word
    t = re.sub(r'[^\w\s]', ' ', t)             # punctuation
    words = t.split()
    keywords = [
        w for w in words
        if len(w) >= 3 and w not in STOP_WORDS
    ]
    if not keywords:
        return ""
    keywords.sort()
    return hashlib.md5(" ".join(keywords).encode()).hexdigest()[:16]


def _extract_keywords(text: str, max_kw: int = 8) -> List[str]:
    """Extract top keywords from text for similarity comparison."""
    if not text:
        return []
    t = text.lower()
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'@\w+', '', t)
    t = re.sub(r'#(\w+)', r'\1', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    words = t.split()
    keywords = [
        w for w in words
        if len(w) >= 3 and w not in STOP_WORDS
    ]
    seen = []
    for w in keywords:
        if w not in seen:
            seen.append(w)
        if len(seen) >= max_kw:
            break
    return seen


def _keywords_overlap(kw1: List[str], kw2: List[str]) -> float:
    """Return Jaccard-like overlap ratio between two keyword lists."""
    if not kw1 or not kw2:
        return 0.0
    s1, s2 = set(kw1), set(kw2)
    intersection = s1 & s2
    union = s1 | s2
    return len(intersection) / len(union) if union else 0.0


TOPIC_THRESHOLD = 0.35  # Min keyword overlap to consider "same topic"

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "denuncias.db"
EXPORT_PATH = DB_DIR / "denuncias.json"

CATEGORY_LABELS = {
    "corrupcion": "Corrupción",
    "abuso": "Abuso",
    "extorsion": "Extorsión",
    "desalojo": "Desalojo",
    "represion": "Represión",
    "servicios": "Servicios",
    "salud": "Salud",
    "censura": "Censura",
    "persecucion": "Persecución",
    "general": "General",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS denuncias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id TEXT UNIQUE NOT NULL,
    tweet_id TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    text TEXT,
    category TEXT DEFAULT 'general',
    severity TEXT DEFAULT 'info',
    status TEXT DEFAULT 'draft',
    video_url TEXT,
    images TEXT,
    retweets INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    created_at TEXT,
    scraped_at TEXT NOT NULL,
    published_at TEXT,
    source_url TEXT NOT NULL,
    query_used TEXT,
    tags TEXT,
    resumen TEXT,
    contexto TEXT,
    fuentes TEXT,
    evidencias TEXT,
    conclusion TEXT,
    topic_hash TEXT,
    source_tweets TEXT DEFAULT '[]',
    source_count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_denuncias_category ON denuncias(category);
CREATE INDEX IF NOT EXISTS idx_denuncias_status ON denuncias(status);
CREATE INDEX IF NOT EXISTS idx_denuncias_severity ON denuncias(severity);
CREATE INDEX IF NOT EXISTS idx_denuncias_tweet_id ON denuncias(tweet_id);
CREATE INDEX IF NOT EXISTS idx_denuncias_expediente ON denuncias(expediente_id);
"""

VALID_COLUMNS = {
    "category", "severity", "status", "video_url", "images", "retweets",
    "likes", "replies", "query_used", "tags", "resumen", "contexto",
    "fuentes", "evidencias", "conclusion", "display_name", "text",
    "topic_hash", "source_tweets", "source_count",
}


def _parse_json_field(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _serialize_json_field(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except (json.JSONDecodeError, TypeError):
            pass
    return json.dumps(value, ensure_ascii=False)


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for field in ("images", "tags", "fuentes", "evidencias", "source_tweets"):
        if field in d:
            d[field] = _parse_json_field(d[field])
    return d


def init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    # Migration: add new columns if missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(denuncias)").fetchall()}
    if "topic_hash" not in cols:
        conn.execute("ALTER TABLE denuncias ADD COLUMN topic_hash TEXT")
    if "source_tweets" not in cols:
        conn.execute("ALTER TABLE denuncias ADD COLUMN source_tweets TEXT DEFAULT '[]'")
    if "source_count" not in cols:
        conn.execute("ALTER TABLE denuncias ADD COLUMN source_count INTEGER DEFAULT 1")
    # Create index for topic_hash if not exists
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    if "idx_denuncias_topic_hash" not in indexes:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_denuncias_topic_hash ON denuncias(topic_hash)")
    conn.commit()
    return conn


def get_next_expediente_id(conn: sqlite3.Connection) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"EV-{year}-"
    row = conn.execute(
        "SELECT expediente_id FROM denuncias WHERE expediente_id LIKE ? ORDER BY expediente_id DESC LIMIT 1",
        (f"{prefix}%",)
    ).fetchone()
    if row:
        num = int(row["expediente_id"].split("-")[-1]) + 1
    else:
        num = 1
    return f"{prefix}{num:04d}"


def _find_topic_match(conn: sqlite3.Connection, tweet_text: str, topic_hash: str) -> Optional[Dict[str, Any]]:
    """Find an existing denuncia that covers the same topic.

    Strategy:
    1. Exact topic_hash match (fast).
    2. Keyword overlap against recent drafts (slower, fallback).
    """
    if topic_hash:
        row = conn.execute(
            "SELECT * FROM denuncias WHERE topic_hash = ? AND topic_hash != '' LIMIT 1",
            (topic_hash,),
        ).fetchone()
        if row:
            return row_to_dict(row)

    new_kw = _extract_keywords(tweet_text)
    if not new_kw:
        return None

    candidates = conn.execute(
        "SELECT * FROM denuncias WHERE status = 'draft' ORDER BY scraped_at DESC LIMIT 50"
    ).fetchall()

    best_match = None
    best_score = 0.0
    for row in candidates:
        existing_kw = _extract_keywords(row["text"] or "")
        score = _keywords_overlap(new_kw, existing_kw)
        if score >= TOPIC_THRESHOLD and score > best_score:
            best_score = score
            best_match = row_to_dict(row)

    return best_match


def insert_denuncia(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new denuncia or merge as source into existing same-topic denuncia.

    Returns:
        Dict with 'expediente_id', 'id', 'is_new', and optionally 'merged_into'.
    """
    tweet_id = data.get("tweet_id")
    if not tweet_id:
        raise ValueError("tweet_id is required")

    # 1. Exact tweet_id dedup
    existing = conn.execute(
        "SELECT expediente_id, id FROM denuncias WHERE tweet_id = ?", (tweet_id,)
    ).fetchone()
    if existing:
        return {"expediente_id": existing["expediente_id"], "id": existing["id"], "is_new": False}

    # 2. Topic fingerprint
    text = data.get("text", "")
    topic_hash = data.get("topic_hash") or _topic_fingerprint(text)

    # 3. Check for same-topic denuncia to merge into
    match = _find_topic_match(conn, text, topic_hash)
    if match:
        return _merge_source(conn, match, data, topic_hash)

    # 4. Insert as new denuncia
    expediente_id = data.get("expediente_id") or get_next_expediente_id(conn)
    scraped_at = data.get("scraped_at") or datetime.now(timezone.utc).isoformat()
    images = _serialize_json_field(data.get("images"))
    tags = _serialize_json_field(data.get("tags"))
    fuentes = _serialize_json_field(data.get("fuentes"))
    evidencias = _serialize_json_field(data.get("evidencias"))
    source_tweets = _serialize_json_field([
        {
            "tweet_id": tweet_id,
            "username": data.get("username", ""),
            "text": text[:200],
            "url": data.get("source_url", ""),
        }
    ])

    cur = conn.execute(
        """INSERT OR IGNORE INTO denuncias
        (expediente_id, tweet_id, username, display_name, text, category, severity,
         status, video_url, images, retweets, likes, replies, created_at, scraped_at,
         published_at, source_url, query_used, tags, resumen, contexto, fuentes,
         evidencias, conclusion, topic_hash, source_tweets, source_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (
            expediente_id, tweet_id,
            data.get("username", ""), data.get("display_name", data.get("username", "")),
            text, data.get("category", "general"),
            data.get("severity", "info"), data.get("status", "draft"),
            data.get("video_url"), images,
            data.get("retweets", 0), data.get("likes", 0), data.get("replies", 0),
            data.get("created_at", ""), scraped_at,
            data.get("published_at"), data.get("source_url", ""),
            data.get("query_used"), tags,
            data.get("resumen"), data.get("contexto"), fuentes,
            evidencias, data.get("conclusion"),
            topic_hash, source_tweets,
        ),
    )
    conn.commit()

    if cur.rowcount == 0:
        existing = conn.execute(
            "SELECT expediente_id, id FROM denuncias WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return {"expediente_id": existing["expediente_id"], "id": existing["id"], "is_new": False}

    return {"expediente_id": expediente_id, "id": cur.lastrowid, "is_new": True}


def _merge_source(
    conn: sqlite3.Connection,
    existing: Dict[str, Any],
    new_data: Dict[str, Any],
    topic_hash: str,
) -> Dict[str, Any]:
    """Merge a new tweet as an additional source into an existing denuncia."""
    existing_sources = existing.get("source_tweets") or []
    new_tweet_id = new_data.get("tweet_id")

    # Don't add same tweet twice
    if any(s.get("tweet_id") == new_tweet_id for s in existing_sources):
        return {
            "expediente_id": existing["expediente_id"],
            "id": existing["id"],
            "is_new": False,
            "merged_into": existing["expediente_id"],
        }

    source_entry = {
        "tweet_id": new_tweet_id,
        "username": new_data.get("username", ""),
        "text": (new_data.get("text") or ""),
        "url": new_data.get("source_url", ""),
    }
    existing_sources.append(source_entry)
    new_count = len(existing_sources)

    # Merge media: keep the best video (prefer new if existing has none)
    video_url = existing.get("video_url")
    if not video_url and new_data.get("video_url"):
        video_url = new_data["video_url"]

    # Merge images: combine unique
    existing_images = existing.get("images") or []
    new_images = new_data.get("images") or []
    merged_images = list(dict.fromkeys(existing_images + new_images))[:10]

    # Aggregate engagement
    total_likes = (existing.get("likes") or 0) + (new_data.get("likes") or 0)
    total_retweets = (existing.get("retweets") or 0) + (new_data.get("retweets") or 0)
    total_replies = (existing.get("replies") or 0) + (new_data.get("replies") or 0)

    conn.execute(
        """UPDATE denuncias SET
            source_tweets = ?,
            source_count = ?,
            video_url = COALESCE(?, video_url),
            images = ?,
            likes = ?,
            retweets = ?,
            replies = ?,
            topic_hash = COALESCE(?, topic_hash)
        WHERE id = ?""",
        (
            _serialize_json_field(existing_sources),
            new_count,
            video_url,
            _serialize_json_field(merged_images),
            total_likes,
            total_retweets,
            total_replies,
            topic_hash,
            existing["id"],
        ),
    )
    conn.commit()

    return {
        "expediente_id": existing["expediente_id"],
        "id": existing["id"],
        "is_new": False,
        "merged_into": existing["expediente_id"],
        "source_count": new_count,
    }


def get_denuncia(conn: sqlite3.Connection, denuncia_id: str) -> Optional[Dict[str, Any]]:
    if denuncia_id.isdigit():
        row = conn.execute("SELECT * FROM denuncias WHERE id = ?", (int(denuncia_id),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM denuncias WHERE expediente_id = ?", (denuncia_id,)).fetchone()
    return row_to_dict(row) if row else None


def list_denuncias(
    conn: sqlite3.Connection,
    category: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    limit = min(limit, 200)
    where = []
    params: list = []
    if category:
        where.append("category = ?")
        params.append(category)
    if status:
        where.append("status = ?")
        params.append(status)
    if severity:
        where.append("severity = ?")
        params.append(severity)

    sql = "SELECT * FROM denuncias"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY scraped_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]


def update_denuncia(conn: sqlite3.Connection, denuncia_id: str, fields: Dict[str, Any]) -> bool:
    valid = {k: v for k, v in fields.items() if k in VALID_COLUMNS}
    if not valid:
        return False

    for k in ("images", "tags", "fuentes", "evidencias"):
        if k in valid:
            valid[k] = _serialize_json_field(valid[k])

    set_clause = ", ".join(f"{k} = ?" for k in valid)
    values = list(valid.values())

    if denuncia_id.isdigit():
        values.append(int(denuncia_id))
        cur = conn.execute(f"UPDATE denuncias SET {set_clause} WHERE id = ?", values)
    else:
        values.append(denuncia_id)
        cur = conn.execute(f"UPDATE denuncias SET {set_clause} WHERE expediente_id = ?", values)
    conn.commit()
    return cur.rowcount > 0


def publish_denuncia(conn: sqlite3.Connection, denuncia_id: str) -> Optional[Dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    if denuncia_id.isdigit():
        conn.execute(
            "UPDATE denuncias SET status = 'published', published_at = ? WHERE id = ?",
            (now, int(denuncia_id)),
        )
    else:
        conn.execute(
            "UPDATE denuncias SET status = 'published', published_at = ? WHERE expediente_id = ?",
            (now, denuncia_id),
        )
    conn.commit()
    export_to_json(conn)
    return get_denuncia(conn, denuncia_id)


def unpublish_denuncia(conn: sqlite3.Connection, denuncia_id: str) -> bool:
    if denuncia_id.isdigit():
        cur = conn.execute(
            "UPDATE denuncias SET status = 'draft', published_at = NULL WHERE id = ?",
            (int(denuncia_id),),
        )
    else:
        cur = conn.execute(
            "UPDATE denuncias SET status = 'draft', published_at = NULL WHERE expediente_id = ?",
            (denuncia_id,),
        )
    conn.commit()
    if cur.rowcount > 0:
        export_to_json(conn)
        return True
    return False


def get_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM denuncias").fetchone()[0]
    published = conn.execute("SELECT COUNT(*) FROM denuncias WHERE status = 'published'").fetchone()[0]
    draft = total - published
    merged = conn.execute("SELECT COUNT(*) FROM denuncias WHERE source_count > 1").fetchone()[0]

    by_cat = {}
    for row in conn.execute("SELECT category, COUNT(*) as cnt FROM denuncias GROUP BY category ORDER BY cnt DESC"):
        by_cat[row["category"]] = row["cnt"]

    by_sev = {}
    for row in conn.execute("SELECT severity, COUNT(*) as cnt FROM denuncias GROUP BY severity ORDER BY cnt DESC"):
        by_sev[row["severity"]] = row["cnt"]

    return {
        "total": total,
        "by_category": by_cat,
        "by_severity": by_sev,
        "published_count": published,
        "draft_count": draft,
        "merged_count": merged,
    }


def export_to_json(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM denuncias WHERE status = 'published' ORDER BY scraped_at DESC"
    ).fetchall()

    denuncias = []
    for row in rows:
        d = row_to_dict(row)
        sources = d.get("source_tweets") or []
        denuncias.append({
            "expediente_id": d["expediente_id"],
            "username": d["username"],
            "name": d["display_name"],
            "text": d.get("text", ""),
            "category": d["category"],
            "category_label": CATEGORY_LABELS.get(d["category"], d["category"]),
            "severity": d["severity"],
            "resumen": d.get("resumen"),
            "video_url": d.get("video_url"),
            "images": d.get("images") or [],
            "likes": d.get("likes", 0),
            "retweets": d.get("retweets", 0),
            "replies": d.get("replies", 0),
            "created_at": d.get("created_at", ""),
            "url": d.get("source_url", ""),
            "source_count": d.get("source_count", 1),
            "sources": [
                {"username": s.get("username", ""), "url": s.get("url", "")}
                for s in sources
            ],
        })

    with_video = [d for d in denuncias if d.get("video_url")]
    no_video = [d for d in denuncias if not d.get("video_url")]
    ordered = with_video + no_video

    cats = {}
    for d in denuncias:
        c = d["category"]
        cats[c] = cats.get(c, 0) + 1

    export = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(ordered),
        "categories": cats,
        "denuncias": ordered,
    }

    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    return export


def migrate_from_json(conn: sqlite3.Connection, json_path: Path) -> int:
    if not json_path.exists():
        return 0
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    old_denuncias = data.get("denuncias", [])
    count = 0
    for d in old_denuncias:
        tweet_id = d.get("id", "")
        if not tweet_id:
            # Already in exported format — try to extract tweet_id from URL
            url = d.get("url", "")
            if "/status/" in url:
                tweet_id = url.rstrip("/").split("/")[-1]
            if not tweet_id:
                continue

        result = insert_denuncia(conn, {
            "tweet_id": tweet_id,
            "username": d.get("username", ""),
            "display_name": d.get("display_name") or d.get("name", d.get("username", "")),
            "text": d.get("text", ""),
            "video_url": d.get("video_url"),
            "images": d.get("images", []),
            "retweets": d.get("retweets", 0),
            "likes": d.get("likes", 0),
            "replies": d.get("replies", 0),
            "created_at": d.get("created_at", ""),
            "source_url": d.get("url", ""),
            "scraped_at": d.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "category": d.get("category", "general"),
            "severity": d.get("severity", "info"),
            "status": "published" if d.get("expediente_id") else "draft",
        })
        if result["is_new"]:
            count += 1
    return count
