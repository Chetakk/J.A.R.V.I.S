#!/bin/bash
# Keeps this PC reachable while you're away. Run: sudo tools/away_setup.sh [laptop's Tailscale name]
#  1. Never sleeps: suspend/hibernate are masked, so nothing (idle, power button, a stray click) can put it to sleep.
#  2. After a power cut: checks auto-login and JARVIS's autostart (the Orin NX dev kit powers on by itself
#     when power comes back; there is no BIOS option for it).
#  3. Tailscale lock: only the phone (and the laptop, if named) may reach this PC over Tailscale: the JARVIS
#     page (8765, 443) and SSH (22). SSH takes keys only, once ~/.ssh/authorized_keys has one.
# Undo the lock: sudo systemctl disable --now jarvis-tailscale-lock. Undo no-sleep: sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
set -e
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0 $*"; exit 1; }
USER_NAME=${SUDO_USER:-biztelai}
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)

systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1
echo "1. Sleep is off for good (suspend and hibernate masked)."

grep -q "^AutomaticLogin=$USER_NAME" /etc/gdm3/custom.conf && grep -q "^AutomaticLoginEnable=True" /etc/gdm3/custom.conf \
  && echo "2. Auto-login is on." || echo "2. WARNING: auto-login is off (Settings > Users > Automatic Login)."
[ -f "$HOME_DIR/.config/autostart/jarvis.desktop" ] && echo "   JARVIS starts with the desktop." \
  || echo "   WARNING: ~/.config/autostart/jarvis.desktop is missing."

PHONE=$(tailscale ip -4 oneplus-nord)
ALLOWED="$PHONE"
if [ -n "$1" ]; then
  LAPTOP=$(tailscale ip -4 "$1") || { echo "No Tailscale device named $1 (see: tailscale status)"; exit 1; }
  ALLOWED="$ALLOWED $LAPTOP"
fi

cat > /usr/local/sbin/jarvis-tailscale-lock <<EOF
#!/bin/bash
for ipt in iptables ip6tables; do
  \$ipt -N JARVIS-TS 2>/dev/null || \$ipt -F JARVIS-TS
  \$ipt -A JARVIS-TS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT   # replies to what this PC started
  if [ \$ipt = iptables ]; then
    for ip in $ALLOWED; do \$ipt -A JARVIS-TS -s \$ip -p tcp -m multiport --dports 8765,443,22 -j ACCEPT; done
  fi
  \$ipt -A JARVIS-TS -j DROP
  while \$ipt -D INPUT -i tailscale0 -j JARVIS-TS 2>/dev/null; do :; done
  \$ipt -I INPUT 1 -i tailscale0 -j JARVIS-TS   # first, ahead of Tailscale's own rules
done
EOF
chmod 755 /usr/local/sbin/jarvis-tailscale-lock

cat > /etc/systemd/system/jarvis-tailscale-lock.service <<'EOF'
[Unit]
Description=Only the phone (and laptop) may reach this PC over Tailscale: JARVIS page and SSH
After=tailscaled.service
PartOf=tailscaled.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sleep 5
ExecStart=/usr/local/sbin/jarvis-tailscale-lock
ExecStop=/bin/sh -c 'for t in iptables ip6tables; do $t -D INPUT -i tailscale0 -j JARVIS-TS; $t -F JARVIS-TS; $t -X JARVIS-TS; done; true'

[Install]
WantedBy=multi-user.target tailscaled.service
EOF
systemctl daemon-reload
systemctl enable jarvis-tailscale-lock >/dev/null 2>&1
systemctl restart jarvis-tailscale-lock
echo "3. Tailscale lock on: only $ALLOWED may connect (JARVIS page and SSH)."

# Key-only SSH, but never before a key is in place: that would lock you out.
if grep -qE '^(ssh-|ecdsa-)' "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null; then
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\n' \
    > /etc/ssh/sshd_config.d/10-jarvis-keys-only.conf
  sshd -t && systemctl reload ssh && echo "   SSH takes keys only now."
else
  echo "   SSH still takes passwords: add your phone's public key to $HOME_DIR/.ssh/authorized_keys, then run this again."
fi
