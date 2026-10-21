#!/usr/bin/env bash
#
# Rebuild the combined file, then run Stage 0 enrichment detached (it keeps
# running if the Terminal window closes or the screen sleeps), and show its
# progress live in this window.
#
#   ./run_enrich.sh                    # normal run, progress shown live
#   ./run_enrich.sh --no-watch         # start it and return to the prompt
#   ./run_enrich.sh --skip-embeddings  # any enrich_companies.py flag works
#   ./run_enrich.sh --watch            # show the progress of a running job again
#   ./run_enrich.sh --status           # one line: running or finished, last progress
#   ./run_enrich.sh --stop             # stop a run in progress
#
# Ctrl-C while watching only stops the WATCHING - the job carries on.
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

latest_log() { ls -t "$LOG_DIR"/enrich_*.log 2>/dev/null | head -1; }

# Follow the log until the job ends. Ctrl-C ends only the watching: the job
# was started with nohup in the background, so the interrupt never reaches it.
watch_job() {
  local pid="$1" log="$2"
  echo "  watching   $log   (Ctrl-C stops watching; the job keeps running)"
  echo
  tail -n 20 -f "$log" &
  local tp=$!
  trap 'kill $tp 2>/dev/null; echo; echo "  Stopped watching - enrichment is still running."; echo "  Watch again:  ./run_enrich.sh --watch"; exit 0' INT
  while kill -0 "$pid" 2>/dev/null; do sleep 2; done
  sleep 1
  kill $tp 2>/dev/null || true
  trap - INT
  echo
  if grep -q "^Done in" "$log"; then
    echo "  Finished.  $(grep '^Done in' "$log" | tail -1)"
  else
    echo "  The job ended without 'Done' - see the end of $log"
  fi
}

if [ "${1:-}" = "--watch" ]; then
  if running; then watch_job "$(cat "$PIDFILE")" "$(latest_log)"
  else
    echo "Nothing is running. Last log: $(latest_log)"
    tail -n 5 "$(latest_log)" 2>/dev/null || true
  fi
  exit 0
fi

if [ "${1:-}" = "--status" ]; then
  L=$(latest_log)
  if running; then echo "  running (PID $(cat "$PIDFILE"))"; else echo "  not running"; fi
  [ -n "$L" ] && echo "  $(grep -E '\]|Done in' "$L" | tail -1)" && echo "  log: $L"
  exit 0
fi

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

# our own flag; everything else goes to enrich_companies.py
WATCH=1
ARGS=()
for a in "$@"; do
  if [ "$a" = "--no-watch" ]; then WATCH=0; else ARGS+=("$a"); fi
done

LOG="$LOG_DIR/enrich_$(date +%Y%m%d_%H%M%S).log"

# Enrichment reads companies_combined.xlsx, so rebuild it first from the
# Capital IQ export and the clean source files. Takes a couple of minutes,
# in the foreground - wait for "started" before closing the window.
echo "  building the combined file (combine_companies.py)..."
if ! "$PY" -u combine_companies.py > "$LOG_DIR/combine_last.log" 2>&1; then
  echo "  combine_companies.py failed - see $LOG_DIR/combine_last.log"
  exit 1
fi
grep -E "written|matched" "$LOG_DIR/combine_last.log" | sed 's/^/  /' | tail -6

# -u so the log fills live; python buffers stdout when it is not a terminal,
# which makes tail -f look frozen for minutes at a time.
nohup "$PY" -u enrich_companies.py ${ARGS[@]+"${ARGS[@]}"} > "$LOG" 2>&1 &
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
echo "  watch it   ./run_enrich.sh --watch      status   ./run_enrich.sh --status"
echo "  stop it    ./run_enrich.sh --stop"
echo "  It keeps running if you close this window or the screen sleeps. Keep the lid open."
echo
if [ "$WATCH" = "1" ]; then
  watch_job "$PID" "$LOG"
fi
