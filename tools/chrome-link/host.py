#!/usr/bin/env python3
"""Native messaging host for the JARVIS Link extension. Chrome starts it (and stops it when Chrome closes).
It listens on $XDG_RUNTIME_DIR/jarvis-<uid>/chrome-link.sock: JARVIS writes one JSON line, the request goes
to the extension, and its answer comes back as one line. Standard library only."""
import json
import os
import socket
import struct
import sys
import threading

RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "jarvis-%d" % os.getuid())  # as system/terminal.py
SOCKET = os.path.join(RUN_DIR, "chrome-link.sock")
TIMEOUT = 3

pending = {}  # id -> [event, reply]
out_lock = threading.Lock()
next_id = [0]


def to_chrome(msg):
    data = json.dumps(msg).encode()
    with out_lock:
        sys.stdout.buffer.write(struct.pack("=I", len(data)) + data)
        sys.stdout.buffer.flush()


def from_chrome():
    """Replies from the extension, until Chrome closes the pipe; then the host goes too."""
    while True:
        head = sys.stdin.buffer.read(4)
        if len(head) < 4:
            break
        msg = json.loads(sys.stdin.buffer.read(struct.unpack("=I", head)[0]))
        slot = pending.get(msg.get("id"))
        if slot:
            slot[1] = msg
            slot[0].set()
    if _own_socket():
        os.unlink(SOCKET)
    os._exit(0)


def _own_socket():
    """Another Chrome profile's host may have taken the socket since: leave theirs alone."""
    try:
        return os.stat(SOCKET).st_ino == bound_inode
    except OSError:
        return False


def serve(conn):
    with conn:
        conn.settimeout(TIMEOUT)
        data = b""
        try:
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            req = json.loads(data)
        except (OSError, ValueError):
            return
        if not isinstance(req, dict):
            return
        with out_lock:
            next_id[0] += 1
            rid = next_id[0]
        slot = pending[rid] = [threading.Event(), None]
        req["id"] = rid
        try:
            to_chrome(req)
            reply = slot[1] if slot[0].wait(TIMEOUT) else {"error": "the extension didn't answer"}
        finally:
            pending.pop(rid, None)
        try:
            conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            pass


os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
try:
    os.unlink(SOCKET)
except OSError:
    pass
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET)
os.chmod(SOCKET, 0o600)
bound_inode = os.stat(SOCKET).st_ino
server.listen(4)
threading.Thread(target=from_chrome, daemon=True).start()
while True:
    conn, _ = server.accept()
    threading.Thread(target=serve, args=(conn,), daemon=True).start()
