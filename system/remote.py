"""Control JARVIS from anywhere: a small web page on the Tailscale address, and a Google Chat space.

Web: http://<tailscale ip>:8765/?key=<key> from any of your Tailscale devices (phone app signed in to the same
account). It only listens on the Tailscale address, never the LAN or the internet, and also needs the key in
~/.local/share/jarvis/remote_key (made on the first start, printed in the log).

Chat: make a Google Chat space named "JARVIS" with only you in it; what you write there is done as a request and
JARVIS's answers are posted back ("JARVIS: ..."). Works from any browser signed in to Google. Checked every 5 s.

Requests come in as kind "remote" (yes/no questions wait longer for them, jarvis._answer). Settings: remote: web,
port, chat_space."""
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from system import google_account as google

KEY_FILE = os.path.expanduser("~/.local/share/jarvis/remote_key")
PREFIX = "JARVIS: "  # marks its own replies in the Chat space (they're posted as you)
FORWARD_FOR = 120  # seconds after a Chat request that what JARVIS says is posted back


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
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif url.path == "/log":
                since = float(parse_qs(url.query).get("since", ["0"])[0] or 0)
                self._send(200, "application/json", json.dumps(
                    {"said": said_since(jarvis, since), "now": time.time(), "state": "online"}).encode())
            else:
                self._send(404, "text/plain", b"Not here.")

        def do_POST(self):
            if not self._allowed():
                return
            size = min(int(self.headers.get("Content-Length") or 0), 2000)
            text = (json.loads(self.rfile.read(size) or b"{}").get("text") or "").strip()[:500]
            if text:
                print("[remote] web: %s" % text)
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

def watch_chat(jarvis, name):
    """Every 5 s: new messages of yours in the space `name` are requests; replies are posted back."""
    space, since, forward_until, forwarded = None, None, 0.0, time.time()
    while True:
        time.sleep(5)
        try:
            if not google.connected():
                continue
            chat = google.api("chat", "v1")
            if space is None:
                spaces = google.run(chat.spaces().list(pageSize=200)).get("spaces") or []
                space = next((s["name"] for s in spaces if (s.get("displayName") or "").lower() == name.lower()), "")
                since = google.run(chat.spaces().messages().list(parent=space, pageSize=1, orderBy="createTime desc")
                                   ).get("messages", [{}])[0].get("createTime") if space else None
                print("[remote] Chat space %s: %s" % (name, space or "not found (make it in Google Chat)"))
            if not space:
                time.sleep(295)  # look for it again in 5 minutes
                space = None
                continue
            query = {"parent": space, "pageSize": 20}
            if since:
                query["filter"] = 'createTime > "%s"' % since
            for msg in google.run(chat.spaces().messages().list(**query)).get("messages") or []:
                since = msg["createTime"]
                text = (msg.get("text") or "").strip()
                if text and not text.startswith(PREFIX):
                    print("[remote] chat: %s" % text)
                    forwarded = max(forwarded, time.time() - 1)
                    forward_until = time.time() + FORWARD_FOR
                    jarvis.inbox.put(("remote", text))
            if time.time() < forward_until + 5:
                new = said_since(jarvis, forwarded)
                if new:
                    forwarded = new[-1][0]
                    google.run(chat.spaces().messages().create(parent=space, body={
                        "text": "\n".join(PREFIX + text for _, text in new)}))
        except google.GoogleError as e:
            if "permission" in str(e):
                print("[remote] Chat: %s" % e)
                time.sleep(300)
        except Exception as e:  # never let the thread die
            print("[remote] chat failed: %r" % e)
            time.sleep(30)


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>JARVIS</title>
<style>
:root{--bg:#0b1220;--card:#131c2e;--me:#1f6feb;--fg:#e6edf3;--dim:#8b949e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif;
display:flex;flex-direction:column;height:100dvh}
header{padding:12px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #222c40}
header b{letter-spacing:.15em;color:#58a6ff}#state{color:var(--dim);font-size:13px}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
.m{max-width:85%;padding:9px 13px;border-radius:14px;line-height:1.35;white-space:pre-wrap;word-wrap:break-word}
.j{background:var(--card);align-self:flex-start}.u{background:var(--me);align-self:flex-end}
.quick{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding:0 16px 8px}
.quick button{flex:none;background:var(--card);color:var(--fg);border:1px solid #2a3550;border-radius:16px;padding:6px 12px}
form{display:flex;gap:8px;padding:8px 16px 16px}
input{flex:1;font:inherit;padding:12px;border-radius:12px;border:1px solid #2a3550;background:var(--card);color:var(--fg)}
form button{font:inherit;padding:0 18px;border:0;border-radius:12px;background:var(--me);color:#fff}
</style></head><body>
<header><b>J.A.R.V.I.S</b><span id="state"></span></header>
<div id="log"></div>
<div class="quick"><button>yes</button><button>no</button><button>stop</button><button>what's on my calendar</button>
<button>pause music</button><button>lock the screen</button></div>
<form id="f"><input id="t" placeholder="Ask JARVIS..." autocomplete="off" enterkeyhint="send"><button>Send</button></form>
<script>
const key=new URLSearchParams(location.search).get("key")||"",log=document.getElementById("log");
let since=Date.now()/1000;
function add(text,who){const d=document.createElement("div");d.className="m "+who;d.textContent=text;
log.appendChild(d);log.scrollTop=log.scrollHeight}
async function send(text){if(!text.trim())return;add(text,"u");
try{await fetch("/?key="+encodeURIComponent(key),{method:"POST",body:JSON.stringify({text})})}
catch(e){add("Couldn't reach JARVIS.","j")}}
document.getElementById("f").onsubmit=e=>{e.preventDefault();const t=document.getElementById("t");send(t.value);t.value=""};
document.querySelectorAll(".quick button").forEach(b=>b.onclick=()=>send(b.textContent));
async function poll(){try{const r=await(await fetch("/log?key="+encodeURIComponent(key)+"&since="+since)).json();
for(const [at,text] of r.said){add(text,"j");since=Math.max(since,at)}
document.getElementById("state").textContent=r.state||""}catch(e){document.getElementById("state").textContent="offline"}
setTimeout(poll,1500)}
poll();
</script></body></html>"""
