#!/usr/bin/env bash
# JARVIS manual-command terminal. Every command JARVIS won't run itself shows up here
# pre-typed and editable. Nothing runs until you press Enter.
fifo="$1"; pidfile="$2"

exec 3<>"$fifo"            # read+write open: never blocks, keeps the pipe alive
echo $$ > "$pidfile"
trap 'rm -f "$pidfile"' EXIT
trap 'echo " (skipped)"' INT

printf '\e[1;36mJARVIS manual terminal\e[0m - commands JARVIS will not run itself appear here.\n'
while IFS=$'\t' read -r -u 3 reason b64; do
  cmd=$(printf '%s' "$b64" | base64 -d)
  printf '\n\e[1;33m⚠  %s\e[0m\n' "$reason"
  printf '\e[2mEnter = run   edit it first if needed   clear the line + Enter or Ctrl+C = skip\e[0m\n'
  if read -e -i "$cmd" -p '$ ' line && [ -n "$line" ]; then
    history -s "$line"
    eval "$line" 3<&-
    printf '\e[2m(exit %s)\e[0m\n' "$?"
  fi
done
