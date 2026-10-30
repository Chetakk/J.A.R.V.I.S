"""Claude Code hooks for the terminal JARVIS opens.

PreToolUse applies the JARVIS safety rules:
  shell commands  auto -> allowed, confirm -> Claude Code asks you, manual -> blocked and
                  sent pre-typed to the JARVIS manual terminal
  file edits      allowed inside ~/jarvis-workspace, ask anywhere else
  other built-in tools (read, search, web) are allowed; MCP tools keep Claude Code's normal prompts
  Switched to auto mode (or bypass), nothing asks: Claude Code's own mode decides instead. In accept-edits
  mode, file edits don't ask either. "manual" commands stay blocked in every mode.
Stop speaks the "SAY:" summary line Claude ends each reply with ("All done." first after a long task;
UserPromptSubmit notes when the task started).
Notification speaks Claude's alerts (e.g. when it is waiting for your permission).
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.safety import AUTO, CONFIRM, Safety  # noqa: E402
from voice.speech import pick, speak_via_jarvis  # noqa: E402
from system.terminal import RUN_DIR, ManualTerminal  # noqa: E402

WORKSPACE = os.path.realpath(os.path.expanduser("~/jarvis-workspace"))
TURN_START = os.path.join(RUN_DIR, "claude_turn_start")
LONG_TASK = 60  # seconds
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
NO_ASKING = {"auto", "bypassPermissions"}  # modes you picked so Claude stops asking: a hook "ask" would undo them


def decide(tool, args, cwd, mode="default"):
    decision, reason = _decide(tool, args, cwd)
    if decision == "ask" and (mode in NO_ASKING or mode == "acceptEdits" and tool in EDIT_TOOLS):
        return None, None
    return decision, reason


def _decide(tool, args, cwd):
    if tool == "Bash":
        command = args.get("command", "")
        level, reason = Safety().classify(command)
        if level == AUTO:
            return "allow", "JARVIS: safe command"
        if level == CONFIRM:
            return "ask", "JARVIS safety: this needs the user's approval because %s." % reason
        ManualTerminal().send(command, reason)
        return "deny", ("JARVIS safety: not allowed to run this because %s. It has been placed pre-typed in "
                        "the user's JARVIS manual terminal; tell the user to run it there themselves." % reason)
    if tool in EDIT_TOOLS:
        path = os.path.realpath(os.path.join(cwd or WORKSPACE, os.path.expanduser(args.get(EDIT_TOOLS[tool], ""))))
        if path == WORKSPACE or path.startswith(WORKSPACE + os.sep):
            return "allow", "JARVIS: inside the workspace"
        return "ask", "JARVIS: editing a file outside ~/jarvis-workspace"
    if tool.startswith("mcp__"):
        return None, None
    return "allow", "JARVIS: built-in tool"


def last_reply(transcript_path):
    """Text of Claude's final reply in this turn, from the session transcript."""
    text = ""
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except (OSError, TypeError):
        return text
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content") or []
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if parts:
            return "\n".join(parts)
    return text


def spoken_summary(reply):
    m = re.findall(r"^\W*SAY:\s*(.+)$", reply, re.MULTILINE)
    if m:
        return m[-1].strip()
    # No SAY line: fall back to the first sentence, without markdown.
    plain = re.sub(r"[`*_#>\[\]]", "", reply).strip()
    return re.split(r"(?<=[.!?])\s", plain, 1)[0][:200] if plain else ""


def main():
    event = json.load(sys.stdin)
    name = event.get("hook_event_name", "PreToolUse")
    if name == "UserPromptSubmit":
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        with open(TURN_START, "w") as f:
            f.write(str(time.time()))
        return
    if name == "Stop":
        summary = spoken_summary(event.get("last_assistant_message") or last_reply(event.get("transcript_path")))
        try:
            with open(TURN_START) as f:
                took = time.time() - float(f.read())
        except (OSError, ValueError):
            took = 0
        if summary and took > LONG_TASK:
            summary = pick(["All done. ", "That's finished. ", "Okay, that's done. "]) + summary
        if summary:
            speak_via_jarvis(summary, "Claude Code in %s finished a reply%s; its hooks tell me when it does." % (
                os.path.basename(event.get("cwd") or "") or "its terminal",
                " after %d minutes" % round(took / 60) if took > LONG_TASK else ""))
        return
    if name == "Notification":
        if event.get("message"):
            speak_via_jarvis(event["message"])
        return
    decision, reason = decide(event.get("tool_name", ""), event.get("tool_input") or {}, event.get("cwd"),
                              event.get("permission_mode") or "default")
    if decision:
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                          "permissionDecisionReason": reason}}, sys.stdout)


if __name__ == "__main__":
    main()
