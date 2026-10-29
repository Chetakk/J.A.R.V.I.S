"""What every skill is: phrase patterns, each with a handler. A handler may return False to let the request
carry on (to the next skill, the command list, the browser or Claude)."""
import re


class Skill:
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
