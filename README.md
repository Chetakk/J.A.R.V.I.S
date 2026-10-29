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

Edit `config/settings.yaml` to set paths for the Piper voice, whisper.cpp model and your own names.
