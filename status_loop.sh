#!/bin/bash
# Check status of the autonomous loop
cd /home/pctorre/expediente-venezuela

echo "=== EXPEDIENTE VENEZUELA - LOOP STATUS ==="
echo ""

# Check if loop is running
if [ -f scraper/data/loop.pid ]; then
    PID=$(cat scraper/data/loop.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "✅ Loop is RUNNING (PID: $PID)"
    else
        echo "❌ Loop is STOPPED (stale PID: $PID)"
    fi
else
    echo "❌ Loop has never been started"
fi

echo ""

# Show last 10 lines of log
echo "=== LAST 10 LOG ENTRIES ==="
if [ -f scraper/data/loop.log ]; then
    tail -10 scraper/data/loop.log
else
    echo "No log file found"
fi

echo ""

# Show DB stats
echo "=== DATABASE STATS ==="
python3 -c "
import sys
sys.path.insert(0, 'scraper')
from denuncias_db import init_db, get_stats
conn = init_db()
stats = get_stats(conn)
conn.close()
print(f'Total: {stats.get(\"total\", 0)}')
print(f'Published: {stats.get(\"published_count\", 0)}')
print(f'Drafts: {stats.get(\"draft_count\", 0)}')
print(f'Categories: {stats.get(\"by_category\", {})}')
"

echo ""

# Show video count
echo "=== VIDEOS ==="
ls -1 /mnt/sdb1/MoneyMakers_webops/Web1/expediente-venezuela/media/videos/*.mp4 2>/dev/null | wc -l
echo "local video files"
du -sh /mnt/sdb1/MoneyMakers_webops/Web1/expediente-venezuela/media/videos/ 2>/dev/null
