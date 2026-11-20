"""Control JARVIS from anywhere: a small web page on the Tailscale address, and a Google Chat space.

Web: http://<tailscale ip>:8765/?key=<key> from any of your Tailscale devices (phone app signed in to the same
account). It only listens on the Tailscale address, never the LAN or the internet, and also needs the key in
~/.local/share/jarvis/remote_key (made on the first start, printed in the log).

Chat: make a Google Chat space named "JARVIS" with only you in it; what you write there is done as a request and
JARVIS's answers are posted back. Works from any browser signed in to Google. Checked every 5 s. With the space's
webhook URL in ~/.local/share/jarvis/chat_webhook, posts come from a "JARVIS" app (so the phone notifies you), and
everything JARVIS says while you're away (screen locked, or 10 min untouched) is posted too; without it they're
posted as you ("JARVIS: ...") and only answers to Chat requests.

Cards (jarvis.show) go to both: drawn on the page (markdown, tables, buttons that say their phrase), and posted to
Chat as text with "Reply with: ..." for the buttons. Drawn blocks (weather, rings, orb) and images are left out.

Requests come in as kind "remote" (yes/no questions wait longer for them, jarvis._answer). Settings: remote: web,
port, chat_space."""
import hmac
import json
import os
import secrets
import subprocess
import threading
import re
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from system import google_account as google

KEY_FILE = os.path.expanduser("~/.local/share/jarvis/remote_key")
PREFIX = "JARVIS: "  # marks its own replies in the Chat space (they're posted as you)
FORWARD_FOR = 120  # seconds after a Chat request that what JARVIS says is posted back
WEBHOOK_FILE = os.path.expanduser("~/.local/share/jarvis/chat_webhook")
AWAY_AFTER = 600  # seconds without touching the PC that count as away
LAST_WEB = [0.0]  # when the page last sent a request: you're on the page, so no Chat notifications meanwhile
PAGE_FILE = os.path.join(os.path.dirname(__file__), "remote.html")  # read on each load: edits show on a refresh
CARDS = deque(maxlen=10)  # (when, card) of the cards shown lately, for the page and Chat


def start(jarvis):
    cfg = jarvis.settings.get("remote") or {}
    if cfg.get("web", True):
        threading.Thread(target=serve, args=(jarvis, cfg.get("port", 8765)), name="remote-web", daemon=True).start()
    if cfg.get("chat_space", "JARVIS"):
        threading.Thread(target=watch_chat, args=(jarvis, cfg.get("chat_space", "JARVIS")), name="remote-chat",
                         daemon=True).start()


def said_since(jarvis, since):
    """[(when, text)] JARVIS said after `since` (its speech history keeps the last 12)."""
    return [(at, text) for at, text in list(jarvis.speaker.history) if at > since]


def shown(card):
    """jarvis.show: keep the parts a phone can show (title, markdown text and columns, buttons)."""
    text = "\n\n".join(str(t) for t in [card.get("text")] + list(card.get("columns") or []) if t)
    choices = [c if isinstance(c, str) else {"label": c.get("label") or c.get("do") or "", "do": c.get("do") or
               c.get("label") or ""} for c in card.get("choices") or [] if isinstance(c, (str, dict))]
    if text or choices:
        CARDS.append((time.time(), {"title": str(card.get("title") or ""), "text": text, "choices": choices,
                                    "id": card.get("id")}))


def face(jarvis):
    """What the desktop orb shows now: its state (idle, listening, thinking, speaking, claude, off) and the looks its
    eyes have on, [(mood, strength, style)] (ui/window.py eye_look; the page draws them the same way)."""
    ui = jarvis.ui
    state = ((getattr(ui, "_shown", None) or ("idle",))[0]) if ui else "idle"
    layers = [(m, round(k, 3), style if isinstance(style, (str, int, float)) else None)
              for m, k, style in (getattr(getattr(ui, "bar", None), "layers", None) or []) if m and m != "watching"]
    return {"state": state, "layers": layers}


def cards_since(since):
    return [(at, card) for at, card in list(CARDS) if at > since]


def chat_text(card):
    """A card as Google Chat text: *bold* title, tables and rows as "a · b" lines, links as <url|text>."""
    lines = ["*%s*" % card["title"]] if card["title"] else []
    for line in card["text"].splitlines():
        line = line.strip()
        if line.startswith("```") or re.match(r"^\|?[\s:|-]+\|?$", line) and "-" in line:
            continue  # fences and table rulers
        if "|" in line:
            line = " · ".join(c.strip() for c in line.strip("|").split("|") if c.strip())
        line = re.sub(r"^#+\s*(.+)", r"*\1*", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", line)
        line = line.replace("{ok}", "\u2705").replace("{warn}", "\u26a0\ufe0f").replace("{bad}", "\u274c")
        line = re.sub(r"\{[a-z]+(?::[^}]*)?\}|\[\[|\]\]", "", line).strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    labels = [c if isinstance(c, str) else c["do"] for c in card["choices"]]
    if labels:
        lines.append("Reply with: " + " / ".join(labels))
    return "\n".join(lines).strip()[:3500]


# --- web page (Tailscale) ---

def key():
    try:
        with open(KEY_FILE) as f:
            return f.read().strip()
    except OSError:
        made = secrets.token_urlsafe(18)
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(made)
        return made


def tailscale_ip():
    for _ in range(30):  # at boot Tailscale can come up after JARVIS
        try:
            ip = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
            if ip:
                return ip[0]
        except (OSError, subprocess.TimeoutExpired):
            return None
        time.sleep(10)
    return None


def serve(jarvis, port):
    ip, secret = tailscale_ip(), key()
    if not ip:
        print("[remote] Tailscale isn't up: no remote web page")
        return

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _allowed(self):
            got = parse_qs(urlparse(self.path).query).get("key", [""])[0] or self.headers.get("X-Key", "")
            if hmac.compare_digest(got.encode(), secret.encode()):
                return True
            self._send(403, "text/plain", b"Wrong or missing key.")
            return False

        def _send(self, code, kind, body):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if not self._allowed():
                return
            if url.path == "/":
                with open(PAGE_FILE, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif url.path == "/log":
                since = float(parse_qs(url.query).get("since", ["0"])[0] or 0)
                self._send(200, "application/json", json.dumps(
                    {"said": said_since(jarvis, since), "cards": cards_since(since), "now": time.time(),
                     **face(jarvis)}).encode())
            else:
                self._send(404, "text/plain", b"Not here.")

        def do_POST(self):
            if not self._allowed():
                return
            size = min(int(self.headers.get("Content-Length") or 0), 2000)
            text = (json.loads(self.rfile.read(size) or b"{}").get("text") or "").strip()[:500]
            if text:
                print("[remote] web: %s" % text)
                LAST_WEB[0] = time.time()
                jarvis.inbox.put(("remote", text))
            self._send(200, "application/json", json.dumps({"now": time.time()}).encode())

    try:
        server = ThreadingHTTPServer((ip, port), Handler)
    except OSError as e:
        print("[remote] can't listen on %s:%d: %r" % (ip, port, e))
        return
    print("[remote] web page: http://%s:%d/?key=%s (Tailscale devices only)" % (ip, port, secret))
    server.serve_forever()


# --- Google Chat space ---

def webhook():
    """The JARVIS space's incoming webhook URL (~/.local/share/jarvis/chat_webhook), or None. Posts through it come
    from a "JARVIS" app, so the Chat app on the phone notifies you; posted as you, it never would."""
    try:
        with open(WEBHOOK_FILE) as f:
            url = f.read().strip()
        return url if url.startswith("https://chat.googleapis.com/") else None
    except OSError:
        return None


def post(space, text):
    url = webhook()
    if url:
        req = urllib.request.Request(url, json.dumps({"text": text}).encode(),
                                     {"Content-Type": "application/json; charset=UTF-8"})
        urllib.request.urlopen(req, timeout=15).close()
    elif space:
        chat = google.api("chat", "v1")
        google.run(chat.spaces().messages().create(parent=space, body={"text": PREFIX + text}))


def away():
    """Screen locked, or nothing touched for AWAY_AFTER seconds (GNOME): heads-ups go to Chat instead."""
    def ask(dest, path, method):
        out = subprocess.run(["gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method],
                             capture_output=True, text=True, timeout=5).stdout
        return out
    try:
        if "true" in ask("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.GetActive"):
            return True
        idle = re.search(r"(\d+)", ask("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
                                        "org.gnome.Mutter.IdleMonitor.GetIdletime"))
        return bool(idle) and int(idle.group(1)) / 1000 >= AWAY_AFTER
    except (OSError, subprocess.TimeoutExpired):
        return False


def watch_chat(jarvis, name):
    """Every 5 s: new messages of yours in the space `name` are requests. Posted back there: what JARVIS says for
    FORWARD_FOR s after one, and, with a webhook, everything it says while you're away (not while you use the page)."""
    space, since, look_at, forward_until, forwarded = None, None, 0.0, 0.0, time.time()
    while True:
        time.sleep(5)
        try:
            if google.connected() and space is None and time.time() >= look_at:
                chat = google.api("chat", "v1")
                spaces = google.run(chat.spaces().list(pageSize=200)).get("spaces") or []
                space = next((s["name"] for s in spaces if (s.get("displayName") or "").lower() == name.lower()), None)
                since = google.run(chat.spaces().messages().list(parent=space, pageSize=1, orderBy="createTime desc")
                                   ).get("messages", [{}])[0].get("createTime") if space else None
                look_at = time.time() + 300  # not found: look again in 5 minutes
                print("[remote] Chat space %s: %s%s" % (name, space or "not found (make it in Google Chat)",
                                                       ", notifying through its webhook" if webhook() else ""))
            if space and google.connected():
                chat = google.api("chat", "v1")
                query = {"parent": space, "pageSize": 20}
                if since:
                    query["filter"] = 'createTime > "%s"' % since
                for msg in google.run(chat.spaces().messages().list(**query)).get("messages") or []:
                    since = msg["createTime"]
                    text = (msg.get("text") or "").strip()
                    if text and not text.startswith(PREFIX) and (msg.get("sender") or {}).get("type") != "BOT":
                        print("[remote] chat: %s" % text)
                        forwarded = max(forwarded, time.time() - 1)
                        forward_until = time.time() + FORWARD_FOR
                        jarvis.inbox.put(("remote", text))
        except google.GoogleError as e:
            if "permission" in str(e):
                print("[remote] Chat: %s" % e)
                time.sleep(300)
        except Exception as e:  # never let the thread die
            print("[remote] chat failed: %r" % e)
            time.sleep(30)
        try:
            if time.time() < forward_until + 5 or (webhook() and time.time() - LAST_WEB[0] > FORWARD_FOR and away()):
                new = sorted([(at, text) for at, text in said_since(jarvis, forwarded)] +
                             [(at, chat_text(card)) for at, card in cards_since(forwarded)])
                if new:
                    post(space, "\n".join(text for _, text in new))
                    forwarded = new[-1][0]
            else:
                forwarded = time.time()  # said at the desk: heard, not posted later
        except Exception as e:
            print("[remote] posting to Chat failed: %r" % e)
            time.sleep(30)
