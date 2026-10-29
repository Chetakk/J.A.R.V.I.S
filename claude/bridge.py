"""Asks Claude (via the Claude Code CLI) when nothing in the command list matches.

JARVIS's questions all go into one Claude session, so Claude remembers the conversation.
"Open Claude" opens a terminal with a copy of that session, guarded by claude/hook.py.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import quote

from system import desktop
from system.terminal import RUN_DIR

_HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(_HERE, "..", "config", "claude_prompt.md")
HOOK = os.path.join(_HERE, "hook.py")
STATE_DIR = os.path.expanduser("~/.local/share/jarvis")
WORKSPACE = os.path.expanduser("~/jarvis-workspace")
TERMINAL_ROLE = "jarvis-claude"
TASK_ROLE = "jarvis-claude-task"  # one-off terminals for project tasks
TERMINAL_PID = os.path.join(RUN_DIR, "claude_terminal.pid")
VOICE_PROMPT = (
    "The user also hears you through their voice assistant, JARVIS. End every reply with one final line "
    "that starts with 'SAY:' followed by one or two short, plain, conversational sentences summarizing what "
    "you did or found, written to be read aloud: no markdown, code, file paths, URLs or symbols. For long "
    "tasks, summarize the outcome and anything the user needs to do next.")
# How any Claude reaches the running JARVIS (tools/jarvis, on the PATH as `jarvis`).
JARVIS_TOOL = (
    "You can also use JARVIS through the `jarvis` shell command: `jarvis show \"Title\" \"markdown\"` puts a card on "
    "the user's screen until they close it (# heading, - bullets (indent to nest), 1. numbered, - [ ] / - [x] "
    "checklists, | table |, ``` blocks, > quote, --- divider, **bold**, *italic*, `code`, [links](https://...); "
    "--image /path.png adds a picture, --choice \"phrase\" adds a button, --id NAME updates that card in place for "
    "progress, --refresh \"command\" --every 5 keeps it live), `jarvis say \"text\"` speaks a short update mid-task, "
    "and `jarvis do \"phrase\"` has JARVIS do one of its own abilities as if the user said it. Show things worth "
    "seeing (results, tables, links, steps), speak short things, and keep the final SAY line for the summary.")


# Thinking only adds delay for these short answers; non-essential traffic is the background call
# that titles each conversation (and similar extras) - JARVIS never uses those.
_ENV = dict(os.environ, MAX_THINKING_TOKENS="0", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")


class ClaudeError(Exception):
    pass


class Claude:
    def __init__(self, cfg, name="Jarvis", user="the user", abilities=""):
        self.model = cfg.get("model", "claude-haiku-5-5")
        self.timeout = cfg.get("timeout", 60)
        self.effort = cfg.get("effort", "low")
        self.idle = cfg.get("new_after_minutes", 20) * 60  # quiet this long -> next question starts fresh
        self.bin = shutil.which("claude")
        with open(PROMPT_FILE) as f:
            self.prompt = f.read().replace("{abilities}", abilities).replace("{name}", name).replace("{user}", user)
        self.session_file = os.path.join(STATE_DIR, "claude_session")
        self.usage_file = os.path.join(STATE_DIR, "claude_usage.json")
        self.abilities = abilities
        try:  # for `jarvis` (tools/jarvis) to list, so Claude in VS Code can find them too
            os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
            with open(os.path.join(RUN_DIR, "abilities.txt"), "w") as f:
                f.write(abilities + "\n")
        except OSError:
            pass
        os.makedirs(WORKSPACE, exist_ok=True)

    @property
    def session(self):
        try:
            if time.time() - os.path.getmtime(self.session_file) > self.idle:
                self.new_session()
                return None
            with open(self.session_file) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def new_session(self):
        try:
            os.remove(self.session_file)
        except OSError:
            pass

    def usage_today(self):
        """How much JARVIS has used Claude today: {"date", "questions", "tokens", "cost"}."""
        today = time.strftime("%Y-%m-%d")
        try:
            with open(self.usage_file) as f:
                usage = json.load(f)
        except (OSError, ValueError):
            usage = {}
        if usage.get("date") != today:
            usage = {"date": today, "questions": 0, "tokens": 0, "cost": 0.0}
        return usage

    def _count(self, result):
        usage, u = self.usage_today(), result.get("usage") or {}
        usage["questions"] += 1
        usage["tokens"] += sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens", "output_tokens"))
        usage["cost"] = round(usage["cost"] + (result.get("total_cost_usd") or 0), 5)
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(self.usage_file, "w") as f:
                json.dump(usage, f)
        except OSError:
            pass

    def ask(self, text, recent=(), on_say=None, image=None, about=None):
        """Return Claude's answer as a dict with say/details/command/run_now/output/save/steps/spoken.

        recent: lines about what JARVIS did on its own since the last question, so Claude can follow along.
        on_say(text): called as soon as the "say" part has streamed in, before the rest of the answer
            (then answer["spoken"] is True), so JARVIS starts talking sooner.
        image: path of a screenshot (inside WORKSPACE) for Claude to look at.
        about: one line on who is asking and how (user, voice/typed, time, habits, usage), put first.
        """
        if not self.bin:
            raise ClaudeError("Claude Code isn't installed")
        if recent:
            text = "Since your last answer, without you:\n%s\n\nUser now says: %s" % (
                "\n".join("- " + line for line in recent), text)
        if image:
            text = ("A screenshot of the user's screen is in the file %s in the current folder. Look at it with "
                    "the Read tool first, then answer. To show it to them on a card, use \"image\": \"%s\" in "
                    "\"show\".\n\nUser says: %s" % (os.path.basename(image), image, text))
        if about:
            text = "[%s]\n%s" % (about, text)
        args = [self.bin, "-p", text, "--model", self.model, "--tools", "Read" if image else "",
                "--strict-mcp-config", "--system-prompt", self.prompt, "--effort", self.effort,
                "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
        sid = self.session
        args += ["--resume", sid] if sid else ["--session-id", str(uuid.uuid4())]
        proc = subprocess.Popen(args, cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=_ENV)
        timer = threading.Timer(self.timeout, proc.kill)
        timer.start()
        block, result, spoken = "", None, False
        try:
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "result":
                    result = event
                    continue
                ev = event.get("event") or {}
                if ev.get("type") == "content_block_start":
                    block = ""
                elif ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                    block += ev["delta"]["text"]
                    m = _SAY.search(block) if on_say and not spoken else None
                    if m:
                        spoken = True
                        try:
                            on_say(json.loads('"%s"' % m.group(1)))
                        except ValueError:
                            spoken = False
            proc.wait()
        finally:
            timed_out = not timer.is_alive() and proc.returncode not in (0, None) and result is None
            timer.cancel()
        if timed_out:
            raise ClaudeError("Claude took too long to answer")
        if result is None:
            if sid and "session" in proc.stderr.read().lower():
                self.new_session()  # Stale session; start fresh next time.
            raise ClaudeError("I couldn't reach Claude")
        self._count(result)
        if result.get("is_error"):
            raise ClaudeError(result.get("result") or "Claude returned an error")
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(self.session_file, "w") as f:
            f.write(result.get("session_id", ""))
        answer = _parse_answer(result.get("result", ""))
        answer["spoken"] = spoken
        return answer

    def rewrite(self, instruction, content):
        """One-off text job ("fix the grammar", "translate to Hindi"): returns just the resulting text."""
        if not self.bin:
            raise ClaudeError("Claude Code isn't installed")
        args = [self.bin, "-p", "%s\n\n<text>\n%s\n</text>" % (instruction, content), "--model", self.model,
                "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--effort", self.effort,
                "--system-prompt", "Do what the user asks with the text between <text> tags. Reply with only the "
                                   "result: no preamble, no quotes, no tags, no explanation.",
                "--output-format", "json"]
        try:
            res = subprocess.run(args, cwd=WORKSPACE, capture_output=True, text=True, timeout=self.timeout,
                                 env=_ENV)
            out = json.loads(res.stdout)
            self._count(out)
        except subprocess.TimeoutExpired:
            raise ClaudeError("Claude took too long to answer")
        except ValueError:
            raise ClaudeError("I couldn't reach Claude")
        if out.get("is_error"):
            raise ClaudeError(out.get("result") or "Claude returned an error")
        return out.get("result", "").strip()

    def open_terminal(self, cwd=None, prompt=None, model=None):
        """Bring up the Claude terminal if Claude is still running in it; else open one, continuing a copy
        of JARVIS's conversation. Returns True if an existing one was brought up.

        cwd / prompt: a separate task terminal (e.g. "run the tests in study buddy") that starts in that
        folder with that request. It always opens fresh, with its own window role, and is never reused as
        the main Claude terminal."""
        task = bool(cwd or prompt)
        if not task:
            try:
                with open(TERMINAL_PID) as f:
                    os.kill(int(f.read()), 0)
                if desktop.activate_role(TERMINAL_ROLE):
                    return True
            except (OSError, ValueError):
                pass
        hook = [{"type": "command", "command": "%s %s" % (shlex.quote(sys.executable), shlex.quote(HOOK))}]
        settings = json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": hook}],
                                         "Stop": [{"hooks": hook}],
                                         "UserPromptSubmit": [{"hooks": hook}],
                                         "Notification": [{"hooks": hook}]}})
        args = ["claude", "--model", model or self.model, "--settings", settings, "--append-system-prompt",
                "%s\n\n%s Phrases for `jarvis do`:\n  %s" % (VOICE_PROMPT, JARVIS_TOOL, self.abilities)]
        if not task and self.session:
            args += ["--resume", self.session, "--fork-session"]
        if prompt:
            args.append(prompt)
        cmd = " ".join(shlex.quote(a) for a in args)
        folder = shlex.quote(cwd or WORKSPACE)
        if task:
            inner = "cd %s && %s; exec bash" % (folder, cmd)
            title, role = "JARVIS: Claude \u00b7 %s" % os.path.basename(cwd or WORKSPACE), TASK_ROLE
        else:
            # The pid file exists only while Claude runs, so a window left at a bare shell isn't reused.
            pid = shlex.quote(TERMINAL_PID)
            inner = "echo $$ > %s; cd %s && %s; rm -f %s; exec bash" % (pid, folder, cmd, pid)
            title, role = "JARVIS: Claude", TERMINAL_ROLE
        desktop.go_to_jarvis_workspace()
        subprocess.Popen(["gnome-terminal", "--title", title, "--role", role, "--", "bash", "-c", inner],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return False


def open_in_vscode(prompt=None):
    """Open the Claude Code panel in VS Code, optionally starting it with `prompt`."""
    url = "vscode://anthropic.claude-code/open"
    if prompt:
        url += "?prompt=" + quote("%s\n\n(%s Run `jarvis` alone to see the phrases.)" % (prompt, JARVIS_TOOL))
    desktop.go_to_jarvis_workspace()
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


_SAY = re.compile(r'"say"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _parse_answer(text):
    answer = {"say": "", "details": "", "command": "", "run_now": False, "output": False, "save": None,
              "steps": None, "suggest": None, "fix": None,
              "misheard": None, "show": None, "do": None}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # Claude answered in plain text: speak the first bit, show the rest.
        answer["say"] = text.strip().split("\n")[0][:300]
        answer["details"] = text.strip()
        return answer
    for key in answer:
        if key in data and data[key] is not None:
            answer[key] = data[key]
    answer["command"] = str(answer["command"] or "").strip()
    return answer
