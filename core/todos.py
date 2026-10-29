"""Todos from ProductVT (~/Projects/ProductVT): read them, add one, tick one off.

Uses ProductVT's /api/jarvis/todos, which acts as one user with a shared token. Its address and token come from
PRODUCTVT_URL / PRODUCTVT_TOKEN, or ~/.config/jarvis/productvt.json: {"url": "https://...", "token": "..."}.
"""
import difflib
import json
import os
import urllib.error
import urllib.request

CONFIG = os.path.expanduser("~/.config/jarvis/productvt.json")
TODO = r"(?:to ?-?dos?|todo list|to ?do list|task list|tasks)"
MAX_READ = 5


class Todos:
    def __init__(self, say):
        self.say = say

    def rules(self):
        """Phrase patterns for core/skills.py. "what's pending" stays with project notes; these say "todo"."""
        return [
            (r"(?:add|put) (.+?) (?:to|on|in) my %s|(?:add|create|make)(?: a)?(?: new)? to ?-?do(?: to| for)?:? (.+)" % TODO,
             self.add),
            (r"(?:mark|tick off|check off|cross off) (?:the )?(.+?)(?: to ?-?do| task)?(?: as)?"
             r"(?: done| complete| completed| finished)?", self.done),
            (r"(?:what'?s|what is|what are|read(?: me)?|show me|list|tell me)(?: on| in)? my %s(?: today)?|my %s|"
             r"(?:how many|any) %s (?:do i have|left|open)(?: today)?" % (TODO, TODO, TODO), lambda m: self.read()),
        ]

    # --- api ---

    def _conf(self):
        url, token = os.environ.get("PRODUCTVT_URL"), os.environ.get("PRODUCTVT_TOKEN")
        if not (url and token):
            try:
                with open(CONFIG) as f:
                    c = json.load(f)
                url, token = url or c.get("url"), token or c.get("token")
            except (OSError, ValueError):
                pass
        return (url or "").rstrip("/"), token

    def _call(self, method, path, body=None):
        url, token = self._conf()
        if not (url and token):
            raise RuntimeError("ProductVT isn't set up; put its url and token in %s" % CONFIG)
        req = urllib.request.Request(url + "/api/jarvis/todos" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError("ProductVT said %s" % ("the token is wrong" if e.code == 401 else e.code))
        except (urllib.error.URLError, OSError):
            raise RuntimeError("I can't reach ProductVT")

    def _open(self):
        return self._call("GET", "")["todos"]

    # --- skills ---

    def read(self):
        todos = self._open()
        if not todos:
            self.say("Your todo list is clear.")
            return
        top = [t["title"] for t in todos[:MAX_READ]]
        more = len(todos) - len(top)
        line = "You have %d open todo%s: %s" % (len(todos), "" if len(todos) == 1 else "s", "; ".join(top))
        self.say(line + (", and %d more." % more if more else "."))

    def add(self, m):
        title = next(g for g in m.groups() if g).strip(" .")
        if title.lower().startswith("to "):  # "add a todo to call Ravi"
            title = title[3:]
        self._call("POST", "", {"title": title[:1].upper() + title[1:]})
        self.say("Added %s to your todos." % title)

    def done(self, m):
        want = m.group(1).strip().lower()
        todos = self._open()
        titles = [t["title"].lower() for t in todos]
        hits = [i for i, t in enumerate(titles) if want in t]
        if not hits:
            hits = [titles.index(c) for c in difflib.get_close_matches(want, titles, n=1, cutoff=0.5)]
        if not hits:
            return False  # not a todo: let the request carry on
        if len(hits) > 1:
            self.say("Which one? %s." % " or ".join(todos[i]["title"] for i in hits[:3]))
            return
        t = todos[hits[0]]
        self._call("PATCH", "/" + t["id"], {"done": True})
        self.say("Done: %s." % t["title"])
