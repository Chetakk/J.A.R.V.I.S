#!/usr/bin/env python3
"""What JARVIS sent to Claude or didn't understand, most frequent first.

  tools/misses.py           # all of them
  tools/misses.py 7         # the last 7 days
Each line: count, outcome, what was said, and the matcher's nearest known phrase with its score.
A near miss (score just under the threshold) is a wording worth adding to config/intents.yaml.
"""
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import misslog  # noqa: E402


def main(argv):
    entries = misslog.read()
    if argv:
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - float(argv[0]) * 86400))
        entries = [e for e in entries if e["time"] >= since]
    groups = collections.OrderedDict()
    for e in entries:
        groups.setdefault((e["text"].lower(), e["outcome"]), []).append(e)
    for (text, outcome), es in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        last = es[-1]
        guess = "  ~ %s (%.2f)" % (last["guess"], last["score"]) if last.get("guess") else ""
        print("%3d  %-8s %s%s" % (len(es), outcome, text, guess))
    print("%d misses, %d different" % (len(entries), len(groups)))


if __name__ == "__main__":
    main(sys.argv[1:])
