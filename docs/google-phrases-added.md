# Google phrases: what we had, what we added (2026-11-14)

For Calendar, Gmail, Google Chat and Drive. Each phrase JARVIS lists for the skill, the extra wordings JARVIS already knew for it (**had**, from config/intents.yaml; everything else that already worked is in all-phrases.md), and the ones added on 2026-11-14 (**added**). Every added one was run through tools/dryrun.py and reaches the feature, not Claude.

Phrases with a blank in them (`<name>`, `<what>`) are matched by the skill's own patterns, so they have no wordings list here. Everything else you can say is in [all-phrases.md](all-phrases.md).

Also changed on 2026-11-14:

- A guess that would open something (a file, a site, an app) is asked first: "Open the latest shared file?". Yes does it and learns the wording; no sends it to Claude. Not asked when you said "open" yourself.
- When Claude's "do" phrase matches nothing, Claude is asked once more to pick a listed phrase.

## Calendar

Added: 24

### do we have meetings today

- **had:** “am i in any meetings today”, “is my calendar busy today”
- **added:** —

### what's on my calendar tomorrow

- **had:** “how busy am i tomorrow”, “what's on my schedule tomorrow”
- **added:** “do i have anything on tomorrow”, “show me tomorrow's schedule”, “what does my day look like tomorrow”

### when's my next meeting

- **had:** “how long until my next meeting”, “what am i in right now”, “what meeting is next”, “what meeting is on now”, “when do i have to be in my next meeting”
- **added:** —

### am I free at 3

- **had:** —
- **added:** —

### when am I free tomorrow afternoon

- **had:** —
- **added:** “i need a free time tomorrow afternoon, when is it”, “what's open on my calendar tomorrow afternoon”

### join my meeting

- **had:** “connect me to the meeting”, “get me into my meeting”, “jump into my call”, “start my meeting”, “take me to my meeting”
- **added:** “open my meeting and join it”

### prep me for my next meeting

- **had:** “get me ready for my next meeting”, “what do i need for my next meeting”
- **added:** “summarize what my next meeting is about”

### schedule a meeting with <name> tomorrow at 3

- **had:** — (the skill's own pattern)
- **added:** —

### accept the invite

- **had:** “i'll go to that meeting”, “rsvp yes”, “say yes to the invite”, “yes accept the meeting invite”
- **added:** “rsvp yes to that invite”, “yes i'm attending, accept the invite”

### decline the invite

- **had:** “i can't make that meeting”, “rsvp no”, “say no to the invite”, “turn down the invite”
- **added:** “i can't make it, decline the invite”, “i'm not going to that meeting, decline it”, “reply no to the invitation”, “turn down the meeting invite”

### turn off meeting mode

- **had:** “disable meeting mode”
- **added:** “exit meeting mode”, “i'm done with my meeting, switch off meeting mode”, “i'm out of the call, stop meeting mode”

### show my meeting

- **had:** —
- **added:** “bring up my meeting info”, “display my next meeting”, “i want to see the meeting on screen”, “let me see my meeting”, “open my meeting details”, “pull up my meeting”, “show the details of my meeting”

### connect Google

- **had:** —
- **added:** “authorize google access”

## Gmail

Added: 16

### any new emails

- **had:** “any new messages in my inbox”, “what emails did i get today”
- **added:** —

### read the latest email from <name>

- **had:** — (the skill's own pattern)
- **added:** —

### did <name> email me

- **had:** — (the skill's own pattern)
- **added:** —

### summarise my inbox

- **had:** “give me a summary of my email”, “what's important in my inbox”
- **added:** “brief me on my emails”, “give me the rundown of my email”, “i have too many emails, summarise them”, “tell me what's new in my inbox, short version”, “what's in my inbox”

### add this email to my to-dos

- **had:** —
- **added:** “add the open email to my tasks”, “i need to deal with this email later, add it to my to-dos”, “make a to-do from this email”, “put this email on my to-do list”, “save this email as a task”, “this one needs action, put it on my to-dos”, “track this email in my to-do list”

### draft a reply saying <what>

- **had:** — (the skill's own pattern)
- **added:** —

### find the invoice email from last month

- **had:** —
- **added:** “did i get an invoice last month, find the email”

### search my email for <what>

- **had:** — (the skill's own pattern)
- **added:** —

### save the attachment

- **had:** —
- **added:** “download the file attached to this message”, “save the file that came with this email”, “store the file attached to this email”

## Google Chat

Added: 15

### any new chat messages

- **had:** “anything new on chat”, “did anyone message me on chat”
- **added:** “did anyone message me in chat”

### show my chats

- **had:** —
- **added:** “i want to see my chats”, “let me look at my chats”, “let me see my chats”, “pull up my chat list”, “show my chat conversations”

### show me the spaces

- **had:** —
- **added:** “display my chat spaces”, “let me see my chat spaces”

### what did <name> say on chat

- **had:** — (the skill's own pattern)
- **added:** —

### what was the last message from <name>

- **had:** — (the skill's own pattern)
- **added:** —

### open my chat with <name>

- **had:** — (the skill's own pattern)
- **added:** —

### open the <name> space

- **had:** — (the skill's own pattern)
- **added:** —

### open chats

- **had:** —
- **added:** “bring up chat”, “go to chat”, “i want to go to chat”, “launch chat”, “open the chat app”, “start chat”, “take me to my chats”

### search chat for <what>

- **had:** — (the skill's own pattern)
- **added:** —

### summarise the <name> space

- **had:** — (the skill's own pattern)
- **added:** —

### summarise my chat with <name>

- **had:** — (the skill's own pattern)
- **added:** —

### tell <name> on chat <message>

- **had:** — (the skill's own pattern)
- **added:** —

### reply saying <message>

- **had:** — (the skill's own pattern)
- **added:** —

## Drive

Added: 20

### find <name> in my drive

- **had:** — (the skill's own pattern)
- **added:** —

### what did I edit yesterday

- **had:** “what did i work on yesterday in drive”
- **added:** “i forgot what i was working on yesterday, what did i edit”, “list the files i modified yesterday”, “show me what i worked on yesterday”, “what did i touch in drive yesterday”, “what files did i change yesterday”, “which files did i edit yesterday”, “recent files on drive”, “recent files in my drive”, “show me my recent files in google drive”

### read the <name> doc

- **had:** — (the skill's own pattern)
- **added:** —

### summarise the <name> doc

- **had:** — (the skill's own pattern)
- **added:** —

### what's been shared with me

- **had:** “any new shared files”, “anything shared with me today”, “what did people share with me”, “what files have been shared with me”, “who shared files with me”
- **added:** “what did others share with me in drive”, “what's new on my drive”, “what's new in my drive”, “anything new in google drive”, “is there anything new on my drive today”, “show me recent shared files”, “latest shared docs”, “new shared sheets”

### open what <name> shared

- **had:** — (the skill's own pattern)
- **added:** —

### open the latest shared file

- **had:** —
- **added:** “open whatever was shared with me most recently”, “pull up the latest file shared with me”, “someone shared a file with me, open it”
- **dropped:** “show me the last file someone shared with me (removed: show means the list)”

### summarise what <name> shared

- **had:** — (the skill's own pattern)
- **added:** —

## Unlearned

Self-taught wordings taken back (they were wrong):

- “show me the latest file people shared” no longer means “open the latest shared file”
