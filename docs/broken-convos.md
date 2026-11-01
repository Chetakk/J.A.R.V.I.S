# Conversations that broke JARVIS (2026-10-30)

23 everyday conversations (131 turns) were run in the sealed test harness (nothing touched the desktop).
Misses that teaching can fix were added to `~/.local/share/jarvis/misses.jsonl`, tagged `"flag": "convo test"`.
The ones below need code changes: teaching can't reach them.

## Yes/no answers read as "no"
To "Close discord? Anything unsaved will be lost.", each of these cancelled:
`go for it`, `please`, `haan`, `haan kar do`, `of course`, `obviously`, `affirmative`, `absolutely`.
(`yes please`, `yeah`, `that's okay close it`, `just close it already` worked; `no don't`, `wait no`, `not now` correctly cancelled.)
Fix: the meaning check for answers (core idea agreed, not built).

## Does the wrong thing
| Said | Did | Meant |
|---|---|---|
| show me the desktop | Opening Vesktop | show the desktop (hide windows) |
| stop listening | private mode: "I can't find any headphones…" | mute the mic / stop hearing |
| go to sleep | asks to suspend the computer | JARVIS stop listening (probably) |
| make the text bigger → make it smaller | zoom out the browser | text smaller |
| open files → okay now close it | closed the card | close Files |
| close firefox and open chrome | `pkill -x 'firefox and open chrome'`, "Closed firefox and open chrome" | two steps |
| close the current app | `pkill -x 'current app'` | close the focused app |
| turn on night light | asks "Night light is on?" | just turn it on (question is its reply with a "?") |

## Doesn't know what "it" / "this" is
- open discord → `kill it`: "I'm not sure what to close" (last_app should be discord)
- `close this`: "I'm not sure what to close" (`close this window` works)
- `no not that`, `the other one`, `i said close` after an action: lost track or Claude

## Crashes
- `why is my computer slow`: "Sorry, that didn't work: list index out of range." (no processes listed; skills/slow_computer.py)

## Not split into steps
- `open discord and set volume to 40` → Claude
- `mute and pause the music` → muted, then Claude

## Media
- `play some music`, `pause`, `stop the music` → Claude after matching (Study Buddy takes play/pause; same as the failing test_registry media case)

## Not checked here (harness limits)
Weather (no network), reminders and to-dos (Day Tracker hub not running), window moves (no windows), opening Discord (its app entry isn't in the sealed HOME).
