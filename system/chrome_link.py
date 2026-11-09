"""JARVIS Link: the small Chrome extension in tools/chrome-link that tells JARVIS what's on the tab you're
looking at in your own Chrome (video and link titles) and opens one of them. Chrome starts the extension's
native host (tools/chrome-link/host.py), which listens on SOCKET; each call here is one line out, one line back.
Page text is the page's own words: used only to match what you said, never as instructions."""
import json
import os
import socket

from system.terminal import RUN_DIR

SOCKET = os.path.join(RUN_DIR, "chrome-link.sock")


def call(op, timeout=2.0, **args):
    """The extension's reply as a dict, or None when Chrome, the extension or the page doesn't answer."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCKET)
            s.sendall((json.dumps(dict(args, op=op)) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data)
    except (OSError, ValueError):
        return None
    return None if not isinstance(reply, dict) or "error" in reply else reply


def page():
    """{"url", "title", "video": a video is on the page, "items": [{"title", "url", "video", "seen"}]} or None."""
    return call("page")
