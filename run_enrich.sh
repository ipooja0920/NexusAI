#!/usr/bin/env bash
#
# Run Stage 0 enrichment detached, so the Terminal window can be closed.
#
#   ./run_enrich.sh                    # normal run
#   ./run_enrich.sh --skip-embeddings  # any enrich_companies.py flag works
#
# What it does:
#   caffeinate -i   keeps the Mac from idle-sleeping while the job runs
#   nohup ... &     detaches it, so closing Terminal (or logging out)
#                   does not kill it
#
# The LID still has to stay open. Closing it sleeps the Mac and pauses the
# job until you open it again - no command can override that. Plug in an
# external display if you need the lid shut.
#
set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/enrich_$(date +%Y%m%d_%H%M%S).log"
PIDFILE="$LOG_DIR/enrich.pid"

# One at a time. Two runs would fight over the same caches and the same
# output workbook, and the loser would write a half-built file.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (PID $(cat "$PIDFILE"))."
  echo "Watch it:  tail -f $(ls -t "$LOG_DIR"/enrich_*.log | head -1)"
  echo "Stop it :  kill $(cat "$PIDFILE")"
  exit 1
fi

# Prefer the project's own venv, so the script works whether or not the
# venv is active in the calling shell. A detached job outlives the shell
# that started it, so relying on the caller having sourced activate is a
# good way to get a ModuleNotFoundError twenty minutes in.
if [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY=$(command -v python3 || command -v python)
fi
if [ -z "$PY" ]; then echo "No python found."; exit 1; fi

if ! "$PY" -c "import pandas, openpyxl" 2>/dev/null; then
  echo "$PY cannot import pandas/openpyxl - check the venv:"
  echo "  python3 -m venv venv && source venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

# caffeinate is macOS-only; skip it elsewhere rather than failing.
if command -v caffeinate >/dev/null 2>&1; then
  KEEP_AWAKE=(caffeinate -i)
else
  KEEP_AWAKE=()
  echo "note: caffeinate not found - the machine may sleep on its own."
fi

nohup "${KEEP_AWAKE[@]}" "$PY" -u enrich_companies.py "$@" > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

echo
echo "  started    PID $PID"
echo "  log        $LOG"
echo
echo "  watch it   tail -f $LOG"
echo "  check it   kill -0 $PID && echo running || echo finished"
echo "  stop it    kill $PID"
echo
echo "  Safe to close this window. Keep the lid open."
echo
