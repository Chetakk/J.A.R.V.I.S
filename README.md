# J.A.R.V.I.S

A light, fast voice/text assistant for Linux (built on a Jetson) that handles OS and browser tasks.

- **Wake word**: Whisper (GPU) or a small CPU keyword model
- **Speech-to-text**: whisper.cpp server
- **Text-to-speech**: Piper
- **Modes**: `/agent` (command list first, then Claude) or `/sys` (command list only, no AI)
- **Safety**: risky shell commands are confirmed or sent to a manual terminal

## Layout

- `jarvis.py`: main loop
- `core/`: wake word, listening, STT, speech, commands, skills, Claude bridge, UI
- `config/`: settings, command list, safety rules, Claude prompt
- `tools/`: launcher scripts

## Run

```sh
tools/run_jarvis.sh
```

Edit `config/settings.yaml` to set paths for the Piper voice, whisper.cpp model and your own names.
