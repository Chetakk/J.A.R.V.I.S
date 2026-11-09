#!/usr/bin/env bash
# Kills a frozen JARVIS so tools/run_jarvis.sh starts it again. Its watchdog thread touches the heartbeat file every
# 30 s; when the whole process freezes no thread of it can notice, so this watches from outside. Before killing it,
# it asks JARVIS to write every thread's stack to the log (SIGUSR1), to see where it was stuck.
# Usage: freeze_guard.sh <pid> <heartbeat file> <log> [seconds silent = frozen] [seconds between checks]
pid=$1 heart=$2 log=$3 limit=${4:-180} step=${5:-10}
start=$(date +%s)
while kill -0 "$pid" 2>/dev/null; do
  sleep "$step"
  now=$(date +%s)
  beat=$(stat -c %Y "$heart" 2>/dev/null || echo 0)
  allowed=$limit
  if [ "$beat" -lt "$start" ]; then  # no heartbeat yet: still starting up, which can take a while
    beat=$start
    allowed=$((limit * 4))
  fi
  if [ $((now - beat)) -ge "$allowed" ] && kill -0 "$pid" 2>/dev/null; then
    echo "=== $(date '+%F %T') JARVIS frozen: no heartbeat for $((now - beat)) s; its threads, then a restart:" >> "$log"
    kill -USR1 "$pid" 2>/dev/null
    sleep 2
    kill -9 "$pid" 2>/dev/null
    exit 0
  fi
done
