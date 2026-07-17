#!/bin/bash
# Run the autonomous loop in the background
cd /home/pctorre/expediente-venezuela

# Kill any existing loop
pkill -f "autonomous_loop.py" 2>/dev/null

# Start new loop in background
nohup python3 scraper/autonomous_loop.py > scraper/data/loop_output.log 2>&1 &
PID=$!

echo "Loop started with PID: $PID"
echo $PID > scraper/data/loop.pid
echo "Logs: scraper/data/loop.log"
echo "Output: scraper/data/loop_output.log"
