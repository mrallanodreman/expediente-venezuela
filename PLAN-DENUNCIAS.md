# Implementation Plan: Loop Denuncias Expediente

**Project:** Expediente Venezuela — Scraper + DB + Slider
**Date:** 2026-07-16
**Scope:** SQLite migration, auto-incremental IDs, keyword categorization, slider HTML

---

## Execution Order

```
Plan 1: SQLite DB Layer + Keyword Categorizer        (Wave 1)
Plan 2: Scraper Migration (JSON → SQLite)            (Wave 1)
Plan 3: JSON Export Script (videos-first)            (Wave 2, depends on 1+2)
Plan 4: Slider HTML Page (denuncias.html)            (Wave 2, depends on 3)
Plan 5: Frontend Integration (index.html updates)    (Wave 3, depends on 4)
```

---

## Plan 1: SQLite DB Layer + Keyword Categorizer

**File:** `scraper/denuncias_db.py` (rewrite)
**Wave:** 1
**Estimated effort:** 30 min

### 1.1 SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS denuncias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id TEXT UNIQUE NOT NULL,    -- EV-2026-0001
    tweet_id TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    text TEXT,
    category TEXT DEFAULT 'general',       -- auto-classified
    video_url TEXT,
    images TEXT,                           -- JSON array
    retweets INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    created_at TEXT,                       -- tweet timestamp
    scraped_at TEXT NOT NULL,              -- when we scraped it
    source_url TEXT NOT NULL,              -- original X URL
    query_used TEXT                        -- which search query found it
);
```

### 1.2 Auto-Incremental Expediente ID Logic

```python
def get_next_expediente_id(conn):
    """Generate next expediente_id: EV-YYYY-NNNN"""
    year = datetime.now().year
    prefix = f"EV-{year}-"
    cursor = conn.execute(
        "SELECT expediente_id FROM denuncias WHERE expediente_id LIKE ? ORDER BY expediente_id DESC LIMIT 1",
        (f"{prefix}%",)
    )
    row = cursor.fetchone()
    if row:
        last_num = int(row[0].split("-")[-1])
        next_num = last_num + 1
    else:
        next_num = 1
    return f"{prefix}{next_num:04d}"
```

### 1.3 Keyword Categorizer

```python
CATEGORY_KEYWORDS = {
    "corrupcion": ["corrupción", "corrupcion", "soborno", "malversación", "malversacion", "coimas", "desfalco", "lavado"],
    "abuso": ["abuso", "maltrato", "violencia", "golpes", "tortura", "amenaza"],
    "extorsion": ["extorsión", "extorsion", "chantaje", "coerce", "paguen", "pague"],
    "desalojo": ["desalojo", "desalojan", "echar", "invasión", "invasion", "ocupación", "ocupacion"],
    "represion": ["represión", "represion", "protesta", "detenido", "preso político", "preso politico", "persecución", "persecucion"],
    "servicios": ["luz", "agua", "gas", "servicio", "falla", "apagón", "apagon", "bache"],
    "salud": ["hospital", "médico", "medico", "medicina", "salud", "muerte", "fallecido"],
}

def classify_denuncia(text):
    """Classify denuncia text into category based on keywords."""
    if not text:
        return "general"
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return category
    return "general"
```

### 1.4 DB Functions to Implement

- `init_db()` — Create table if not exists, return connection
- `insert_denuncia(conn, data)` — Insert with auto ID, dedup by tweet_id
- `get_all_denuncias(conn)` — Return all rows as dicts
- `get_denuncias_by_category(conn, category)` — Filter by category
- `get_next_expediente_id(conn)` — Generate next EV-YYYY-NNNN
- `classify_denuncia(text)` — Keyword-based classification
- `export_to_json(conn)` — Export with videos-first ordering

### 1.5 Verification

- [ ] `python3 -c "from denuncias_db import init_db; conn=init_db(); print('OK')"` runs without error
- [ ] `classify_denuncia("Denuncia por corrupción en el ministerio")` returns `"corrupcion"`
- [ ] `classify_denuncia("Hospital sin medicinas")` returns `"salud"`
- [ ] `classify_denuncia("Tweet random sin palabras clave")` returns `"general"`
- [ ] `get_next_expediente_id(conn)` returns `"EV-2026-0001"` on fresh DB

---

## Plan 2: Scraper Migration (JSON → SQLite)

**File:** `scraper/denuncias_scraper.py` (modify)
**Wave:** 1 (parallel with Plan 1)
**Estimated effort:** 20 min

### 2.1 Changes to denuncias_scraper.py

1. **Replace JSON save with SQLite:**
   - Remove `save_denuncias()` JSON writer
   - Import `denuncias_db` and use `init_db()`, `insert_denuncia()`, `export_to_json()`

2. **Add category classification:**
   - After extracting tweet text, call `classify_denuncia(text)`
   - Store result in the DB row

3. **Auto-generate expediente_id:**
   - Call `get_next_expediente_id(conn)` before insert
   - Store in the `expediente_id` field

4. **Keep intermediate saves:**
   - `_save_intermediate()` now writes to SQLite + exports JSON snapshot

5. **Sorting in export:**
   - Videos first (sorted by scraped_at DESC)
   - Then no-video (sorted by scraped_at DESC)

### 2.2 Updated save_denuncias Flow

```python
def save_results(conn, all_tweets, seen_ids):
    """Save scraped tweets to SQLite and export JSON."""
    for t in all_tweets:
        expediente_id = get_next_expediente_id(conn)
        category = classify_denuncia(t.get("text", ""))
        data = {
            "expediente_id": expediente_id,
            "tweet_id": t["id"],
            "username": t["username"],
            "display_name": t["name"],
            "text": t.get("text", ""),
            "category": category,
            "video_url": t.get("video_url"),
            "images": json.dumps(t.get("images", [])),
            "retweets": t.get("retweets", 0),
            "likes": t.get("likes", 0),
            "replies": t.get("replies", 0),
            "created_at": t.get("created_at", ""),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source_url": t.get("url", ""),
            "query_used": t.get("query_used", ""),
        }
        insert_denuncia(conn, data)
    
    # Export JSON snapshot
    export_to_json(conn)
```

### 2.3 Verification

- [ ] Run scraper: `python3 denuncias_scraper.py`
- [ ] Check SQLite: `sqlite3 denuncias.db "SELECT expediente_id, category FROM denuncias LIMIT 5"`
- [ ] Verify sequential IDs: EV-2026-0001, EV-2026-0002, ...
- [ ] Verify categories assigned: no row has NULL category

---

## Plan 3: JSON Export Script

**File:** `scraper/export_json.py` (new)
**Wave:** 2 (after Plan 1+2)
**Estimated effort:** 15 min

### 3.1 Export Format

```json
{
  "updated_at": "2026-07-16T12:00:00Z",
  "count": 25,
  "categories": {
    "corrupcion": 8,
    "abuso": 5,
    "represion": 3,
    "general": 9
  },
  "denuncias": [
    {
      "expediente_id": "EV-2026-0001",
      "tweet_id": "...",
      "username": "...",
      "name": "...",
      "text": "...",
      "category": "corrupcion",
      "category_label": "Corrupción",
      "video_url": "...",
      "images": [],
      "likes": 123,
      "retweets": 45,
      "replies": 12,
      "created_at": "...",
      "url": "https://x.com/..."
    }
  ]
}
```

### 3.2 Category Labels Map

```python
CATEGORY_LABELS = {
    "corrupcion": "Corrupción",
    "abuso": "Abuso",
    "extorsion": "Extorsión",
    "desalojo": "Desalojo",
    "represion": "Represión",
    "servicios": "Servicios",
    "salud": "Salud",
    "general": "General",
}
```

### 3.3 Ordering Logic

```python
def export_to_json(conn):
    """Export denuncias from SQLite to JSON with videos-first ordering."""
    rows = conn.execute(
        "SELECT * FROM denuncias ORDER BY scraped_at DESC"
    ).fetchall()
    
    # Split: videos first, then no-video
    with_video = [r for r in rows if r["video_url"]]
    no_video = [r for r in rows if not r["video_url"]]
    
    # Both sorted by scraped_at DESC (already from query)
    ordered = with_video + no_video
    
    # Count categories
    categories = {}
    for r in ordered:
        cat = r["category"]
        categories[cat] = categories.get(cat, 0) + 1
    
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(ordered),
        "categories": categories,
        "denuncias": [dict_to_slide(r) for r in ordered],
    }
    
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    return output
```

### 3.4 Verification

- [ ] `python3 export_json.py` produces valid JSON
- [ ] First item in `denuncias` array has a `video_url` (if any exist)
- [ ] `categories` object sums to `count`
- [ ] Each item has `expediente_id` in EV-YYYY-NNNN format
- [ ] Each item has `category_label` matching the category

---

## Plan 4: Slider HTML Page

**File:** `denuncias.html` (new, root level)
**Wave:** 2 (parallel with Plan 3)
**Estimated effort:** 45 min

### 4.1 Page Structure

```
denuncias.html
├── Header (back to index, title)
├── Filter bar (by category, search)
├── Slider container
│   ├── Slide: Expediente ID eyebrow
│   ├── Slide: Category tag (colored pill)
│   ├── Slide: User info (avatar initial, name, @username)
│   ├── Slide: Denuncia text
│   ├── Slide: Media (video or image)
│   ├── Slide: Stats (likes, retweets, replies)
│   ├── Slide: Source link
│   └── Slide: Timestamp (relative)
├── Pagination dots
└── Keyboard navigation
```

### 4.2 Category Color Mapping (CSS Variables)

```css
:root {
  --cat-corrupcion: #8b2500;
  --cat-abuso: #a63200;
  --cat-extorsion: #c9a227;
  --cat-desalojo: #6b6352;
  --cat-represion: #e63946;
  --cat-servicios: #3a86ff;
  --cat-salud: #00e87b;
  --cat-general: #7a7568;
}
```

### 4.3 Slide HTML Template

```html
<div class="slide" data-category="corrupcion">
  <div class="slide-header">
    <span class="expediente-id">EV-2026-0001</span>
    <span class="category-tag" style="background:var(--cat-corrupcion)">Corrupción</span>
  </div>
  <div class="slide-user">
    <div class="avatar-initial">J</div>
    <div class="user-info">
      <span class="display-name">Juan Pérez</span>
      <span class="username">@juanperez</span>
    </div>
  </div>
  <div class="slide-text">
    Denuncia text goes here...
  </div>
  <div class="slide-media">
    <!-- video or image -->
  </div>
  <div class="slide-stats">
    <span class="stat">❤ 123</span>
    <span class="stat">🔄 45</span>
    <span class="stat">💬 12</span>
  </div>
  <div class="slide-footer">
    <a href="https://x.com/..." target="_blank" class="source-link">
      Ver denuncia original en X →
    </a>
    <span class="timestamp">hace 2h</span>
  </div>
</div>
```

### 4.4 Relative Time Function

```javascript
function timeAgo(dateStr) {
  const now = new Date();
  const then = new Date(dateStr);
  const seconds = Math.floor((now - then) / 1000);
  
  if (seconds < 60) return 'hace unos segundos';
  if (seconds < 3600) return `hace ${Math.floor(seconds/60)}m`;
  if (seconds < 86400) return `hace ${Math.floor(seconds/3600)}h`;
  if (seconds < 604800) return `hace ${Math.floor(seconds/86400)}d`;
  return then.toLocaleDateString('es-VE');
}
```

### 4.5 Slider Controls

- Arrow keys (←/→) for navigation
- Click/tap for next/prev
- Swipe on mobile
- Category filter buttons at top
- Current position indicator (1/25)

### 4.6 Design Principles

- Dark theme matching existing site (`--bg: #0a0a0a`)
- Same fonts: Courier Prime, Source Serif 4, Inter
- Category tags as colored pills with border
- Avatar: first letter of display name in a circle
- Video autoplay (muted) with controls
- Responsive: full-width on mobile, centered card on desktop

### 4.7 Verification

- [ ] Open `denuncias.html` in browser
- [ ] Slides show expediente IDs (EV-2026-0001 etc.)
- [ ] Category tags display with correct colors
- [ ] Arrow keys navigate between slides
- [ ] Category filter buttons work
- [ ] Videos autoplay on slide entry
- [ ] Mobile responsive layout works

---

## Plan 5: Frontend Integration

**File:** `index.html` (modify)
**Wave:** 3 (after Plan 4)
**Estimated effort:** 20 min

### 5.1 Changes to index.html

1. **Add "Denuncias" nav link:**
   ```html
   <a href="denuncias.html">Denuncias</a>
   ```

2. **Update expedientes grid to load from JSON:**
   - Add `<script>` that fetches `scraper/data/denuncias.json`
   - Dynamically populate cards with real data
   - Show category tags with colors
   - Show expediente IDs

3. **Add category colors to CSS:**
   ```css
   --cat-corrupcion: #8b2500;
   --cat-abuso: #a63200;
   /* ... etc */
   ```

4. **Update stats section:**
   - Load total count from JSON
   - Load category counts from JSON

### 5.2 Verification

- [ ] `index.html` loads denuncias from JSON
- [ ] Cards show real data with category tags
- [ ] "Denuncias" link in nav works
- [ ] Stats update dynamically

---

## File Summary

| File | Action | Wave |
|------|--------|------|
| `scraper/denuncias_db.py` | Rewrite | 1 |
| `scraper/denuncias_scraper.py` | Modify | 1 |
| `scraper/export_json.py` | Create | 2 |
| `denuncias.html` | Create | 2 |
| `index.html` | Modify | 3 |
| `scraper/data/denuncias.json` | Auto-generated | 2 |
| `scraper/data/denuncias.db` | Auto-created | 1 |

---

## Dependencies

```
Plan 1 (DB) ──┐
               ├──→ Plan 3 (Export) ──→ Plan 5 (Frontend)
Plan 2 (Scraper) ┘         │
                           └──→ Plan 4 (Slider HTML)
```

---

## Risk Mitigation

1. **Existing data migration:** The current `denuncias.json` has 20 records without expediente IDs. Plan 1 includes a migration function to import existing data and assign sequential IDs.

2. **Playwright cookie expiry:** The scraper depends on Ferdium cookies. If expired, the scraper will fail gracefully and log the error.

3. **Large dataset performance:** SQLite handles 10K+ records efficiently. No performance concerns for this use case.

4. **Mobile slider:** Touch events need careful implementation. Using CSS scroll-snap as fallback.

---

## Migration Script (Existing Data)

Add to `denuncias_db.py`:

```python
def migrate_from_json(conn, json_path):
    """Import existing denuncias.json into SQLite."""
    if not Path(json_path).exists():
        return 0
    
    data = json.loads(Path(json_path).read_text())
    count = 0
    
    for d in data.get("denuncias", []):
        try:
            expediente_id = get_next_expediente_id(conn)
            category = classify_denuncia(d.get("text", ""))
            
            conn.execute("""
                INSERT OR IGNORE INTO denuncias 
                (expediente_id, tweet_id, username, display_name, text, category,
                 video_url, images, retweets, likes, replies, created_at, 
                 scraped_at, source_url, query_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                expediente_id,
                d["id"],
                d["username"],
                d["name"],
                d.get("text", ""),
                category,
                d.get("video_url"),
                json.dumps(d.get("images", [])),
                d.get("retweets", 0),
                d.get("likes", 0),
                d.get("replies", 0),
                d.get("created_at", ""),
                datetime.now(timezone.utc).isoformat(),
                d.get("url", ""),
                "",
            ))
            count += 1
        except Exception as e:
            print(f"Migration error: {e}")
    
    conn.commit()
    return count
```

---

## Next Steps

1. Execute Plan 1: Create `denuncias_db.py` with full SQLite layer
2. Execute Plan 2: Update scraper to use SQLite
3. Run migration on existing data
4. Execute Plan 3: Create export script
5. Execute Plan 4: Create slider HTML
6. Execute Plan 5: Update frontend
7. Test end-to-end: scrape → export → display
