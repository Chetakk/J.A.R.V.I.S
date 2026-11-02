"""Built-in browser phrases: open sites/URLs/tab groups and run searches, in any browser.

Besides "open X" and "search X for Y": a site's own pages ("open my youtube playlists", "open claude new chat",
"open bigg boss on hotstar", "open rakazone on kick"), "open X and search Y", and GitHub repos ("open jarvis on
github", "open the retraining pipeline pull requests"). A page with {me} in it is yours: with a personal and a work
account, "my work github" says which, and otherwise JARVIS asks."""
import re
import shlex
import subprocess
from urllib.parse import quote, quote_plus, urlparse

_DOMAIN = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$", re.IGNORECASE)
_SPLIT_AND = re.compile(r"\s*(?:,|\band(?: then)?\b|&|\bplus\b|\bthen\b|\balong with\b|\bwith\b|\balso\b)\s*",
                        re.IGNORECASE)
# every way of saying "open" ("fire up youtube", "head to github", "watch bigg boss", "check my gmail")
_VERB = (r"(?:open(?: up)?|go(?: over)? to|launch|show(?: me)?|take me(?: over)? to|bring up|pull up|fire up|"
         r"start(?: up)?|load(?: up)?|get(?: me)?|put on|head(?: over)? to|navigate to|visit|browse to|switch to|"
         r"jump to|throw up|check(?: out)?|see|watch|play|set up|create|make)")
_OPEN = re.compile(r"^%s\s+(.+)$" % _VERB, re.IGNORECASE)
# Only saying you want it: "I want youtube", "can I get youtube", "let me see youtube", "let's go to youtube"
_WANT = re.compile(r"^(?:(?:i (?:want|need|wanna|would like)|i'd like|can i (?:get|have|see)|let me|let's|lets)"
                   r"(?: to)?\s+)+", re.IGNORECASE)
# Said after the thing, and what it asks for: "youtube kholo", "get youtube up", "bigg boss lagao", "zepto pe milk dhundo"
_TAILS = [(r"(?:pe |par )?le chalo|kholo|khol do|open karo|up|open", "open"),
          (r"chalao|chala do|lagao|laga do|bajao", "play"),
          (r"dhundo|dhoondo|dhundho|search karo|dikhao|dikha do|karo", "search"),
          (r"together", "")]
_HINDI_ON = r"(?:pe|par|pr|mein|me)"  # "youtube pe lofi": lofi on youtube
# "claude dot ai", "www dot hotstar dot com"
_DOT = re.compile(r"\s+dot\s+(?=(?:[a-z0-9-]+\s+dot\s+)*(?:com|in|org|net|ai|io|co|tv|dev|app|me|gg|vg|so|ly)\b)",
                  re.IGNORECASE)
_ON = re.compile(r"\s+(?:on|in|from|at)$")
# "rakazone youtube channel", "o-ring github repo": what the thing before the site's name is
_KIND = re.compile(r"^(?:channel|stream|live ?stream|live|page|profile|account|show|repo|repository|project|videos?|"
                   r"event|code)s?$")
_KIND_END = re.compile(r"\s+(?:channel|stream|live ?stream|live|page|profile|account|show|event|code|list)s?$")
_LIVE = re.compile(r"\s+(?:stream|live ?stream|live)s?$")  # "the rakazone stream": a channel, not a video
_ACCOUNT = re.compile(r"\b(work|office|personal|own|company|job)\b")
_FILLER = re.compile(r"\b(?:my|the|me|please|work|office|personal|own|company|job|a|an|some)\b")
_REPO_WORD = re.compile(r"\s+(?:repo|repository|project|code)$")
# Page names too plain to open without the site's name: "open home" is your home folder, not X's home page
_PLAIN_PAGES = {"home", "new", "explore", "settings", "profile", "network", "events", "notifications", "messages",
                "inbox", "jobs", "history", "list", "dms", "feed"}
# "lofi on youtube" is something on YouTube; "what's on youtube" and "turn it up on youtube" aren't
_NOT_A_THING = re.compile(r"^(?:what|what's|whats|who|why|how|is|are|do|does|did|can|could|which|when|where|turn|set|"
                          r"make|pause|stop|close|mute|unmute|skip|increase|decrease|raise|lower|change|switch|put|"
                          r"add|remove|delete|send|share|post|upload|like|subscribe|follow|log|sign|i|it|this|that)\b",
                          re.IGNORECASE)
_DIGITS = dict(zip("zero one two three four five six seven eight nine".split(), "0123456789"))


def _key(text):
    """"Tbone Gaming", "tbone_gaming" and "TBONE-gaming" are the same name; so are "brooklyn 99" and "nine nine"."""
    text = re.sub(r"\b(%s)\b" % "|".join(_DIGITS), lambda m: _DIGITS[m.group(1)], text.lower())
    return re.sub(r"[^a-z0-9]", "", text)


def _words(text):
    return re.sub(r"\s+", " ", _FILLER.sub(" ", re.sub(r"'s\b", "", text))).strip(" '")


class BrowserAction:
    """A resolved browser request: which URLs, in which browser."""

    def __init__(self, urls, browser, description, choices=None, question=None):
        self.urls = urls
        self.browser = browser  # key from `browsers`, or None for the system default
        self.description = description
        self.choices = choices    # {"personal": action, "work": action}: which one is for the user to say
        self.question = question

    def shell(self, browsers):
        """In a new window (on JARVIS's empty workspace), not a tab in the browser window already open."""
        urls = " ".join(shlex.quote(u) for u in self.urls)
        browser = self.browser or default_browser(browsers)
        if browser:
            run = browsers[browser]
            return " ".join(filter(None, (run["run"], run.get("new_window"), urls)))
        # xdg-open takes one URL at a time.
        return " ; ".join("xdg-open %s" % shlex.quote(u) for u in self.urls)


def default_browser(browsers):
    """The key in `browsers` for the system's default browser (its `desktop` file), else None."""
    try:
        desktop = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True,
                                 timeout=2).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return None
    return next((k for k, b in browsers.items() if desktop and b.get("desktop", "").lower() == desktop), None)


class Browser:
    def __init__(self, registry):
        self.reg = registry

    def parse(self, text, guess=True):
        """Return a BrowserAction if `text` is a browser request, else None.
        guess=False: not "play X song" on YouTube when no site was said (a skill's own group may be called that)."""
        text, browser = self._pop_browser(text)
        text = self._plain(text)

        # "open kick and search rakazone", "open youtube then play lofi": the search is on that site, not Google
        m = re.match(r"^%s\s+(?:my\s+|the\s+)?(.+?)\s+(?:and(?: then)?|then)\s+(?:search|look up|look for|find|play|"
                     r"type|order|buy|watch|get)\s+(?:for\s+)?(.+)$" % _VERB, text, re.IGNORECASE)
        site = self.site(m.group(1)) if m else None
        if site and self.reg.sites[site].get("search"):
            return self._search(site, m.group(2), browser)

        # "find jobs for ML engineer on linkedin", "show me ML engineer jobs"
        m = re.match(r"^(?:find|search|look for|show|get)(?: me)?(?: for)? (?:(?:jobs|openings|vacancies) for "
                     r"(.+?)|(?!(?:my |the )?linkedin\b)(.+?) (?:jobs|openings|vacancies|roles))(?: (?:on|in) linkedin"
                     r"(?: jobs)?)?$", text, re.IGNORECASE)
        if m and "linkedin jobs" in self.reg.sites:
            return self._search("linkedin jobs", m.group(1) or m.group(2), browser)
        m = re.match(r"^(?:search|find|look up|look for)(?: for)? (?:tweets|posts) (?:about|on|for) (.+)$", text,
                     re.IGNORECASE)
        if m and "x" in self.reg.sites:
            return self._search("x", m.group(1), browser)

        # "search youtube for X", "search X on youtube", "youtube X", "play X on youtube", "order milk on zepto"
        for name, site in sorted(self._names(), key=lambda n: -len(n[0])):
            if not self.reg.sites[site].get("search"):
                continue
            s = re.escape(name)
            pats = [r"^(?:search|find|look up|look for|check)\s+(?:on\s+)?%s\s+for\s+(.+)$" % s,
                    r"^(?:search(?: for)?|find(?: me)?|look up|look for|order|buy|shop for|get)\s+(?:for\s+)?"
                    r"(.+?)\s+(?:on|in|from|at)\s+%s$" % s,
                    r"^see if %s has (.+)$" % s]
            if len(name) > 2:  # "x something" is too likely something else
                pats.append(r"^%s\s+(?:search\s+)?(?:for\s+)?(.+)$" % s)
            for pat in pats:
                m = re.match(pat, text, re.IGNORECASE)
                if m and pat is pats[-1] and len(name) > 2 and self._all_sites(text):
                    continue  # "youtube and gmail": two sites to open, not a YouTube search for "and gmail"
                if m:
                    return self._search(site, re.sub(r"^(?:some|me)\s+", "", m.group(1), flags=re.IGNORECASE),
                                        browser)

        # "where is the nearest pizza place", "find the closest petrol pump": on the map
        m = re.match(r"^(?:where(?:'s| is)|find|show me|show)(?: me)? (?:the |a )?((?:nearest|closest) .+)$", text,
                     re.IGNORECASE)
        if m and self.reg.sites.get("maps", {}).get("search"):
            return self._search("maps", m.group(1), browser)

        # "google X", "search for X", "search the web for X", "do a google search for X"
        m = re.match(r"^(?:do an? (?:google |web )?search for|(?:search|look) (?:the web|the internet|online|google) "
                     r"for|web search(?: for)?|google|search for|search|look up)\s+(.+)$", text, re.IGNORECASE)
        if m:
            return self._search("google", m.group(1), browser)

        # "open X", "open X and Y", "go to X", "open my work tabs", "open gmail youtube and github"
        m = re.match(r"^%s\s+(my\s+|the\s+|both\s+|an?\s+)?(.+?)(?:\s+(?:tabs?|website|web site|sites?|page|app|stuff|"
                     r"mode))?$" % _VERB, text, re.IGNORECASE)
        if m:
            action = self._sites(m.group(2), browser, mine=(m.group(1) or "").strip().lower() == "my")
            if action:
                return action

        # "youtube", "gmail and youtube", "music mode", "lofi on youtube": no "open" said
        if not _OPEN.match(text):
            m = re.match(r"^(?:my\s+|the\s+)?(.+?)\s+(?:tabs|mode|stuff|sites)$", text, re.IGNORECASE)
            if m and m.group(1).lower() in self.reg.tab_groups:
                return self._sites(m.group(1), browser)
            if self._all_sites(text):
                return self._sites(text, browser)
            if not _NOT_A_THING.match(text) and any(re.search(r"\s(?:on|in|from)\s+%s$" % re.escape(n), text,
                                                              re.IGNORECASE) for n, _ in self._names()):
                text = "open " + text
            else:
                # "what have I watched on youtube": its history
                m = re.match(r"^what (?:have|did) i (?:watched|watch)(?: on| in)? (.+)$", text, re.IGNORECASE)
                if m:
                    text = "open history on %s" % m.group(1)
                # "is rakazone live", "did jarvis's build pass"
                m = re.match(r"^is (.+?) (?:live|streaming)(?: now| right now)?$", text, re.IGNORECASE)
                if m:
                    text = "open %s live" % m.group(1)
                m = re.match(r"^did (?:the )?(.+?)(?:'s)? (?:build|builds|ci|checks|tests|workflows?) (?:pass|passed|"
                             r"work|go through)$", text, re.IGNORECASE)
                if m:
                    text = "open %s actions" % m.group(1)

        # "open my github profile", "open bigg boss on hotstar", "open rakazone youtube channel"
        m = _OPEN.match(text)
        action = self._place(m.group(1), browser) if m else None
        if action:
            return action
        # "play deva deva song", "watch the avengers trailer": a song or a video, on YouTube
        m = re.match(r"^(?:play|watch|put on)\s+(?:some\s+|the\s+)?(.+\s(?:song|songs|gaana|gaane|music|video|videos|"
                     r"trailer|mix))$", text, re.IGNORECASE)
        if m and guess and self.reg.sites.get("youtube", {}).get("search"):
            return self._search("youtube", m.group(1), browser)
        return None

    def _plain(self, text):
        """Down to a plain request: no "I want", Hindi or other words after the thing, "dot com" as ".com".
        "I need youtube open" -> "open youtube", "zepto pe milk dhundo" -> "search milk on zepto"."""
        text = _DOT.sub(".", _WANT.sub("", text.strip()))
        how = None
        for words, wants in _TAILS:
            m = re.match(r"^(.+?)\s+(?:%s)$" % words, text, re.IGNORECASE)
            if m:
                text, how = m.group(1), wants
                break
        if how is None or _OPEN.match(text) or re.match(r"^(?:search|find|look up)\b", text, re.IGNORECASE):
            return text
        verb = {"play": "play", "search": "search"}.get(how, "open")
        for name, site in sorted(self._names(), key=lambda n: -len(n[0])):
            s = re.escape(name)
            m = (re.match(r"^%s %s (.+)$" % (s, _HINDI_ON), text, re.IGNORECASE) or
                 re.match(r"^(.+) %s %s$" % (s, _HINDI_ON), text, re.IGNORECASE) or
                 (how in ("search", "play") and re.match(r"^(.+) %s$" % s, text, re.IGNORECASE)))
            if m:  # "youtube pe lofi chalao": play lofi on youtube; "cats google karo": search cats on google
                return "%s %s on %s" % ("open" if verb == "play" else verb, m.group(1), name)
        return "%s %s" % (verb, text)

    def _sites(self, said, browser, mine=False):
        """"youtube", "gmail and youtube", "gmail youtube and github", "work" (a tab group), "hotstar.com"."""
        said = re.sub(r"^(?:site|website|url|web address|address|link)\s+", "", said.strip(), flags=re.IGNORECASE)
        group = re.sub(r"^(?:everything|all|stuff) for ", "", said.lower())
        if group in self.reg.tab_groups:
            urls = [self._url(t) for t in self.reg.tab_groups[group]]
            return BrowserAction([u for u in urls if u], browser, "your %s tabs" % group)
        parts = [p for p in _SPLIT_AND.split(said) if p]
        names = []
        for part in parts:
            names += [part] if self._url(part) else (self._run_of_sites(part) or [None])
        if not names or not all(names):  # "And" alone splits into nothing: not a site
            return None
        if mine and len(names) == 1 and self.reg.sites.get(self.site(names[0]), {}).get("my"):
            return None  # "open my github": your page on it, not its home page
        return BrowserAction([self._url(n) for n in names], browser, " and ".join(names))

    def _run_of_sites(self, said):
        """"gmail youtube": several site names one after another, longest names first, or None."""
        words, out = said.lower().split(), []
        while words:
            for n in range(len(words), 0, -1):
                if self.site(" ".join(words[:n])):
                    out.append(" ".join(words[:n]))
                    words = words[n:]
                    break
            else:
                return None
        for name, nxt in zip(out, out[1:]):  # "linkedin inbox" is LinkedIn's inbox, not LinkedIn and Gmail
            if self._lookup(self.reg.sites[self.site(name)].get("pages") or {}, nxt, prefix=False):
                return None
        return out if len(out) > 1 else None

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

    def _all_sites(self, text):
        """"youtube and gmail": every part a site name (just "Claude" is talking to Claude, not claude.ai)."""
        parts = [p for p in _SPLIT_AND.split(text) if p]
        return bool(parts) and all(len(p) > 2 and self.site(p) and self.site(p) != "claude" for p in parts)

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
        said = re.sub(r"\b(?:old|past) streams\b|\bvods\b", "videos", said)  # "rakazone's old streams"
        for name, site in sorted(self._names(), key=lambda n: -len(n[0])):
            m = re.search(r"(?:^|\s)%s(?:'s)?(?=\s|$)" % re.escape(name), said)
            if not m:
                continue
            before, after = said[:m.start()].strip(), said[m.end():].strip()
            hint = _ACCOUNT.search(said)
            account = {"office": "work", "own": "personal", "company": "work", "job": "work"}.get(
                hint.group(1), hint.group(1)) if hint else None
            if re.search(r"\bbiztel$", before):  # "open biztel github": the work one
                account, before = "work", re.sub(r"\bbiztel$", "", before).strip()
            for who, login in (self.reg.sites[site].get("accounts") or {}).items():  # "open chetakk's github"
                if _words(before) and _key(_words(before)) == _key(login):
                    account, before = who, ""
            mine = bool(account or re.search(r"\bmy\b", said))
            pages = self.reg.sites[site].get("pages") or {}
            # "new claude chat", "make a new github repo": the page's name is around the site's
            page = _words(before) and after and self._lookup(pages, "%s %s" % (_words(before), _words(after)))
            if page:
                return self._mine(site, page[1], "%s %s" % (self._title(site), page[0]), browser, account)
            if _ON.search(" " + before) or (_words(before) and len(name) > 2 and _KIND.match(after or "page")):
                thing = _words(_ON.sub("", " " + before))  # "bigg boss on hotstar", "rakazone youtube channel"
                if not thing:
                    continue
                return self._thing(site, thing, browser, account, named=True, kind=after)
            if _words(before):
                continue  # the site's name inside something else: "open the x file"
            thing = re.sub(r"^the\s+", "", after)  # "hotstar my space": "my" is part of the page's name
            if not self._lookup(pages, thing):
                thing = _words(after)  # "claude new chat", "my github repos", "my github"
            if thing and _KIND.match(thing) and not self._lookup(pages, thing):
                thing = ""  # "my linkedin page", "my work github account": the site's own "my" page
            if not thing:
                if not mine or not self.reg.sites[site].get("my"):
                    return None
                thing = self.reg.sites[site]["my"]
            return self._thing(site, thing, browser, account)
        # "watch bigg boss", "open my playlists", "the rakazone stream": a page only one site has
        action = self._anywhere(said, browser)
        if action:
            return action
        # "open the retraining pipeline pull requests", "open the o-ring repo": no site said, but it's a repo
        for site, info in self.reg.sites.items():
            if not info.get("repos"):
                continue
            pages = "|".join(re.escape(w) for w in sorted(info.get("repo_pages") or {}, key=len, reverse=True))
            # "the pull requests for jarvis", "recent commits on jarvis", "issues on the jarvis repo"
            m = re.match(r"^(?:the |my )?(?:recent |latest |open |last )?(%s) (?:for|on|of|in|from) (.+)$" % pages,
                         said)
            if m:
                return self._repo(site, "%s %s" % (_REPO_WORD.sub("", _words(m.group(2))), m.group(1)), browser)
            if re.search(r"\s(?:%s|repo|repository)$" % pages, said):
                return self._repo(site, _words(said), browser)
        return None

    def _anywhere(self, said, browser):
        """A page by its name alone, if only one site has it ("bigg boss" is on Hotstar). "Stream" or "live" after
        it means one with channels ("rakazone" is on Kick and YouTube; "the rakazone stream" is Kick)."""
        thing = _words(said)
        live = bool(_LIVE.search(" " + thing))
        names = [thing] + [n for n in (_KIND_END.sub("", " " + thing).strip(),) if n and n != thing]
        for name, prefix in [(n, False) for n in names] + [(n, True) for n in names]:  # "tbone": "tbone gaming"
            if _key(name) in _PLAIN_PAGES:
                continue
            hits = [(site, page) for site, info in self.reg.sites.items()
                    if not live or info.get("channel")
                    for page in [self._lookup(info.get("pages") or {}, name, prefix=prefix)] if page]
            if len(hits) == 1:
                site, page = hits[0]
                return self._mine(site, page[1], "%s on %s" % (page[0], self._title(site)), browser, None)
        return None

    def _thing(self, site, thing, browser, account, named=False, kind=""):
        """A page of `site` called `thing`. named: "<thing> on <site>", where anything goes (a channel, a search)."""
        info = self.reg.sites[site]
        bare = _KIND_END.sub("", " " + thing).strip() or thing  # "rakazone's channel", "kick events page"
        page = self._lookup(info.get("pages") or {}, thing) or self._lookup(info.get("pages") or {}, bare)
        if page:
            return self._mine(site, page[1], ("%s on %s" if named else "%s %s") % (
                (page[0], self._title(site)) if named else (self._title(site), page[0])), browser, account)
        repo = self._repo(site, thing, browser) or self._repo(site, bare, browser)
        if repo:
            return repo
        if not named:
            return None
        if info.get("channel") and not re.match(r"^(?:show|repo|repository|project)s?$", kind):
            return BrowserAction([info["channel"].replace("{name}", quote(_key(bare)))], browser,
                                 "%s on %s" % (bare, self._title(site)))
        if info.get("search"):
            return self._search(site, thing, browser)
        return None

    def _lookup(self, pages, thing, prefix=True):
        """(name, url) of the page `thing` names: the same name, or the start of one only one page has."""
        want = _key(thing)
        if not want:
            return None
        hit = [(n, u) for n, u in pages.items() if _key(n) == want]
        if not hit and prefix and len(want) >= 4:
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
            return BrowserAction([url.replace("{me}", accounts[who])], self._account_browser(site, who, browser),
                                 "your %s%s" % ("%s " % who if len(accounts) > 1 else "", description))
        # each one known by its word and its account's name: "work", "the biztel one", "chetakk"
        choices = {"%s %s %s" % (who, {"work": "office", "personal": "own"}.get(who, ""), re.sub(r"[-_.]", " ", name)):
                   BrowserAction([url.replace("{me}", name)], self._account_browser(site, who, browser),
                                 "your %s %s" % (who, description))
                   for who, name in accounts.items()}
        return BrowserAction([], browser, description, choices=choices,
                             question="Your %s %s, or %s?" % (list(accounts)[0], self._title(site), ", or ".join(
                                 "the %s one" % w for w in list(accounts)[1:])))

    def _account_browser(self, site, who, browser):
        """The browser that account is logged in on ("browsers: {work: firefox}"), unless one was named."""
        return browser or (self.reg.sites[site].get("browsers") or {}).get(who)

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
        owner = hit[1].split("/")[0].lower()
        who = next((w for w, login in (info.get("accounts") or {}).items() if login.lower() == owner), None) or \
            next((w for w, orgs in (info.get("orgs") or {}).items() if owner in (o.lower() for o in orgs)), None)
        return BrowserAction([self._full(site, hit[1]) + ("/" + path if path else "")],
                             self._account_browser(site, who, browser), "%s%s" % (hit[0], " " + sub if sub else ""))
