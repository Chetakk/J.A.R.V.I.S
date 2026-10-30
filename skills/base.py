"""What every skill is: phrase patterns, each with a handler. A handler may return False to let the request
carry on (to the next skill, the command list, the browser or Claude)."""
import re


PERMISSIONS = ("auto", "confirm", "manual")


class Skill:
    """Each skill says what it is (skills/registry.py collects these; Claude's list of what JARVIS can do and
    tools/skills.py come from them, and tests/test_registry.py checks them):

      does        what it does, one line
      phrases     what you can say ("<x>" is a value you fill in); these are also what Claude may "do"
      gives       what you get back: spoken, a card, something done on the desktop...
      permission  the most it does without you: "auto" (just does it), "confirm" (asks first for some things,
                  see asks), "manual" (only ever pre-types commands in your terminal)
      asks        when it asks first ("" for auto)
      checks      how it knows it worked, or why it can't tell
      group       the label on Claude's list (default: the skill's name)
    (Not "about" / "output" / "verify": some skills have handler methods with those names.)
    """
    does = ""
    phrases = ()
    gives = ""
    permission = "auto"
    asks = ""
    checks = ""
    group = ""

    def __init__(self, jarvis):
        self.j = jarvis
        self.say = jarvis.speaker.say
        self.rules = [(re.compile("^(?:%s)$" % p, re.IGNORECASE) if isinstance(p, str) else p, fn)
                      for p, fn in self.patterns()]

    def patterns(self):
        """[(regex string or compiled pattern, handler(match))], tried in order."""
        return []

    def handle(self, text):
        """True if this skill took care of `text`."""
        text = text.strip().rstrip("?.!")
        for pattern, fn in self.rules:
            m = pattern.match(text)
            if m:
                try:
                    done = fn(m)
                except Exception as e:  # a broken skill must never take JARVIS down
                    print("[%s] %s failed: %r" % (type(self).__name__, getattr(fn, "__name__", "skill"), e))
                    self.say("Sorry, that didn't work: %s." % e)
                    return True
                if done is not False:
                    return True
        return False
