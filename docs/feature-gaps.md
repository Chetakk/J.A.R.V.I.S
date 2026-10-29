# J.A.R.V.I.S: feature gaps, what can and can't be done

What's missing compared with the full "JARVIS-like assistant" feature list, and for each gap:
**can be done** (and how), **can't be done / limits**, and **other options**.

Machine this is judged against (2026-10-29): Jetson Orin NX 16 GB, Ubuntu 20.04 (glibc 2.31),
Python 3.8, X11 (GNOME + Pop Shell), Google Chrome installed, ~9 GB disk free, ~5.5 GB RAM free.
Hard rule: everything stays light and fast. Local first, Claude (Haiku) only as the fallback.

Legend: ✅ mostly there · 🟡 partial · 🔴 mostly missing

---

## A. Natural conversation ✅

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Short vs detailed answers by voice | Yes, easily: "be brief" / "more detail" sets a flag added to Claude's header; local replies already short | – | Save it as a lasting preference (see 5. Memory) |
| Multiple languages / code-switching (Hinglish) | Partly: whisper `base` (multilingual, ~142 MB) instead of `base.en`; Piper has Hindi voices; Claude already understands mixed text | Mixed-language speech in one sentence is weak in small Whisper models; local command list + meaning matcher are English-only, so non-English mostly goes to Claude (slower) | Keep `base.en` for speed and switch model only with "speak Hindi"; `small` multilingual is better but ~3x slower |
| Barge-in (interrupt while it talks) | Yes: written and unit-tested, only uncommitted (waits on echo-filter work in voice/) | Over the Bluetooth/Echo speaker, JARVIS hears itself, so "interrupt with anything" will false-trigger | `barge_in: named` (say the name to interrupt) is safest on speakers; `any` with headphones |
| Talking to JARVIS vs someone else | Already: name required, follow-up window only 8 s | True "is this sentence for me" detection isn't reliable on tiny models | Shorter follow-up window, or "stop listening" phrase |

## B. Voice commands for the computer ✅

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| "Arrange my coding windows" | Yes: named layouts in YAML (app → workspace + left/right/max), uses existing windows skill | – | "Save this layout" captures current window positions |
| "File I downloaded this morning" | Yes: filter `~/Downloads` by mtime with words like today / this morning / yesterday | – | Track downloads as they arrive (inotify) for instant answers |
| "Read this window aloud" | Partly: AT-SPI text (python Atspi is installed) for GTK apps; screenshot → Claude for others | Chrome needs `--force-renderer-accessibility`; VS Code/Electron expose little text; no local OCR (tesseract not installed) | Browser pages via the browser agent (section 3) give clean text; tesseract is ~30 MB if wanted |
| "Open the project I was working on yesterday" | Yes: Day Tracker + where.json already know recent projects | – | – |

## C. Screen-aware interaction ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Read UI tree (buttons, menus, dialogs) | **Done:** "what can I click here", "what's in the file menu" (skills/controls.py). AT-SPI lists a GTK window's buttons, tabs and menu items (even with the menu closed) in ~0.15 s; when the tree is empty, a screenshot goes to Claude | Chrome, VS Code and Electron apps (Vesktop) expose 0–3 nodes. Anything started from VS Code's terminal inherits `NO_AT_BRIDGE=1` and is invisible to AT-SPI | Chrome with `--force-renderer-accessibility` gives a full tree (costs some speed, so it isn't on by default); VS Code `"editor.accessibilitySupport": "on"` exposes a little more |
| Click a button / do an action in the current app | **Done:** "click save", "press the back button", "choose zoom in from the view menu", "press control shift t", "hit enter". Tried surest first: the app's shortcut (config/app_keys.yaml), then AT-SPI `do_action` (no pointer used), then a spot Claude finds on a window screenshot. That spot is shown marked on a card and clicked only after a yes, and not if the window moved since. Buttons that delete or send are asked about first | Claude's spot was exact on a test window (sized so it isn't scaled down) but takes ~5 s. Terminals only use their own shortcuts (ctrl+c / ctrl+z mean something else there) | Add more apps to config/app_keys.yaml as they come up |
| Verify an action worked | **Done:** every launch is watched (core/verify.py): a new window or a changed title counts as done; a non-zero exit is said out loud with its error. After a click the window's pixels are compared (~0.1 s, JARVIS's own orb and cards masked), and if nothing changed JARVIS says so. "Did that work?" answers from what it saw | Key presses and things like "copy" leave no visible trace: reported as "couldn't tell", not as done. Window apps' errors aren't captured (they write to stderr for days), only their exit code | When it couldn't tell, "did that work?" offers a screenshot for Claude to check (slow, so only when asked) |
| "Why isn't my app running?" | **Done:** skills/troubleshoot.py reads biztel run's state, the ports its folder's programs listen on, the log `biztel run` now keeps, and the terminal's visible text. Clear cases are said locally (never started, port taken and by what, the last error before it stopped); unclear ones go to Claude with those facts. Also "what's using port 3000", "what ports are open" | AT-SPI only gives a terminal's visible screen, not its scrollback. Projects started by hand outside `biztel run` have no log | `biztel run` keeps `~/.local/state/biztel/runs/<name>.log` (last ~1 MB, kept after it stops). For other terminals: run under `script` or tmux to keep a log |

## D. Personality and presence ✅

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Speaking speed by voice | Yes: Piper `length_scale` ("talk faster/slower") | – | – |
| Verbosity by voice | Yes (same as A) | – | – |
| Headphones-only / private mode | Yes: `pactl` is installed, can route JARVIS's player to a chosen sink | Only if headphones are connected; Bluetooth switching takes a few seconds | "Quiet mode": cards only, no speech |
| Summarise long text instead of reading it all | Yes: over N words → Claude summary spoken, full text on a card | – | Read the first 2 sentences locally, offer "read the rest" |
| Mic indicator | Already: orb + tray | – | – |

## 3. Browser agent 🔴

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Read pages, extract facts, summarise tabs | Yes: Playwright (Node, already have node) with `channel: chrome`, so no browser download; separate Chrome profile with a debug port | Chrome 136+ won't allow the debug port on your normal profile, so the agent's Chrome has its own logins; Playwright Python's last 3.8 release is 1.48 (old), so Node is the better choice | Chrome extension talking to JARVIS over a local socket: works in your real profile, more work to build |
| Research with sources / compare | Yes: agent fetches pages → text → Claude compares, cites URLs, card output | Slow (many pages × Claude); Haiku is weaker at long research | Hand big research to a Claude Code task terminal (it has WebSearch/WebFetch) |
| Fill forms, multi-page workflows | Yes for simple ones, with confirm before submit | Captchas, logins with 2FA and heavy JS sites will fail sometimes; must never submit/pay without asking | – |
| Download / upload files | Yes via Playwright | Upload needs explicit file permission each time | – |
| Bookmarks | Yes: Chrome's `Bookmarks` JSON file can be read locally (read-only while Chrome runs) | Writing bookmarks safely needs the extension route | – |
| YouTube summaries | Yes when a transcript exists (fetch captions → Claude) | No transcript = can't (local Whisper on a full video is too slow) | – |
| Prompt-injection defence | Yes: page text always wrapped as untrusted data in Claude's prompt; agent tools allowlisted; secrets never in prompt | No defence is perfect; keep high-risk actions behind confirm | – |

## 4A. Application control 🟡

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Detect frozen apps | Yes: `_NET_WM_PING` check or process state; offer to close (slow_computer skill already offers) | – | – |
| Click inside apps / menus | Done, see C (shortcut → AT-SPI → Claude's spot, asked first) | Same limits as C | – |
| Copy info between apps | Yes: clipboard skill + typing helper exist; chain them | – | – |

## 4B. File intelligence 🔴

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Search by date / extension | Yes, quick: `find` with mtime/ext | – | – |
| Search by content | Yes: `grep -rl` on text/code, `pdftotext` (installed) for PDFs | Slow over the whole home folder; office files need extra libs | Small SQLite index of chosen folders, rebuilt in background (sqlite3 CLI not installed, but Python's sqlite3 is built in) |
| Read PDFs / docs / sheets / slides | PDFs yes (`pdftotext`); docx/xlsx/pptx are zip+XML, readable with stdlib | Scanned PDFs need OCR (not installed) | Send the file to Claude Code task (it reads them well) |
| Summarise / extract across files | Yes: text → Claude | Big files cost time; cap the size | – |
| Duplicates | Yes: size then hash, stdlib only | – | – |
| Organise / rename / create folders | Yes, with confirm + undo log (undo.py exists) | Never delete permanently: move to Trash (`gio trash`) | – |
| Meaning-based file search | Possible: MiniLM is already loaded, could embed file names/first lines | Embedding whole documents uses RAM/disk; keep it to names + headings | – |

## 4C. System intelligence 🟡

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Network diagnostics | Yes: `nmcli`, ping gateway/DNS, explain result | – | – |
| Audio device choice | Yes: `pactl` (sinks, sources, mic volume; mic is at 27%) | Bluetooth (Echo Dot) has its own connect services | – |
| Read / summarise notifications | Yes: `dbus-monitor` on org.freedesktop.Notifications (installed); keep in memory only | Can't read notifications already shown before JARVIS started | – |
| Display settings | Partly: `xrandr` read + simple changes, confirm | Risky (wrong mode = black screen): needs auto-revert timer | – |
| Install software | Only through the safety gate (confirm/manual terminal) | Never sudo automatically | – |

## 5. Personal memory 🟡

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Structured preferences ("I prefer battery life") | Yes: `preferences` in memory.json with topic + value, sent to Claude when relevant | – | – |
| Show / edit / delete memory | Yes: "what do you know about me" → card with delete buttons; "forget X" | – | – |
| Meaning-based recall | Yes: MiniLM embeddings over notes (already loaded, ~20 ms) | – | – |
| Short-term vs project vs long-term | Yes: already three places (session, project notes, notes); JARVIS asks before saving | – | – |
| Sensitive info | Partly: mark items private (never sent to Claude) | No encryption store on this box yet | GNOME Keyring (secret-tool) for anything secret |

## 6. Autonomous agent 🔴 (most important after the browser)

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Task state (done / remaining / failed) | Yes: one task record per multi-step request, shown on a card | – | – |
| Verify each step | Yes: each action type gets a check (window, process, file, URL) | Some steps can't be verified (see C) → report "not checked" honestly | – |
| Retry / recover | Yes: bounded (e.g. 1 retry, then ask) | No unlimited loops; never retry destructive steps | – |
| Pause / resume | Yes: task record saved to disk, "continue the task" | – | – |
| Claude driving JARVIS's own tools | Yes: let fallback Claude call an allowlisted set of JARVIS phrases (`jarvis do` exists) through the safety gate | Haiku plans weaker than bigger models; more steps = more seconds | Big tasks → Claude Code task terminal (already exists) |
| Specialised agents | Later: browser / files / coding agents = separate Claude calls with only their tools | Each extra agent costs time and tokens | – |

## 7. Productivity 🟡

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Email (search, summarise, draft) | Yes via Gmail API or the claude.ai Gmail connector | Needs you to authorise it; sending always asks first | Open Gmail in the browser agent |
| Calendar | Yes via Google Calendar connector/API | Needs authorisation | – |
| Meeting summaries | Yes from a transcript/recording file | Live meeting transcription with base.en is possible but heavy for long meetings | – |
| Daily briefing | Yes: todos + Day Tracker + calendar (once connected) → card + short speech | – | – |
| Notes / tasks / reminders | Already (notes, todos, Day Tracker) | – | Obsidian/Notion only if you use them |

## 8. Coding ✅

Already strong through Claude Code (task terminals, VS Code, safety hook).
Small additions that **can be done**: "why did the tests fail" (read last `biztel run`/test log),
"what's using port 3000" (`ss -ltnp`), "show me the diff" on a card before committing.
**Can't / shouldn't**: deploys, credential changes, DB migrations without asking.

## 9. Multimodal 🟡

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Images / diagrams | Yes: same path as screenshots (Claude Read tool) | – | – |
| Local OCR | Possible: tesseract ~30 MB | Uses disk; Claude vision is already better | Skip unless offline is needed |
| Recordings | Yes: whisper.cpp on a file, then Claude summary | Long recordings take minutes on GPU | – |
| Documents | See 4B | – | – |
| Compare two document versions | Yes: text diff locally, Claude explains | – | – |

## 10. Proactive 🟡

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Resource alerts (CPU, disk, temp) | Yes: watchdog exists, add thresholds in settings | – | – |
| Quiet hours | Yes: settings time range, same as focus mode | – | – |
| "Why did this notification appear" | Yes: every heads-up carries its reason | – | – |
| Background job finished | Yes: `biztel run` / task terminals already tracked, add "notify when done" | – | – |
| Research monitoring | Later, with the browser agent + a schedule | Costs Claude calls each check | – |

## 12. Security ✅

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Audit log of actions | Yes: append-only JSONL (what, risk, approved by, result); "what did you do today" | – | – |
| Prompt-injection defence | Yes (see 3): untrusted wrapping, tool allowlist, confirm on high risk | Never 100% | – |
| Pause everything / revoke tools | Yes: "pause JARVIS" kill-switch + per-skill on/off in settings | – | – |
| Private / temporary session | Yes: "private mode": no saving, no Claude history | – | – |

## 13. Advanced 🔴

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Workflow learning ("every time I start this project…") | Yes: saved routines in YAML, JARVIS offers one after it sees the same steps 3 times | Must ask before saving routines with risky steps | Hand-written routines first, learning later |
| Triggers ("whenever a PDF lands here, summarise it") | Yes: inotify watcher (Python stdlib-friendly via `inotifywait` or polling) | Each trigger = background Claude calls; keep a limit | – |
| Knowledge graph | Possible later: SQLite tables linking projects, notes, files, URLs | Big build for little gain right now | Project notes + embeddings cover most of it |
| Visual dashboard (plan, approvals, running tasks) | Yes: GTK card already shows cards; add a "tasks" card | – | – |
| Fully offline LLM | Small one possible (Qwen 0.5B–1.5B) | Weak reasoning, uses GBs of RAM/disk; was rejected for size | Keep Claude as the fallback |

---

## Truly can't be done (on this setup)

- **Reliable control of every app's insides**: Electron/custom apps don't expose their UI; screenshot clicking is never 100%.
- **Agent using your normal Chrome profile through the debug port**: Chrome blocks it since v136 (needs a separate profile, or an extension).
- **Perfect "is this speech for me" detection** with tiny local models.
- **Strong multilingual + code-switching speech** at current speed: small Whisper models are weak at it.
- **Big local reasoning model**: not enough RAM/disk on the Jetson alongside everything else.
- **100% prompt-injection safety**: only reduced, by keeping risky actions behind confirm.

## Suggested order

1. Check every action + audit log (6, 12): cheap, makes everything else trustworthy
2. Claude calls JARVIS's own commands as tools, with a task card (6)
3. Browser agent: Playwright Node + `channel: chrome`, separate profile (3)
4. Files by date/content + PDF reading (4B)
5. Preferences memory with show/edit/forget (5)
6. Small wins any time: verbosity/speed by voice, quiet hours, network check, audio device, window layouts
7. Later: email/calendar, routines and triggers, notifications reader
