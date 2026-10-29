#!/usr/bin/env bash
# Keeps JARVIS running in the background: restarts it if it crashes, stops when you choose
# "Quit JARVIS" in the tray menu. Output goes to ~/.local/share/jarvis/jarvis.log.
cd "$(dirname "$0")/.." || exit 1
LOG="$HOME/.local/share/jarvis/jarvis.log"
mkdir -p "$(dirname "$LOG")"

crashes=()
while true; do
  if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 5000000 ]; then
    mv "$LOG" "$LOG.old"
  fi
  echo "=== $(date '+%F %T') starting JARVIS" >> "$LOG"
  python3 -u jarvis.py < /dev/null >> "$LOG" 2>&1
  code=$?
  # 0 = quit from the tray menu, 3 = another JARVIS is already running.
  if [ "$code" -eq 0 ] || [ "$code" -eq 3 ]; then
    exit 0
  fi
  # 4 = "restart yourself": not a crash.
  if [ "$code" -eq 4 ]; then
    echo "=== $(date '+%F %T') restarting JARVIS as asked" >> "$LOG"
    continue
  fi
  echo "=== $(date '+%F %T') JARVIS stopped unexpectedly (exit $code); restarting" >> "$LOG"
  # Give up if it keeps crashing (5 times in 2 minutes) instead of looping forever.
  now=$(date +%s)
  crashes=($(for t in "${crashes[@]}" "$now"; do [ $((now - t)) -lt 120 ] && echo "$t"; done))
  if [ "${#crashes[@]}" -ge 5 ]; then
    echo "=== giving up after 5 crashes in 2 minutes; see the errors above" >> "$LOG"
    notify-send "JARVIS" "JARVIS keeps crashing; see ~/.local/share/jarvis/jarvis.log" 2>/dev/null
    exit 1
  fi
  sleep 3
done
