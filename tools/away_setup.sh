#!/bin/bash
# Keeps this PC reachable while you're away. Run: sudo tools/away_setup.sh
#  1. Never sleeps: suspend/hibernate are masked, so nothing (idle, power button, a stray click) can put it to sleep.
#  2. After a power cut: checks auto-login and JARVIS's autostart (the Orin NX dev kit powers on by itself
#     when power comes back; there is no BIOS option for it).
#  3. No Tailscale lock (any tailnet device may connect); removes it if installed. SSH takes keys only,
#     once ~/.ssh/authorized_keys has one.
# Undo no-sleep: sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
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

# No Tailscale lock: every device in the tailnet may connect; SSH keys and the JARVIS page key guard it.
if systemctl is-enabled jarvis-tailscale-lock >/dev/null 2>&1; then
  systemctl disable --now jarvis-tailscale-lock >/dev/null 2>&1
  echo "3. Tailscale lock removed."
fi
# Key-only SSH, but never before a key is in place: that would lock you out.
if grep -qE '^(ssh-|ecdsa-)' "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null; then
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\n' \
    > /etc/ssh/sshd_config.d/10-jarvis-keys-only.conf
  sshd -t && systemctl reload ssh && echo "4. SSH takes keys only now."
else
  echo "4. SSH still takes passwords: add your phone's public key to $HOME_DIR/.ssh/authorized_keys, then run this again."
fi
