# J.A.R.V.I.S

A light, fast voice/text assistant for Linux (built on a Jetson) that handles OS and browser tasks.

- **Wake word**: Whisper (GPU) or a small CPU keyword model
- **Speech-to-text**: whisper.cpp server
- **Text-to-speech**: Piper
- **Modes**: `/agent` (command list first, then Claude) or `/sys` (command list only, no AI)
- **Safety**: risky shell commands are confirmed or sent to a manual terminal

## Layout

- `jarvis.py`: main loop
- `core/`: the brain: command list, meaning matcher, executor, safety rules, undo, persona, missed requests
- `skills/`: everything JARVIS does itself, one file per skill (projects, notes, media, windows, focus mode...);
  `skills/__init__.py` sets the order they're tried in, `skills/base.py` is what a skill is
- `voice/`: wake word, listening, speech-to-text, speech
- `claude/`: the Claude bridge and the hook that guards the Claude terminal
- `ui/`: the window and cards
- `system/`: the desktop: windows, terminal, installed apps, browser, projects, health check, watchdog
- `config/`: settings, command list, wordings, safety rules, Claude prompt
- `tools/`: launcher, `jarvis` command for other Claudes, dry run (`tools/dryrun.py --check`), missed requests
- `tests/`: phrases and what each should do

To add a skill: a new `skills/<name>.py` with a `Skill` subclass whose `patterns()` returns
`[(regex, handler)]`, then add it to `ORDER` in `skills/__init__.py`.

## Run

```sh
tools/run_jarvis.sh
```

`tools/run_jarvis.sh` restarts JARVIS when it crashes and logs to a file. To see its output in a terminal instead, `biztel run jarvis` (`biztel stop jarvis` or Ctrl+C stops it), with this in `~/.config/biztel/run.json`:

```json
"J.A.R.V.I.S": { "services": [{ "name": "jarvis", "cmd": "python3 -u jarvis.py" }] }
```


Edit `config/settings.yaml` to set paths for the Piper voice, whisper.cpp model and your own names.

## Google (Calendar, Gmail, Chat, Drive)

Optional. With a Google account connected, JARVIS answers "do we have meetings today", "any new emails", "what did
Rahul say on chat", "find the budget sheet in my drive", gives a heads-up before meetings, says new direct messages
and important mail, and can schedule meetings, answer invites and send a Chat message (each asked about first) or
save a Gmail draft (never sent). Skills: `skills/google_calendar.py`, `gmail.py`, `google_chat.py`, `drive.py`, `wrap.py`.

1. In a Google Cloud project (Internal, under your Workspace organisation), turn on the Calendar, Gmail, Drive,
   Chat and People APIs, configure the Chat app (name, avatar, interactive features off) and create a
   **Desktop app** OAuth client.
2. Save its JSON as `~/.config/jarvis/google_client.json` (`chmod 600`; never in this repo).
3. `pip install --user google-api-python-client google-auth-oauthlib`, then say "connect Google" (or run
   `python3 -m system.google_account`) and allow it. The sign-in is kept in the GNOME Keyring.

Settings (`google:` in `config/settings.yaml`, all optional): `heads_up_minutes` (5), `meeting_mode` (true),
`mail_alerts` (true), `chat_alerts` (true), `workday` ([9, 18]).
