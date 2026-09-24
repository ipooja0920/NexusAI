#!/usr/bin/env bash
#
# Run Stage 0 enrichment detached, so the Terminal window can be closed.
#
#   ./run_enrich.sh                    # normal run
#   ./run_enrich.sh --skip-embeddings  # any enrich_companies.py flag works
#   ./run_enrich.sh --stop             # stop a run in progress
#
# The LID still has to stay open. Closing it sleeps the Mac and pauses the
# job until you open it again - no command can override that. Plug in an
# external display if you need the lid shut.
#
set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
PIDFILE="$LOG_DIR/enrich.pid"

# The PID file holds PYTHON's pid. That is the whole reason python is
# launched directly below, with caffeinate merely WATCHING it via -w.
#
# The obvious arrangement - `nohup caffeinate -i python ... &` - stores
# CAFFEINATE's pid instead, because that is what bash backgrounds; python
# runs as its child. Killing the stored pid then kills the wrapper and
# leaves python orphaned and still running. That happened once for real:
# the job carried on for hours after a "kill" that reported no error, with
# nothing left holding the Mac awake.
running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}

if [ "${1:-}" = "--stop" ]; then
  if running; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    sleep 1
    if running; then kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true; fi
    rm -f "$PIDFILE"
    echo "Stopped."
  else
    rm -f "$PIDFILE"
    echo "Nothing was running."
  fi
  exit 0
fi

# One at a time. Two runs would fight over the same caches and the same
# output workbook, and the loser would write a half-built file.
if running; then
  echo "Already running (PID $(cat "$PIDFILE"))."
  echo "Watch it:  tail -f $(ls -t "$LOG_DIR"/enrich_*.log 2>/dev/null | head -1)"
  echo "Stop it :  ./run_enrich.sh --stop"
  exit 1
fi
rm -f "$PIDFILE"

# Prefer the project's own venv, so this works whether or not the venv is
# active in the calling shell. A detached job outlives the shell that
# started it, so relying on the caller having sourced activate is a good
# way to get a ModuleNotFoundError twenty minutes in.
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

LOG="$LOG_DIR/enrich_$(date +%Y%m%d_%H%M%S).log"

# -u so the log fills live; python buffers stdout when it is not a terminal,
# which makes tail -f look frozen for minutes at a time.
nohup "$PY" -u enrich_companies.py "$@" > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

# caffeinate -w holds the Mac awake until that pid exits, then exits itself.
if command -v caffeinate >/dev/null 2>&1; then
  nohup caffeinate -i -w "$PID" >/dev/null 2>&1 &
else
  echo "note: caffeinate not found - the machine may sleep on its own."
fi

echo
echo "  started    PID $PID"
echo "  log        $LOG"
echo
echo "  watch it   tail -f $LOG"
echo "  check it   kill -0 $PID 2>/dev/null && echo running || echo finished"
echo "  stop it    ./run_enrich.sh --stop"
echo
echo "  Safe to close this window. Keep the lid open."
echo
