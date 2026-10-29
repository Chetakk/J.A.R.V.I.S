"""Built-in browser phrases: open sites/URLs/tab groups and run searches, in any browser."""
import re
import shlex
from urllib.parse import quote_plus

_DOMAIN = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$", re.IGNORECASE)
_SPLIT_AND = re.compile(r"\s*(?:,|\band\b|&)\s*", re.IGNORECASE)


class BrowserAction:
    """A resolved browser request: which URLs, in which browser."""

    def __init__(self, urls, browser, description):
        self.urls = urls
        self.browser = browser  # key from `browsers`, or None for the system default
        self.description = description

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

        # "search youtube for X", "search X on youtube", "youtube X", "play X on youtube"
        for site, info in self.reg.sites.items():
            if not info.get("search"):
                continue
            s = re.escape(site)
            for pat in (r"^(?:search|find|look up)\s+(?:on\s+)?%s\s+for\s+(.+)$" % s,
                        r"^(?:search|find|look up|play)\s+(.+?)\s+on\s+%s$" % s,
                        r"^%s\s+(?:search\s+)?(?:for\s+)?(.+)$" % s):
                m = re.match(pat, text, re.IGNORECASE)
                if m:
                    return self._search(site, m.group(1), browser)

        # "google X", "search for X", "search X"
        m = re.match(r"^(?:google|search for|search|look up)\s+(.+)$", text, re.IGNORECASE)
        if m:
            return self._search("google", m.group(1), browser)

        # "open X", "open X and Y", "go to X", "open my work tabs"
        m = re.match(r"^(?:open|go to|launch|show)\s+(?:my\s+|the\s+)?(.+?)(?:\s+(?:tabs?|website|web site|site|page))?$",
                     text, re.IGNORECASE)
        if m:
            target = m.group(1).lower()
            if target in self.reg.tab_groups:
                urls = [self._url(t) for t in self.reg.tab_groups[target]]
                return BrowserAction([u for u in urls if u], browser, "your %s tabs" % target)
            parts = [p for p in _SPLIT_AND.split(m.group(1)) if p]
            urls = [self._url(p) for p in parts]
            if all(urls):
                return BrowserAction(urls, browser, " and ".join(parts))
        return None

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

    def _url(self, name):
        name = name.strip()
        site = self.reg.sites.get(name.lower())
        if site:
            return site["url"]
        if _DOMAIN.match(name):
            return name if re.match(r"^https?://", name, re.IGNORECASE) else "https://" + name
        return None

    def _search(self, site, query, browser):
        url = self.reg.sites[site]["search"].replace("{q}", quote_plus(query.strip()))
        return BrowserAction([url], browser, "%s for %s" % (site.capitalize(), query.strip()))
