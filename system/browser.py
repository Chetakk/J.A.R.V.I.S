"""Built-in browser phrases: open sites/URLs/tab groups and run searches, in any browser.

Besides "open X" and "search X for Y": a site's own pages ("open my youtube playlists", "open claude new chat",
"open bigg boss on hotstar", "open rakazone on kick"), "open X and search Y", and GitHub repos ("open jarvis on
github", "open the retraining pipeline pull requests"). A page with {me} in it is yours: with a personal and a work
account, "my work github" says which, and otherwise JARVIS asks."""
import re
import shlex
from urllib.parse import quote, quote_plus, urlparse

_DOMAIN = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$", re.IGNORECASE)
_SPLIT_AND = re.compile(r"\s*(?:,|\band\b|&)\s*", re.IGNORECASE)
_VERB = r"(?:open|go to|launch|show(?: me)?|take me to|bring up|pull up)"  # every way of saying "open"
_OPEN = re.compile(r"^%s\s+(.+)$" % _VERB, re.IGNORECASE)
_ON = re.compile(r"\s+(?:on|in|from|at)$")
# "rakazone youtube channel", "o-ring github repo": what the thing before the site's name is
_KIND = re.compile(r"^(?:channel|stream|live ?stream|page|profile|account|show|repo|repository|project)s?$")
_ACCOUNT = re.compile(r"\b(work|office|personal|own)\b")
_FILLER = re.compile(r"\b(?:my|the|me|please|work|office|personal|own)\b")
_REPO_WORD = re.compile(r"\s+(?:repo|repository|project)$")


def _key(text):
    """"Tbone Gaming", "tbone_gaming" and "TBONE-gaming" are the same name."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _words(text):
    return re.sub(r"\s+", " ", _FILLER.sub(" ", text)).strip(" '")


class BrowserAction:
    """A resolved browser request: which URLs, in which browser."""

    def __init__(self, urls, browser, description, choices=None, question=None):
        self.urls = urls
        self.browser = browser  # key from `browsers`, or None for the system default
        self.description = description
        self.choices = choices    # {"personal": action, "work": action}: which one is for the user to say
        self.question = question

    def shell(self, browsers):
        urls = " ".join(shlex.quote(u) for u in self.urls)
        if self.browser:
            return "%s %s" % (browsers[self.browser]["run"], urls)
        # xdg-open takes one URL at a time.
        return " ; ".join("xdg-open %s" % shlex.quote(u) for u in self.urls)


class Browser:
    def __init__(self, registry):
        self.reg = registry

    def parse(self, text):
        """Return a BrowserAction if `text` is a browser request, else None."""
        text, browser = self._pop_browser(text)

        # "open kick and search rakazone": the search is on that site, not Google
        m = re.match(r"^%s\s+(?:my\s+|the\s+)?(.+?)\s+and\s+(?:search|look up|find)\s+(?:for\s+)?(.+)$" % _VERB,
                     text, re.IGNORECASE)
        site = self.site(m.group(1)) if m else None
        if site and self.reg.sites[site].get("search"):
            return self._search(site, m.group(2), browser)

        # "search youtube for X", "search X on youtube", "youtube X", "play X on youtube"
        for name, site in sorted(self._names(), key=lambda n: -len(n[0])):
            if not self.reg.sites[site].get("search"):
                continue
            s = re.escape(name)
            pats = [r"^(?:search|find|look up)\s+(?:on\s+)?%s\s+for\s+(.+)$" % s,
                    r"^(?:search|find|look up|play)\s+(?:for\s+)?(.+?)\s+on\s+%s$" % s]
            if len(name) > 2:  # "x something" is too likely something else
                pats.append(r"^%s\s+(?:search\s+)?(?:for\s+)?(.+)$" % s)
            for pat in pats:
                m = re.match(pat, text, re.IGNORECASE)
                if m:
                    return self._search(site, m.group(1), browser)

        # "google X", "search for X", "search X"
        m = re.match(r"^(?:google|search for|search|look up)\s+(.+)$", text, re.IGNORECASE)
        if m:
            return self._search("google", m.group(1), browser)

        # "open X", "open X and Y", "go to X", "open my work tabs"
        m = re.match(r"^%s\s+(my\s+|the\s+)?(.+?)(?:\s+(?:tabs?|website|web site|site|page))?$" % _VERB,
                     text, re.IGNORECASE)
        if m:
            target = m.group(2).lower()
            if target in self.reg.tab_groups:
                urls = [self._url(t) for t in self.reg.tab_groups[target]]
                return BrowserAction([u for u in urls if u], browser, "your %s tabs" % target)
            parts = [p for p in _SPLIT_AND.split(m.group(2)) if p]
            urls = [self._url(p) for p in parts]
            mine = (m.group(1) or "").strip().lower() == "my"
            if all(urls) and not (mine and len(parts) == 1 and self.reg.sites.get(self.site(parts[0]), {}).get("my")):
                return BrowserAction(urls, browser, " and ".join(parts))

        # "open my github profile", "open bigg boss on hotstar", "open rakazone youtube channel"
        m = _OPEN.match(text)
        return self._place(m.group(1), browser) if m else None

    def _pop_browser(self, text):
        """Strip a trailing 'in chrome' / 'on firefox' and return (text, browser key)."""
        for key, info in self.reg.browsers.items():
            for alias in sorted(info.get("aliases") or [key], key=len, reverse=True):
                m = re.match(r"^(.+?)\s+(?:in|on|using|with)\s+%s$" % re.escape(alias), text, re.IGNORECASE)
                if m:
                    return m.group(1), key
        m = re.match(r"^(.+?)\s+(?:in|on|using|with)\s+(?:the\s+|my\s+)?(?:default\s+|web\s+)?browser$",
                     text, re.IGNORECASE)
        if m:
            return m.group(1), None
        return text, None

    def _names(self):
        """(name as said, site key) for every site and its aliases."""
        return [(n.lower(), site) for site, info in self.reg.sites.items()
                for n in [site] + list(info.get("aliases") or [])]

    def site(self, name):
        """The site key `name` is (its own name or an alias), or None."""
        name = name.lower().strip()
        return next((site for n, site in self._names() if n == name), None)

    def _title(self, site):
        return self.reg.sites[site].get("title") or site.title()

    def _url(self, name):
        name = name.strip()
        site = self.site(name)
        if site:
            return self.reg.sites[site]["url"]
        if _DOMAIN.match(name):
            return name if re.match(r"^https?://", name, re.IGNORECASE) else "https://" + name
        return None

    def _search(self, site, query, browser):
        url = self.reg.sites[site]["search"].replace("{q}", quote_plus(query.strip()))
        return BrowserAction([url], browser, "%s for %s" % (self._title(site), query.strip()))

    # --- a site's own pages ----------------------------------------------------------------------------

    def _place(self, said, browser):
        said = said.lower().strip(" .?!")
        for name, site in sorted(self._names(), key=lambda n: -len(n[0])):
            m = re.search(r"(?:^|\s)%s(?:'s)?(?=\s|$)" % re.escape(name), said)
            if not m:
                continue
            before, after = said[:m.start()].strip(), said[m.end():].strip()
            hint = _ACCOUNT.search(said)
            account = {"office": "work", "own": "personal"}.get(hint.group(1), hint.group(1)) if hint else None
            if re.search(r"\bbiztel$", before):  # "open biztel github": the work one
                account, before = "work", re.sub(r"\bbiztel$", "", before).strip()
            mine = bool(account or re.search(r"\bmy\b", said))
            if _ON.search(" " + before) or (_words(before) and len(name) > 2 and _KIND.match(after or "page")):
                thing = _words(_ON.sub("", " " + before))  # "bigg boss on hotstar", "rakazone youtube channel"
                if not thing:
                    continue
                return self._thing(site, thing, browser, account, named=True, kind=after)
            if _words(before):
                continue  # the site's name inside something else: "open the x file"
            thing = _words(after)  # "claude new chat", "my github repos", "my github"
            if not thing:
                if not mine or not self.reg.sites[site].get("my"):
                    return None
                thing = self.reg.sites[site]["my"]
            return self._thing(site, thing, browser, account)
        # "open the retraining pipeline pull requests", "open the o-ring repo": no site said, but it's a repo
        for site, info in self.reg.sites.items():
            words = "|".join(re.escape(w) for w in list(info.get("repo_pages") or {}) + ["repo", "repository"])
            if info.get("repos") and re.search(r"\s(?:%s)$" % words, said):
                return self._repo(site, _words(said), browser)
        return None

    def _thing(self, site, thing, browser, account, named=False, kind=""):
        """A page of `site` called `thing`. named: "<thing> on <site>", where anything goes (a channel, a search)."""
        info = self.reg.sites[site]
        page = self._lookup(info.get("pages") or {}, thing)
        if page:
            return self._mine(site, page[1], ("%s on %s" if named else "%s %s") % (
                (page[0], self._title(site)) if named else (self._title(site), page[0])), browser, account)
        repo = self._repo(site, thing, browser)
        if repo:
            return repo
        if not named:
            return None
        if info.get("channel") and not re.match(r"^(?:show|repo|repository|project)s?$", kind):
            return BrowserAction([info["channel"].replace("{name}", quote(_key(thing)))], browser,
                                 "%s on %s" % (thing, self._title(site)))
        if info.get("search"):
            return self._search(site, thing, browser)
        return None

    def _lookup(self, pages, thing):
        """(name, url) of the page `thing` names: the same name, or the start of one only one page has."""
        want = _key(thing)
        if not want:
            return None
        hit = [(n, u) for n, u in pages.items() if _key(n) == want]
        if not hit and len(want) >= 4:
            hit = [(n, u) for n, u in pages.items() if _key(n).startswith(want)]
        return hit[0] if len(hit) == 1 else None

    def _mine(self, site, url, description, browser, account):
        """`url` with {me} as the user's account; with two and no "work" or "personal" said, the user picks."""
        url = self._full(site, url)
        if "{me}" not in url:
            return BrowserAction([url], browser, description)
        accounts = self.reg.sites[site].get("accounts") or {}
        if not accounts:
            return None
        if account in accounts or len(accounts) == 1:
            who = account if account in accounts else next(iter(accounts))
            return BrowserAction([url.replace("{me}", accounts[who])], browser,
                                 "your %s%s" % ("%s " % who if len(accounts) > 1 else "", description))
        # each one known by its word and its account's name: "work", "the biztel one", "chetakk"
        choices = {"%s %s %s" % (who, {"work": "office", "personal": "own"}.get(who, ""), re.sub(r"[-_.]", " ", name)):
                   BrowserAction([url.replace("{me}", name)], browser, "your %s %s" % (who, description))
                   for who, name in accounts.items()}
        return BrowserAction([], browser, description, choices=choices,
                             question="Your %s %s, or %s?" % (list(accounts)[0], self._title(site), ", or ".join(
                                 "the %s one" % w for w in list(accounts)[1:])))

    def _full(self, site, url):
        """A page's address; one without https:// is on the site's own server ("in/mypage" on hotstar)."""
        if re.match(r"^https?://", url):
            return url
        home = urlparse(self.reg.sites[site]["url"])
        return "%s://%s/%s" % (home.scheme, home.netloc, url.lstrip("/"))

    def _repo(self, site, thing, browser):
        """"jarvis", "the jarvis repo", "retraining pipeline pull requests": one of your repos on `site`."""
        info = self.reg.sites[site]
        if not info.get("repos"):
            return None
        sub, path = "", ""
        for words, p in sorted((info.get("repo_pages") or {}).items(), key=lambda w: -len(w[0])):
            m = re.match(r"^(.+?)(?:'s)?\s+%s$" % re.escape(words), thing)
            if m:
                thing, sub, path = m.group(1), words, p
                break
        thing = _REPO_WORD.sub("", thing)
        repos = {r.split("/")[-1]: r for r in info["repos"]}
        hit = self._lookup(repos, thing)
        if not hit:
            return None
        return BrowserAction([self._full(site, hit[1]) + ("/" + path if path else "")], browser,
                             "%s%s" % (hit[0], " " + sub if sub else ""))
