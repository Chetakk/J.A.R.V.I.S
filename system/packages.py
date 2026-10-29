"""Looking software up, read-only: is it installed (dpkg, or on the PATH), what apt has under that name, and
pip packages. Installing and removing never happen here: skills/install.py hands the command to the executor,
whose safety rules always send sudo and pip to your terminal."""
import re
import shutil
import subprocess

APT_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
PIP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ALIASES = {"chromium": "chromium-browser", "vs code": "code", "vscode": "code", "visual studio code": "code",
           "chrome": "google-chrome-stable", "google chrome": "google-chrome-stable", "obs": "obs-studio",
           "libre office": "libreoffice", "node": "nodejs", "node js": "nodejs", "java": "default-jre",
           "pip": "python3-pip", "docker": "docker.io"}


def _run(args, timeout=10):
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def name_for(words):
    """What you said as a package name: "vs code" -> "code", "obs" -> "obs-studio", "foo bar" -> "foo-bar"."""
    words = re.sub(r"\s+", " ", words.lower().strip())
    return ALIASES.get(words, words.replace(" ", "-"))


def installed(pkg):
    """The installed version, "yes" for a program on the PATH that apt doesn't know, or None."""
    if APT_NAME.match(pkg):
        code, out = _run(["dpkg-query", "-W", "-f=${Status} ${Version}", pkg])
        if code == 0 and out.startswith("install ok installed"):
            return out.split()[-1]
    return "yes" if shutil.which(pkg) else None


def info(pkg):
    """{"name", "version", "summary"} of the apt package called `pkg`, or None."""
    if not APT_NAME.match(pkg):
        return None
    code, out = _run(["apt-cache", "show", "--no-all-versions", pkg])
    if code or not out.strip():
        return None
    fields = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line and not line.startswith(" "))
    return {"name": pkg, "version": fields.get("Version", ""),
            "summary": fields.get("Description-en") or fields.get("Description", "")}


def search(words, limit=3):
    """[(name, summary)] of apt packages whose names start with `words`, closest first."""
    pkg = name_for(words)
    if not APT_NAME.match(pkg):
        return []
    _, out = _run(["apt-cache", "search", "--names-only", "^" + re.escape(pkg)])
    found = [tuple(line.split(" - ", 1)) for line in out.splitlines() if " - " in line]
    found = [f for f in found if not re.search(r"-(?:dbg|dbgsym|dev|doc|data|l10n|common)$", f[0])] or found
    return sorted(found, key=lambda f: (len(f[0]), f[0]))[:limit]


def pip_installed(name):
    if not PIP_NAME.match(name):
        return None
    code, out = _run(["pip3", "show", name], timeout=15)
    version = re.search(r"^Version: (\S+)", out, re.M)
    return version.group(1) if code == 0 and version else None
