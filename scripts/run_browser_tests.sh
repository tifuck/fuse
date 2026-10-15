#!/bin/bash
# Run the Playwright browser suites (tests/*.browser.cjs), each against its own
# fresh isolated fixture server (tests/chat_browser_fixture.py: disposable
# MariaDB schema, mocked model, loopback port 8099). Never touches fuse-dev.
#
#   NODE_PATH=/path/to/node_modules scripts/run_browser_tests.sh [tests/x.browser.cjs ...]
#
# Needs the Node "playwright" package (npm install playwright, or on
# NODE_PATH) and its Chromium (npx playwright install chromium).
# Logs go to $LOG_DIR (default /tmp/fuse-browser-tests).
set -u
cd "$(dirname "$0")/.."
LOG_DIR=${LOG_DIR:-/tmp/fuse-browser-tests}
PORT=${FUSE_BROWSER_PORT:-8099}
PYTHON=${PYTHON:-/root/fuse/venv/bin/python}
mkdir -p "$LOG_DIR"
export FUSE_BROWSER_ORIGIN="http://127.0.0.1:$PORT" FUSE_BROWSER_PORT="$PORT"
files=("$@")
[ ${#files[@]} -eq 0 ] && files=(tests/*.browser.cjs)
failed=0
for f in "${files[@]}"; do
  name=$(basename "$f")
  FUSE_CHAT_TEST_MODE=1 "$PYTHON" tests/chat_browser_fixture.py > "$LOG_DIR/fixture-$name.log" 2>&1 &
  server=$!
  for _ in $(seq 1 60); do curl -s -o /dev/null "$FUSE_BROWSER_ORIGIN/static/app.css" && break; sleep 0.5; done
  if timeout 900 node "$f" > "$LOG_DIR/$name.log" 2>&1; then echo "PASS $f"; else echo "FAIL $f (see $LOG_DIR/$name.log)"; failed=1; fi
  kill "$server" 2>/dev/null; wait "$server" 2>/dev/null
done
exit $failed
