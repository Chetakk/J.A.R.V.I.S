#!/usr/bin/env python3
"""Registers JARVIS Link's native host with Chrome (and Chromium). Run once; then in chrome://extensions turn on
Developer mode, click "Load unpacked" and pick this folder. The extension's ID is fixed by the key in
manifest.json, so the host only answers that extension."""
import json
import os
import stat

HERE = os.path.dirname(os.path.abspath(__file__))
ID = "geholpdbgfhdohbniapbcnpcbgjnfhmh"  # from manifest.json's key
NAME = "com.jarvis.link"


def main():
    host = os.path.join(HERE, "host.py")
    os.chmod(host, os.stat(host).st_mode | stat.S_IXUSR)
    spec = {"name": NAME, "description": "JARVIS Link", "path": host, "type": "stdio",
            "allowed_origins": ["chrome-extension://%s/" % ID]}
    for browser in ("google-chrome", "chromium"):
        folder = os.path.expanduser("~/.config/%s/NativeMessagingHosts" % browser)
        if not os.path.isdir(os.path.dirname(folder)):
            continue
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, NAME + ".json"), "w") as f:
            json.dump(spec, f, indent=2)
        print("Registered for %s." % browser)
    print('Now open chrome://extensions, turn on Developer mode, click "Load unpacked" and choose:\n  ' + HERE)


if __name__ == "__main__":
    main()
