"""Which programs listen on which TCP ports (ss -ltnp), and the folder each runs in, so "port 5173" can be
told as "node, in study-buddy/frontend"."""
import os
import re
import subprocess


class Listener:
    def __init__(self, port, address, program, pid):
        self.port, self.address, self.program, self.pid = port, address, program, pid
        self.folder = _cwd(pid)

    def __repr__(self):
        return "<Listener :%d %s pid %s in %s>" % (self.port, self.program, self.pid, self.folder)

    @property
    def local_only(self):
        return self.address.startswith(("127.", "[::1]", "::1"))


def listening():
    """[Listener] for every listening TCP port (one per port and program), lowest port first."""
    try:
        out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    seen, found = set(), []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        address, _, port = cols[3].rpartition(":")
        if not port.isdigit():
            continue
        m = re.search(r'\(\("([^"]+)",pid=(\d+)', line)
        program, pid = (m.group(1), int(m.group(2))) if m else ("", None)
        if (int(port), program) in seen:
            continue  # the same server on IPv4 and IPv6
        seen.add((int(port), program))
        found.append(Listener(int(port), address, program, pid))
    return sorted(found, key=lambda l: l.port)


def on_port(port):
    return [l for l in listening() if l.port == port]


def _cwd(pid):
    try:
        return os.readlink("/proc/%d/cwd" % pid) if pid else ""
    except OSError:
        return ""
