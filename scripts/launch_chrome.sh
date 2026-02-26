#!/bin/bash
# Launch Chrome with remote debugging enabled for ChatGPT MCP automation.
# Uses a separate user-data-dir to avoid interfering with the user's main Chrome.
#
# Usage:
#   bash scripts/launch_chrome.sh          # default port 9222
#   bash scripts/launch_chrome.sh 9333     # custom port

set -euo pipefail

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DATA_DIR="${CHROME_USER_DATA_DIR:-$HOME/Library/Application Support/Google/Chrome-Automation}"
PORT="${1:-9222}"

if [ ! -x "$CHROME" ]; then
    echo "Chrome not found at $CHROME"
    echo "Install with: brew install --cask google-chrome"
    exit 1
fi

echo "Launching Chrome on CDP port $PORT"
echo "Profile: $DATA_DIR"
echo ""
echo "First run? Log into chatgpt.com in the browser window that opens."

"$CHROME" \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$DATA_DIR" \
    --no-first-run \
    --no-default-browser-check \
    "https://chatgpt.com/" &

echo "Chrome PID: $!"
