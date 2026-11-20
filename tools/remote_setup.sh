#!/bin/bash
# One-time setup for controlling JARVIS from the phone (system/remote.py). Run: sudo tools/remote_setup.sh
#  1. JARVIS may (re)start RustDesk's service without a password: one sudoers rule, for exactly that command.
#  2. The phone page gets HTTPS at https://<this pc>.<tailnet>.ts.net (Tailscale serve, Tailscale devices only):
#     Android installs a page as an app only over HTTPS. Needs HTTPS turned on once in the Tailscale admin
#     console (DNS page, "Enable HTTPS").
#  3. If tools' Tailscale lock is installed (only the phone may connect), the phone may use port 443 too.
set -e
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0"; exit 1; }
USER_NAME=${SUDO_USER:-biztelai}
PORT=8765
IP=$(tailscale ip -4 | head -1)

echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart rustdesk, /bin/systemctl restart rustdesk" \
  > /etc/sudoers.d/jarvis-rustdesk
chmod 440 /etc/sudoers.d/jarvis-rustdesk
visudo -cf /etc/sudoers.d/jarvis-rustdesk >/dev/null && echo "1. JARVIS can restart RustDesk's service."

if tailscale serve --bg --https=443 "http://$IP:$PORT" >/dev/null 2>&1; then
  echo "2. HTTPS on: https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')/"
else
  echo "2. HTTPS not on yet: open https://login.tailscale.com/admin/dns, click Enable HTTPS, then run this again."
fi

LOCK=/usr/local/sbin/jarvis-tailscale-lock
if [ -f "$LOCK" ] && ! grep -q "8765,443" "$LOCK"; then
  sed -i 's|-p tcp --dport 8765 -j ACCEPT|-p tcp -m multiport --dports 8765,443 -j ACCEPT|' "$LOCK"
  systemctl restart jarvis-tailscale-lock && echo "3. The Tailscale lock lets the phone use HTTPS too."
fi
