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

## B. Voice commands for the computer ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| "Arrange my coding windows" | **Done:** "arrange my coding windows", "coding layout", "save this layout as reading", "list my layouts", "delete the reading layout" (skills/layouts.py). Each window is found, or opened with its commands.yaml phrase, moved to the layout's workspace and put left/right/maximised. Then JARVIS checks where each one actually is and says which didn't land. It won't take over one of JARVIS's own terminals while it can open a new one. Layouts in config/layouts.yaml; saved ones in `~/.local/share/jarvis/layouts.json`. Also fixed "put X on the left": under Pop Shell, Super+Left only moved the focus | Pop Shell tiling is on, so left/right is a tile swap (Super+Enter, Ctrl+Arrow, Enter), not an exact size. With 3+ windows the tiler decides the rest of the split. Not yet tried live on the real desktop | Turn tiling off (Super+Y) and layouts use exact halves |
| "File I downloaded this morning" | **Done:** "what did I download today", "open the pdf I downloaded this morning", "where's the zip I downloaded", "show me my downloads from this week", also yesterday, last night, "2 hours ago" (skills/files.py). Searches ~/Downloads and the folders it gets sorted into, by modified time. Several hits go on a card with buttons ("the second one" opens it); one hit opens | Uses the modified time: sorting files into folders resets their change time, and a file copied in keeps its original date | Watching downloads as they arrive (inotify) isn't needed: the scan is instant |
| "Read this window aloud" | **Done:** "read this window aloud", "read the terminal out loud", "read this aloud" (skills/screen.py, system/accessibility.py). AT-SPI text in ~0.02 s for the terminal and GNOME apps, with icon-font symbols removed. Up to 70 words are spoken; longer text goes on a card and JARVIS reads the start (a terminal's end). When the app shares no text, a screenshot goes to Claude to read out | VS Code, Chrome and Electron apps give no text (system accessibility is off), so they take the slower Claude path (~4 s). No local OCR | Chrome with `--force-renderer-accessibility`; tesseract (~30 MB) for offline reading; browser pages via the browser agent (section 3) |
| "Open the project I was working on yesterday" | **Done:** "open the project I was working on yesterday", "open yesterday's project", "open my last project", "get back to my last project" (skills/dev.py); "continue where I left off" goes to "where was I". Picks the project with the most VS Code time that day from the Day Tracker (it logs "VS Code · <folder>"), opens it in VS Code and says how long you spent on it | Only VS Code time counts (terminal-only work isn't logged per project). With the Day Tracker hub down, it falls back to the project whose files changed last | – |

## C. Screen-aware interaction ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Read UI tree (buttons, menus, dialogs) | **Done:** "what can I click here", "what's in the file menu" (skills/controls.py). AT-SPI lists a GTK window's buttons, tabs and menu items (even with the menu closed) in ~0.15 s; when the tree is empty, a screenshot goes to Claude | Chrome, VS Code and Electron apps (Vesktop) expose 0–3 nodes. Anything started from VS Code's terminal inherits `NO_AT_BRIDGE=1` and is invisible to AT-SPI | Chrome with `--force-renderer-accessibility` gives a full tree (costs some speed, so it isn't on by default); VS Code `"editor.accessibilitySupport": "on"` exposes a little more |
| Click a button / do an action in the current app | **Done:** "click save", "press the back button", "choose zoom in from the view menu", "press control shift t", "hit enter". Tried surest first: the app's shortcut (config/app_keys.yaml), then AT-SPI `do_action` (no pointer used), then a spot Claude finds on a window screenshot. That spot is shown marked on a card and clicked only after a yes, and not if the window moved since. Buttons that delete or send are asked about first | Claude's spot was exact on a test window (sized so it isn't scaled down) but takes ~5 s. Terminals only use their own shortcuts (ctrl+c / ctrl+z mean something else there) | Add more apps to config/app_keys.yaml as they come up |
| Verify an action worked | **Done:** every launch is watched (core/verify.py): a new window or a changed title counts as done; a non-zero exit is said out loud with its error. After a click the window's pixels are compared (~0.1 s, JARVIS's own orb and cards masked), and if nothing changed JARVIS says so. "Did that work?" answers from what it saw | Key presses and things like "copy" leave no visible trace: reported as "couldn't tell", not as done. Window apps' errors aren't captured (they write to stderr for days), only their exit code | When it couldn't tell, "did that work?" offers a screenshot for Claude to check (slow, so only when asked) |
| "Why isn't my app running?" | **Done:** skills/troubleshoot.py reads biztel run's state, the ports its folder's programs listen on, the log `biztel run` now keeps, and the terminal's visible text. Clear cases are said locally (never started, port taken and by what, the last error before it stopped); unclear ones go to Claude with those facts. Also "what's using port 3000", "what ports are open" | AT-SPI only gives a terminal's visible screen, not its scrollback. Projects started by hand outside `biztel run` have no log | `biztel run` keeps `~/.local/state/biztel/runs/<name>.log` (last ~1 MB, kept after it stops). For other terminals: run under `script` or tmux to keep a log |

## D. Personality and presence ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Speaking speed by voice | **Done:** "talk faster" / "slow down" / "you're talking too fast" / "normal speed" (Piper `length_scale`, 0.6–1.6, kept across restarts). The reply already uses the new speed; "a bit more" / "too much" follow it | Piper restarts for it (unnoticeable between lines) | – |
| Verbosity by voice | **Done:** "be brief" / "give me detailed answers" / "normal answers". Brief picks the shortest local lines and tells Claude to keep "say" to one sentence; detailed lets it use 3–4. "More detail" on its own still goes to Claude as "tell me more" | – | – |
| Headphones-only / private mode | **Done:** "headphones only" / "private mode (off)". Only JARVIS's own voice moves, to Bluetooth or named headphones when connected, else `speech.private_sink` (the jack); other sound stays where it is. If the headphones disconnect it goes quiet and writes in the chat, never out loud | The jack can't tell whether anything is plugged in. The "only X will hear me now" confirmation is still said on the speaker | **Done too:** "quiet mode" / "don't talk" / "you can talk again": nothing spoken, everything in the chat panel (opened for you) |
| Summarise long text instead of reading it all | **Done:** over `speech.read_words` (60) words, the start is said locally and all of it goes on a card; "read the rest", "read all of it", "summarise it" (Claude, 2–3 sentences). Used by "read it to me" (cards), "read this window", "read my clipboard" | – | `speech.long_text: summary` summarises straight away (a few seconds slower) |
| Mic indicator | Already: orb + tray | – | – |

## 3. Browser agent ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Read pages, extract facts, summarise tabs | **Done:** "summarise this page", "what's this page about", "what's the price on this page", "on this page, who wrote it", "summarise example.com", "summarise my tabs" (skills/web.py, system/webagent.py, tools/browser-agent/agent.js). The agent is playwright-core (Node, 8.7 MB, no browser download) starting your installed Chrome headless over a pipe, so there's no debug port at all, with its own profile `~/.local/share/jarvis/agent-chrome`. It starts in ~0.6 s and closes after 10 idle minutes. "This page" is the address copied from the front browser's address bar (your clipboard is put back). A login wall (almost no text) falls back to a screenshot for Claude. A page summary takes ~9 s and puts the key points on a card. Tabs: steps through them with Ctrl+Tab reading the titles, then Claude groups them | The agent has none of your logins (separate profile). "Summarise my tabs" only sees titles and visibly flips through the tabs. The agent is an honest automated browser (no faked user agent, no hidden webdriver flag), so some sites show it a human check. Copying the address from your real browser isn't tried live yet | Chrome extension + native messaging host: would read the real page (with your logins), every tab's text and YouTube transcripts in your own profile. Not built |
| Research with sources / compare | **Done:** "research the best budget keyboard", "compare jetson orin nx and raspberry pi 5", "deep research on X". Claude's WebSearch picks up to 5 pages, the agent reads them all at once in separate tabs, and Claude answers only from them: two spoken sentences and a card with [1] [2] citations (a table when comparing) and a Sources list. ~30 s. "Deep research" opens a Claude Code task terminal (WebSearch/WebFetch) that puts its findings on a card with `jarvis show` | DuckDuckGo shows the agent a "prove you're human" puzzle and Bing gives a cookie-less agent junk results, so finding the pages costs a Claude WebSearch call (~10 s, ~1¢). Haiku is good for 3-5 sources, not long reports | – |
| Fill forms, multi-page workflows | **Done:** "fill in the form on this page with my work email", "in the agent browser, find the cheapest flight to goa", "use the browser agent to ...". One step at a time: the page's text and numbered elements go to Claude, which picks one allowed action (click, fill, select, check, press, scroll, back, goto, upload, download, ask, done); up to 10 steps, stopping if it repeats itself. Before anything that submits, sends, buys or deletes (or Enter in a field) it shows a screenshot card and asks. On a test signup form it filled name, email and the dropdown, left the password, asked before "Sign up" and didn't submit on no. "Show the agent browser" to watch or take over; "log in to github in the agent browser" to log in once (it stays logged in) | Never types passwords or codes, never pays: a payment page is opened in your browser instead. It starts from the page's address in its own browser, so pages behind your login need that one-time login. Google's sign-in may refuse an automated browser. Captchas and 2FA are left to you. ~4 s per step | – |
| Download / upload files | **Done:** "download https://…/file.zip" (fetched with the agent's cookies, saved in ~/Downloads, never overwriting); "download the pdf on this page", "upload ~/cv.pdf to this page" go through the step loop. Uploads must be an existing file you named and are asked about every time ("Upload cv.pdf to example.com?") | Upload only checked through its logic, not on a live site yet | – |
| Bookmarks | **Done:** "open my jira bookmark", "search my bookmarks for recipes", "what did I bookmark about X", "show my bookmarks in the recipes folder" read Chrome's Bookmarks file (last used profile) and list them on a card with links. "Bookmark this" presses Ctrl+D in your browser (Chrome saves it; the box lets you pick a folder) | Your Chrome profile has no bookmarks yet, so it was tried on a sample file. Moving or deleting bookmarks isn't done | Extension route for full editing |
| YouTube summaries | **Done:** "summarise this video", "what's this video about", "summarise <youtube link>". The agent opens the video's "Show transcript" panel the way you would and Claude sums up the transcript. When there's no transcript it says so and sums up the title, description and chapters | YouTube doesn't give an automated browser the transcript: the direct request is refused ("Precondition check failed") and the panel just spins. Getting round that means pretending to be a normal browser, which it won't do, so today it's always the description summary | The extension reading the transcript panel in your own Chrome, where it loads; or hand the video to a Claude Code task terminal |
| Prompt-injection defence | **Done:** page text, element labels and tab titles always go to Claude inside `<untrusted>` tags (look-alike tags stripped) with a rule that it's data, never instructions. The agent can only do the allowed actions on numbered elements; password values are never read, and password, card and code fields can't be typed into. Risky clicks need a yes; payments are refused; its profile is separate from yours and it has no debug port | No defence is perfect: a page can still slant a summary | Keep high-risk actions behind confirm (done) |

## 4A. Application control ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Detect frozen apps | **Done:** "is anything frozen", "Chrome isn't responding", "this app is stuck", "is my computer frozen", "force quit this" (skills/frozen.py). Each window is pinged (`_NET_WM_PING`, what GNOME's own "not responding" dialog uses): a working app answers in ~0.1 s, one that hasn't in 2 s is frozen. Its process says why: busy (and how much CPU), waiting on the disk, or paused (stopped from a terminal). JARVIS offers to close it (politely, forced after 3 s) or to resume a paused one, then checks the window really went. "What's slowing my computer" pings the windows while it measures the CPU and deals with a frozen one first | Apps that don't take pings can't be checked (every window here does). A busy app may come back by itself: JARVIS says so, but can't know. The desktop and JARVIS itself are never closed. Tried on test windows (busy, blocked, paused), not on a real frozen app | GNOME's own "not responding" dialog still shows after ~5 s, with Force Quit |
| Click inside apps / menus | **Done** in C: shortcut → AT-SPI → Claude's spot on a screenshot (asked first) | Same limits as C | – |
| Copy info between apps | **Done:** "copy this into Slack", "paste my clipboard into the terminal", "copy the link from Chrome into VS Code", "copy the error from the terminal into Chrome", "copy this window into …", "summarise / translate this and put it in Gmail" (skills/copy_between.py). The selection is copied with the app's own key (when nothing is selected JARVIS says so and puts your clipboard back), the other window is focused (or the app opened, after a yes) and the text pasted. Then its pixels show whether the paste landed (even one letter). The text stays on your clipboard | It pastes where that window's cursor is: with no text box focused nothing happens, and JARVIS says so. Never presses Enter or sends; a paste of more than one line into a terminal is asked about first (each line would run). "The error" and "this window" need the app's text (terminal, GNOME apps), so not Chrome or VS Code; "the link" only comes from a browser. Tried between test windows; Chrome's address bar and opening a closed app not yet tried live | In Chrome or VS Code: select the text and say "copy this into …" |

## 4B. File intelligence ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Search by date / extension | **Done:** "find pdfs from last week", "show the zip files in downloads from this week", "find .docx files in downloads", "find python files from today in projects", "what files did I change today", "which files have I edited this week in projects" (skills/files.py). It walks your home folder (or the folder you name), skipping hidden folders, node_modules and venvs: ~0.5 s for all ~25k files. A card lists up to 30, newest first, with buttons to open the first 6. Times: today, yesterday, this/last week, this/last month, the last 3 days, 2 days ago… | Modified time only (not when a file was made or opened). It stops after 4 s on a very big folder. "Last week" means 7 to 14 days ago | – |
| Search by content | **Not built, by choice.** Finding a file by a word inside it is left out; Claude reading the file you point at (next row) answers "what does it say" better | Can't find a file by what's in it | A small SQLite index of chosen folders, if it's ever needed |
| Read PDFs / docs / sheets / slides | **Done:** "summarise this pdf", "what's in budget.xlsx", "what does my resume say about python", "in this document, what's the deadline", "what's the total in invoice.pdf" (skills/documents.py, system/documents.py). The text is taken out locally (pdftotext for PDFs; Word, Excel, PowerPoint and LibreOffice files from their XML with Python's own zip reader; text and code as is), then Claude answers: a Word guide summarised in 6 s, a question about a spreadsheet in 3 s. Scanned PDFs and pictures have no text, so Claude looks at the file itself with its Read tool (a copy in jarvis-workspace, removed afterwards). A one-page scanned CAD drawing came back with its dimensions in 9 s, so no OCR is needed. "This file" is what's selected in Files (copied with Ctrl+C, your clipboard put back), else the document a viewer or editor has open (from its command line, or the name in its title looked up in your recent files), else the file JARVIS just found. "It" / "that" is the file just talked about, and "this pdf" only takes a PDF. The card has Open it and Go through it with Claude buttons. "Go through <file> with Claude" opens a Claude Code terminal in the file's folder that reads all of it and takes questions. File text goes to Claude inside `<untrusted>` tags | The quick answer reads up to ~60k characters (about 40 PDF pages): for longer files it reads the start and says so. Old binary .doc/.xls/.ppt aren't read (the Claude terminal can convert them). Password-protected PDFs can't be read. Picking up the selection in Files hasn't been tried live; the VS Code title was picked up correctly | The Claude Code terminal for long files and back-and-forth (built) |
| Summarise / extract across files | **Done:** "summarise the pdfs in downloads", "summarise the documents in ~/Documents/reports", "summarise what I downloaded today", "compare offer.pdf and offer_signed.pdf" (two spreadsheets compared in 7 s). It reads up to the 8 newest files, sharing the 60k characters between them, and puts a section per file on the card | For a big folder, only the newest 8. Compare is Claude reading both files, not a line-by-line diff | – |
| Duplicates | **Done:** "find duplicate files in downloads", "any duplicates on my desktop" (skills/organise.py). Files are compared by size, then the first 64 KB, then a full SHA-1. The card lists each group, the copy to keep (the one without "(1)" or "copy" in its name, else the oldest) and the space taken. JARVIS asks before putting the extra copies in the Trash, and "undo that" brings them back | Exact copies only (not similar photos). Tried on a test folder, not on your real Downloads | – |
| Organise / rename / create folders | **Done:** "make a folder called invoices in documents", "rename this file to final report" (keeps the .pdf), "move resume.pdf to documents" (or "to a new folder called X"), "delete old.txt" / "put X in the trash", "tidy my downloads" (loose files go into Images, Videos, Music, Installers, Archives, Models and Documents, the folders your Downloads already uses, shown on a card first). Nothing is overwritten (a taken name gets " (2)") and nothing is deleted for good: `gio trash`, so it's in Files' Trash. JARVIS asks first when it had to work out which file you meant, before the Trash, and before changing many files. "Undo that" puts it all back (moves reversed, new folders removed, files restored from the Trash), and every change is written to ~/.local/share/jarvis/file_changes.log. The old "delete X" command that put `rm` in the terminal is gone | Undo covers the last change only. Tidy only sorts Downloads and Desktop, and only the loose files. Tried on a test folder, not on your real folders | – |
| Meaning-based file search | **Done, names only:** "find my job offer" finds OfferLetter_CVEngineer_Chetak_Signed.pdf when no name matches the words. MiniLM compares your request with the file names (and their folder) in Documents, Downloads, Desktop, Pictures, Videos and Music, plus a small bonus for a shared word. The first time takes ~2 s (~400 files), then it's instant until a file comes or goes. JARVIS asks before opening | Names only, never what's in the files. MiniLM is weak on file names, so the bar is high: "holiday photos" finds nothing rather than a screenshot. Projects isn't included | Headings or first lines too, if names turn out not to be enough |

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

## 7. Productivity 🟡 (daily briefing built 2026-10-29)

| Gap | Done / can be done | Can't / limits | Other options |
|---|---|---|---|
| Email (search, summarise, draft) | Yes via Gmail API or the claude.ai Gmail connector | Needs you to authorise it; sending always asks first | Open Gmail in the browser agent |
| Calendar | Yes via Google Calendar connector/API | Needs authorisation | – |
| Meeting summaries | Yes from a transcript/recording file | Live meeting transcription with base.en is possible but heavy for long meetings | – |
| Daily briefing | **Done:** "brief me", "morning briefing", "what's my day look like", "what do I have today", "what's on my plate today" (skills/briefing.py). One card: today's reminders with times, open to-dos (newest first), today's Day Tracker goals with time so far, the week's goals (and how much a day it takes to make them), yesterday's time by category and the projects / sites it went on. A short spoken line: the date, the first reminder, how many to-dos and the newest, yesterday's top two. It also comes by itself once a day, the first time you're around between 5 am and noon (not in focus mode or quiet hours; settings `briefing`); "turn off the daily briefing" / "start the daily briefings" by voice | No calendar yet: it joins once the Calendar row above is connected. Needs the Day Tracker running (else it says so and how to start it). Checked with the live tracker data; the morning one not yet heard live | – |
| Notes / tasks / reminders | **Done** (built before this list): notes ("remember that…", "what do you remember", "what do you know about X", "forget…"; skills/notes.py), to-dos ("add X to my todo list", "what's on my todo list", "mark X done") and reminders ("remind me in 20 minutes to…", "remind me at 5 pm to…", "what are my reminders", "cancel the X reminder"; skills/todos.py). To-dos and reminders live in the Day Tracker, so its page shows them and chimes too; JARVIS says reminders out loud when due. Day Tracker time by voice: "start studying", "how's my day", "how long did I work today" (skills/daytracker.py) | Reminders are only said while JARVIS is running; one that came due more than 10 minutes before it started is dropped quietly. Notes are a local file, not synced anywhere | Obsidian/Notion only if you use them |

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

## 10. Proactive ✅ (built 2026-10-29)

| Gap | Done | Can't / limits | Other options |
|---|---|---|---|
| Resource alerts (CPU, disk, temp) | **Done:** every 2 minutes JARVIS checks the CPU (averaged over the 2 minutes since the last check), memory, the hottest temperature sensor, the disk and batteries, and says the busiest program with it ("Heads up, the CPU has been at 94 percent for the last 2 minutes, mostly chrome"). Thresholds in settings `heads_up` (`cpu: 90`, `memory: 90`, `temperature: 85`, `disk_gb: 3`; 0 turns one off). Each kind is said at most every 2 hours. "Don't warn me about that again" / "stop the cpu alerts" turns a kind off, "turn the cpu alerts back on" turns it back on (skills/proactive.py) | Two-minute checks, so a spike shorter than that isn't reported. The GPU's own load isn't read (only its temperature) | "What's slowing my computer" for the full list and to close one |
| Quiet hours | **Done:** besides the softer, shorter voice, heads-ups, habit offers and Claude's suggestions now wait during quiet hours, as in focus mode. Events that don't repeat (a project crashing) are kept and said when quiet hours end and you're back ("During quiet hours: ...", more than one also on a card). Things you asked for (reminders, timers, "tell me when this finishes") and "I can't hear you" problems still come through. By voice: "quiet hours from 10 pm to 6 am", "turn off quiet hours", "what are my quiet hours" (kept across restarts, over settings.yaml) | Whole hours only | `quiet_hours: hold: false` keeps only the softer voice |
| "Why did this notification appear" | **Done:** everything JARVIS brings up by itself keeps its reason: heads-ups, habit offers (which days you did it at this hour), reminders, timers, focus ending, project up or crashed, microphone/speaker problems, Claude's suggestions, a Claude task terminal finishing. "Why did you say that" / "why did that notification appear" says it; after an ordinary answer, "why did you say that" goes on to Claude as before. "Show my recent alerts" / "what did I miss" puts today's on a card with their reasons | Only JARVIS's own heads-ups and notifications, not other apps' | – |
| Background job finished | **Done:** "tell me when this finishes", "let me know when the build is done", "notify me when npm install finishes". JARVIS finds the command your terminal is busy with (any terminal: GNOME Terminal, VS Code, tmux; several, it asks which), watches it every 2 s and says "npm run build has finished, after 6 minutes", even in focus mode (system/jobs.py). A project under `biztel run` whose part stops by itself is said straight away with its last error ("Heads up, Avtec HS's backend crashed. The last error was: ..."); one you stop isn't. Claude task terminals already said when they're done (their hook) | It can't tell whether the command succeeded: the exit code belongs to the terminal's shell. Programs you sit in (vim, ssh, a bare python) aren't offered | Long-running projects through `biztel run`, whose log keeps the error |
| Research monitoring | Not built: later, with the browser agent + a schedule | Costs Claude calls each check | – |

## 12. Security ✅

| Gap | Can be done | Can't / limits | Other options |
|---|---|---|---|
| Audit log of actions | Yes: append-only JSONL (what, risk, approved by, result); "what did you do today" | – | – |
| Prompt-injection defence | Yes (see 3): untrusted wrapping, tool allowlist, confirm on high risk | Never 100% | – |
| Pause everything / revoke tools | Yes: "pause JARVIS" kill-switch + per-skill on/off in settings | – | – |
| Private / temporary session | Yes: "private mode": no saving, no Claude history | – | – |

## 13. Advanced 🟡 (dashboard and links built 2026-10-29)

| Gap | Done / can be done | Can't / limits | Other options |
|---|---|---|---|
| Workflow learning ("every time I start this project…") | Yes: saved routines in YAML, JARVIS offers one after it sees the same steps 3 times | Must ask before saving routines with risky steps | Hand-written routines first, learning later |
| Triggers ("whenever a PDF lands here, summarise it") | Yes: inotify watcher (Python stdlib-friendly via `inotifywait` or polling) | Each trigger = background Claude calls; keep a limit | – |
| Knowledge graph | **Done, the lighter way:** "everything about study buddy", "what do I have on avtec", "what's linked to the camera calibration", "all my stuff about the nano" (skills/linked.py). No graph database: the links are worked out when you ask, from what JARVIS already keeps. For a project: what it is (README), its folder, git (branch, last commit, uncommitted files), its links if running, and Day Tracker VS Code time today and this week. For a project or a topic: notes, open to-dos that name it, Chrome bookmarks, files in Downloads / Documents / Desktop and saved cards with its name. One card with Open in VS Code / Run / Git status buttons, in ~0.1 s. Nothing is stored, so nothing goes stale or needs syncing | Matched by names and words, not meaning: a note that says "the app" without naming it isn't found. Files are matched by name, not content. "Everything about X" only answers for projects ("everything about black holes" is a question for Claude); other topics need "what do I have on X". Time only counts VS Code | A real SQLite graph only if questions like "which projects use this library" come up. MiniLM embeddings over notes (5) would add matching by meaning |
| Visual dashboard (plan, approvals, running tasks) | **Done:** "show my dashboard", "what are you working on", "show running tasks", "what's in progress", "close the dashboard" (skills/dashboard.py). One live card, refreshed every 2 s while it's open (only when something changed; it stops once closed or replaced, or after 30 min). It shows the question waiting for your answer, with Yes / No buttons that answer it; Claude's plan with each step ticked; and what's running: biztel run projects (links, Stop buttons), commands busy in your terminals (and the ones JARVIS will tell you about), Claude task terminals, the agent browser, timers. Also focus / quiet / private mode, the last checked action and your last requests. Claude's multi-step plans now tick themselves off on their own Plan card too ("← now", "✗ stopped here") | Claude task terminals show as open, not whether Claude is still working in them. "What are you doing" stays small talk. Checked with real data and a simulated plan, not yet tried live on the desktop | – |
| Fully offline LLM | Small one possible (Qwen 0.5B–1.5B) | Weak reasoning, uses GBs of RAM/disk; was rejected for size | Keep Claude as the fallback |

---

## Truly can't be done (on this setup)

- **Reliable control of every app's insides**: Electron/custom apps don't expose their UI; screenshot clicking is never 100%.
- **Agent using your normal Chrome profile through the debug port**: Chrome blocks it since v136 (needs a separate profile, or an extension).
- **Perfect "is this speech for me" detection** with tiny local models.
- **Strong multilingual + code-switching speech** at current speed: small Whisper models are weak at it.
- **Big local reasoning model**: not enough RAM/disk on the Jetson alongside everything else.
- **100% prompt-injection safety**: only reduced, by keeping risky actions behind confirm.
- **YouTube transcripts in the agent's browser**: YouTube refuses them to an automated browser (only the extension route in your own Chrome would get them).

## Suggested order

1. Check every action + audit log (6, 12): cheap, makes everything else trustworthy
2. Claude calls JARVIS's own commands as tools, with a task card (6)
3. ~~Browser agent: Playwright Node + `channel: chrome`, separate profile (3)~~ built
4. ~~Files by date + reading files (4B)~~ built (search by content left out: Claude reads the file instead)
5. Preferences memory with show/edit/forget (5)
6. Small wins any time: verbosity/speed by voice, quiet hours, network check, audio device, window layouts
7. Later: email/calendar, routines and triggers, notifications reader
