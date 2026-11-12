"""JARVIS Link: the small Chrome extension in tools/chrome-link that tells JARVIS what's on the tab you're
looking at in your own Chrome (video and link titles) and opens one of them. Chrome starts the extension's
native host (tools/chrome-link/host.py), which listens on SOCKET; each call here is one line out, one line back.
Page text is the page's own words: used only to match what you said, never as instructions.

Agent does the browser agent's work (system/webagent.py) in your Chrome instead, with your logins."""
import base64
import json
import os
import socket

from system.terminal import RUN_DIR
from system.webagent import AgentError

SOCKET = os.path.join(RUN_DIR, "chrome-link.sock")


def call(op, timeout=2.0, raw=False, **args):
    """The extension's reply as a dict, or None when Chrome, the extension or the page doesn't answer.
    raw: an answer that's an error comes back too, as {"error": why}."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout + 1)  # the host gives up at `timeout` and says so
            s.connect(SOCKET)
            s.sendall((json.dumps(dict(args, op=op, wait=timeout)) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data)
    except (OSError, ValueError):
        return None
    return None if not isinstance(reply, dict) or ("error" in reply and not raw) else reply


def page():
    """{"url", "title", "video": a video is on the page, "items": [{"title", "url", "video", "seen"}]} or None."""
    return call("page")


class Agent:
    """system/webagent.WebAgent's calls (open, read, readMany, transcript, snapshot, act, download, screenshot) plus
    console and network, done in your own Chrome by the extension. A task keeps to one tab: the one you're on, or a
    new one from "open" without a tab. readMany, transcript and reading another address load out of your way."""

    def __init__(self, timeout=60):
        self.tab, self.timeout = None, timeout

    def call(self, op, timeout=None, **args):
        if "id" in args:  # an element's number: "id" names the host's messages
            args["el"] = args.pop("id")
        reply = call("agent", timeout or self.timeout, raw=True, do=op, tab=self.tab, **args)
        if reply is None:
            raise AgentError("Chrome isn't answering")
        if "error" in reply:
            raise AgentError(reply["error"])
        self.tab = reply.get("tab") or self.tab
        if op == "screenshot":
            with open(args["path"], "wb") as f:
                f.write(base64.b64decode(reply.pop("png")))
            reply["path"] = args["path"]
        return reply
