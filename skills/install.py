""""Install vlc", "install the python package requests", "uninstall gimp", "is ffmpeg installed": JARVIS looks the
software up itself (system/packages.py, read-only), then hands sudo apt install / remove or pip3 install --user to
the executor. Its safety rules always send sudo and pip to the manual terminal, pre-typed: nothing is installed
until you press Enter there and type your password. Something apt doesn't have goes to Ubuntu Software (asked
first)."""
import re
import shlex

from skills.base import Skill
from skills.helpers import or_list
from system import packages

TAIL = r"(?: for me| please| on (?:my|this|the) (?:computer|jetson|machine|system))?"
KIND = r"(?:the |a )?(?:app |application |program |package |software |tool )?(?:called |named )?"
VAGUE = {"it", "this", "that", "them", "dependencies", "the dependencies", "deps", "requirements", "updates",
         "packages", "the packages", "everything", "something", "stuff"}


class Install(Skill):
    def patterns(self):
        return [
            (r"(?:please |can you |could you |go ahead and )?(?:pip3? install|install (?:the |a )?python (?:package|"
             r"library|module) (?:called |named )?) ?([\w.-]+)%s" % TAIL, self.pip),
            (r"(?:please |can you |could you |go ahead and )?(?:uninstall|remove (?:the )?(?:app|application|program|"
             r"package|software)) %s(.+?)%s" % (KIND, TAIL), self.uninstall),
            (r"(?:please |can you |could you |go ahead and |i want to |i need to |help me )?install %s(.+?)%s"
             % (KIND, TAIL), self.install),
            (r"(?:is|are) (.+?) installed(?: on (?:my|this|the) (?:computer|jetson|machine))?|(?:do|have) (?:i|we) "
             r"(?:got |have )?(.+?) installed", self.check),
        ]

    def install(self, m):
        words = m.group(1).strip()
        if words.lower() in VAGUE:
            return False  # "install the dependencies": a project's, not the system's
        pkg = packages.name_for(words)
        have = packages.installed(pkg)
        if have:
            self.say("%s is already installed%s." % (words, ", version %s" % _version(have) if have != "yes" else ""))
            return
        found = packages.info(pkg)
        if not found:
            options = packages.search(words)
            if len(options) == 1:
                found = packages.info(options[0][0])
            elif options:
                self.j.show({"title": "Packages for %s" % words, "text": "\n".join(
                    "- **%s**: %s" % o for o in options)})
                pick = self.j.choose("I found %s. Which one?" % or_list(o[0] for o in options),
                                     [o[0] for o in options])
                if not pick:
                    self.say("Okay, I won't install anything.")
                    return
                found = packages.info(pick)
        if not found:
            self.j.executor.run("gnome-software --search=%s" % shlex.quote(words), reply="Opening Ubuntu Software.",
                                question="%s isn't in the Ubuntu packages. Look for it in Ubuntu Software?" % words,
                                window=True)
            return
        self.j.show({"title": "Install %s" % found["name"], "text": "**%s** %s\n\n%s\n\nIn your terminal: "
                     "`sudo apt install %s`, then Enter and your password." % (
                         found["name"], found["version"], found["summary"], found["name"])})
        self.say("That's %s, version %s: %s." % (found["name"], _version(found["version"]), found["summary"]))
        self.j.executor.run("sudo apt install %s" % found["name"])

    def pip(self, m):
        name = m.group(1)
        if not packages.PIP_NAME.match(name):
            self.say("That doesn't look like a python package name.")
            return
        have = packages.pip_installed(name)
        if have:
            self.say("The python package %s is already installed, version %s." % (name, have))
            return
        self.say("The python package %s goes in just for you, with pip3 install --user." % name)
        self.j.executor.run("pip3 install --user %s" % name)

    def uninstall(self, m):
        words = m.group(1).strip()
        if words.lower() in VAGUE:
            return False
        pkg = packages.name_for(words)
        have = packages.installed(pkg)
        if not have or have == "yes" or not packages.APT_NAME.match(pkg):
            self.say("I can't find an installed package called %s." % words if not have else
                     "%s isn't from the Ubuntu packages, so I can't remove it that way." % words)
            return
        self.say("Removing %s %s; your own settings for it are kept." % (pkg, _version(have)))
        self.j.executor.run("sudo apt remove %s" % pkg)

    def check(self, m):
        words = next(g for g in m.groups() if g).strip()
        if words.lower() in VAGUE:
            return False
        pkg = packages.name_for(words)
        have = packages.installed(pkg) or packages.pip_installed(pkg)
        if have:
            self.say("Yes, %s is installed%s." % (words, ", version %s" % _version(have) if have != "yes" else ""))
        else:
            self.say("No, %s isn't installed. Say install %s if you want it." % (words, words))


def _version(v):
    """"1:85.0.4183.83-0ubuntu0.20.04.3" -> "85.0.4183.83": no epoch, no Ubuntu revision."""
    return re.sub(r"^\d+:|-[^-]*$", "", v)
