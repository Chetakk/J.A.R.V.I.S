#!/usr/bin/env python3
"""Every way of saying what docs/feature-gaps.md marks as built, expanded from templates.

  tools/variants.py                 # write tests/variants.txt (all wordings, by feature)
  tools/variants.py --check         # route each one with tools/dryrun.py, list the ones that go elsewhere
  tools/variants.py --check ram     # only features whose name contains "ram"

Template syntax: {a|b|} picks one (empty = leave it out), <slot> takes each value in SLOTS.
Each group's first line is the doc's own wording: its route is what the rest should get too.
"""
import itertools
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "tests", "variants.txt")

SLOTS = {
    "app": ["chrome", "firefox", "vs code", "the terminal", "slack", "files", "gmail"],
    "file": ["resume.pdf", "budget.xlsx", "invoice.pdf", "report.docx", "notes.txt", "slides.pptx"],
    "doctype": ["pdf", "zip", "docx", "image", "video", "spreadsheet", "python file"],
    "kind": ["pdf", "zip", "docx", "image", "video", "excel", "python"],  # "<kind> files"
    "when": ["today", "this morning", "yesterday", "last night", "this week", "last week", "2 hours ago",
             "this afternoon", "earlier today"],
    "range": ["today", "yesterday", "this week", "last week", "this month", "last month", "the last 3 days"],
    "folder": ["downloads", "documents", "my desktop", "projects", "~/Documents/reports"],
    "project": ["jarvis", "study buddy", "avtec hs", "product vt", "the platform"],
    "topic": ["the nano", "camera calibration", "avtec", "invoices", "the router"],
    "site": ["example.com", "github.com", "wikipedia.org"],
    "port": ["3000", "8080", "5000", "8000"],
    "num": ["1", "2", "3", "second", "third"],
    "menu": ["file", "edit", "view", "help"],
}

POLITE = ["jarvis {}", "hey jarvis, {}", "can you {}", "could you {} please", "{} please", "jarvis, can you {}"]

FEATURES = {
    # A / D. Natural conversation, personality
    "D verbosity brief": ["be brief", "{keep it|make it|keep your answers|make your answers} {short|brief|shorter}",
                          "{give me|use} {short|shorter|brief} answers", "{less|not so much} {detail|talking}",
                          "{stop|don't} {rambling|waffling}", "{short|brief} answers {please|from now on|}",
                          "{just|only} give me the short version"],
    "D verbosity detailed": ["give me detailed answers", "{be|go into} more {detailed|detail}",
                             "{give me|use} {longer|detailed|fuller|more detailed} answers",
                             "{longer|detailed} answers {please|from now on|}", "explain things in more detail"],
    "D verbosity normal": ["normal answers", "{go back to|back to} normal answers", "normal length answers",
                           "{stop being|don't be} so brief", "{reset|default} answer length"],
    "D speed faster": ["talk faster", "{speak|talk|go} {faster|quicker|a bit faster|a little faster}",
                       "{you're|you are} {talking|speaking} too slow{ly|}", "speed up {your voice|your speech|}",
                       "{increase|raise} your {speaking|talking} speed"],
    "D speed slower": ["slow down", "{speak|talk} {slower|more slowly|a bit slower|a little slower}",
                       "{you're|you are} {talking|speaking} too fast", "slow down your {voice|speech|talking}",
                       "{decrease|lower|reduce} your {speaking|talking} speed"],
    "D speed normal": ["normal speed", "{talk|speak} at {normal|the normal|regular} speed",
                       "{reset|default} {your|} {speaking|talking|voice} speed", "back to normal speed"],
    "D headphones only": ["headphones only", "private mode", "{only|just} {talk|speak} {in|through} my headphones",
                          "{use|switch to} {my|the} headphones {only|for your voice|}", "turn on private mode",
                          "{talk|speak} {privately|quietly through my headphones}"],
    "D private off": ["private mode off", "turn off private mode", "{stop|end|exit|leave} private mode",
                      "{use|talk on} the speaker again", "you can use the speaker again"],
    "D quiet mode": ["quiet mode", "don't talk", "{stop|no} talking", "{be|stay} quiet", "{go|switch to} quiet mode",
                     "{only|just} write {in the chat|it down}", "{don't|do not} {speak|say anything} out loud",
                     "{shut up|hush} for a while", "text only"],
    "D quiet off": ["you can talk again", "{you can|go ahead and} {speak|talk} {again|now}", "quiet mode off",
                    "{turn off|stop|end} quiet mode", "{start|resume} talking again"],
    "D read the rest": ["read the rest", "read all of it", "{read|keep reading|carry on reading|continue reading} "
                        "{the rest|the rest of it|everything|the whole thing}", "{finish|keep} reading"],
    "D summarise it": ["summarise it", "{summarise|summarize|sum up} {that|this|it|the card}",
                       "give me {the gist|a summary|the short version}", "what's the gist", "tldr"],

    # B. Voice commands for the computer
    "B coding layout": ["arrange my coding windows", "coding layout", "{set up|arrange|lay out|organise|sort out} "
                        "my {coding|code|programming|dev|development|work} windows",
                        "{switch to|go to|use|load} {the|my} {coding|programming|dev} layout",
                        "{get|set} {me|} {ready|set up} for coding"],
    "B save layout": ["save this layout as reading", "save {the|this|my} {layout|window layout|windows} as "
                      "{reading|writing|meeting|research}", "remember this layout as {reading|meeting}",
                      "call this layout {reading|meeting}"],
    "B list layouts": ["list my layouts", "{show|list|what are} my {window|} layouts", "what layouts do i have",
                       "which layouts {are there|have i saved}"],
    "B delete layout": ["delete the reading layout", "{delete|remove|forget} {the|my} {reading|meeting} layout"],
    "B put window left": ["put chrome on the left", "{put|move|snap} <app> {on|to} the {left|right}",
                          "{put|move} <app> {to|on} the {left|right} {side|half}"],
    "B downloads today": ["what did i download today", "what did i download <when>",
                          "{show|list} {me|} {my|the} downloads from <when>",
                          "{show|list} {me|} what i downloaded <when>", "what have i downloaded <when>",
                          "any downloads <when>", "{what|which} files did i download <when>"],
    "B open download": ["open the pdf i downloaded this morning", "open the <doctype> i downloaded <when>",
                        "{show|find} {me|} the <doctype> {that|} i downloaded <when>"],
    "B where's download": ["where's the zip i downloaded", "{where is|where's} the <doctype> i {just|} downloaded",
                           "where did the <doctype> i downloaded go", "find the <doctype> i {just|} downloaded"],
    "B read window": ["read this window aloud", "read {this|the} {window|screen|terminal|app} {aloud|out loud|to me|out}",
                      "read {this|it} {aloud|out loud}", "read {out|aloud} {this|the} {window|terminal}",
                      "what does {this|the} {window|terminal} say", "read me {this|the} {window|terminal}"],
    "B yesterday's project": ["open the project i was working on yesterday", "open yesterday's project",
                              "open my last project", "get back to my last project",
                              "{open|reopen|bring up|load} {the|my} {project|code} {i was on|i worked on|"
                              "i was working on} {yesterday|last time|before}",
                              "{back|go back|get back} to {my|the} {last|previous} project",
                              "{open|reopen} {my|the} {last|previous|most recent} project"],
    "B continue where left": ["continue where i left off", "{pick up|carry on|start again} where i left off",
                              "where did i leave off"],

    # C. Screen-aware interaction
    "C what can i click": ["what can i click here", "what {can i|is there to} {click|press} {here|on this window|}",
                           "{list|show|tell me} the buttons {here|on this window|in this app|}",
                           "what buttons {are there|are on this window|can i press}", "what are my options here"],
    "C menu contents": ["what's in the file menu", "what's in the <menu> menu", "{show|list|read} {me|} the <menu> menu",
                        "what {options|items} are in the <menu> menu"],
    "C click": ["click save", "{click|press|hit|tap|push|select} {save|ok|cancel|the save button|the ok button|"
                "next|the back button}", "click {on|} {save|ok|cancel|next}",
                "press the {back|save|cancel|next|ok} button"],
    "C menu item": ["choose zoom in from the view menu",
                    "{choose|pick|select|click} {zoom in|new window|save as|preferences} {from|in} the "
                    "{view|file|edit} menu", "{open|go to} the {view|file|edit} menu and {click|choose} "
                    "{zoom in|save as}"],
    "C keys": ["press control shift t", "press {control|ctrl} {c|v|z|s|shift t}", "hit {enter|escape|tab|space}",
               "{press|hit|push} {the|} {enter|escape|tab|backspace|delete} {key|}", "press alt {tab|f4}",
               "{press|type} {control|ctrl} {alt|shift} {t|n}"],
    "C did that work": ["did that work", "{did|was} {that|it} {work|successful|ok}", "{did|has} it {open|go through|happen}",
                        "{was that|is it} done", "did it {actually|really} work", "{check|see} if {that|it} worked"],
    "C why isn't my app running": ["why isn't my app running", "why {isn't|is not|won't|doesn't} <project> "
                                   "{running|start|starting|work|working|come up}", "why did <project> {stop|crash|die}",
                                   "what's wrong with <project>", "<project> {isn't|is not|won't} {running|starting|"
                                   "working}", "why {can't|won't} <project> start"],
    "C port": ["what's using port 3000", "what's {using|on|running on|listening on} port <port>",
               "{who|which program|what} {has|is using|took} port <port>", "is {port|anything on port} <port> "
               "{free|taken|in use|busy}", "what's on <port>"],
    "C open ports": ["what ports are open", "{which|what} ports are {open|in use|being used|listening}",
                     "{list|show} {the|} {open|listening} ports", "show me the open ports"],

    # 3. Browser agent
    "3 summarise page": ["summarise this page", "{summarise|summarize|sum up} {this|the} {page|website|site|article}",
                         "what's this {page|website|site|article} about", "give me {the gist|a summary} of this "
                         "{page|article}", "{tldr|tl dr} this page", "what does this {page|article} say"],
    "3 page fact": ["what's the price on this page", "on this page, who wrote it",
                    "{on|from} this page, {what's the price|who wrote it|when was it published}",
                    "what's the {price|date|author|total} on this page",
                    "how much {is it|does it cost} on this page", "who wrote this {page|article}"],
    "3 summarise site": ["summarise example.com", "{summarise|summarize|sum up} <site>",
                         "what's {on|} <site> about", "give me a summary of <site>"],
    "3 summarise tabs": ["summarise my tabs", "{summarise|summarize|sum up|group} {my|all my|the|my open} tabs",
                         "what {tabs do i have open|are my tabs about}", "what's in my tabs"],
    "3 research": ["research the best budget keyboard", "research {the best|a good|a cheap} {budget keyboard|"
                   "laptop under 50000|mechanical keyboard|noise cancelling headphones}",
                   "{look up|find out|do some research on} the best {budget keyboard|usb mic}",
                   "{find|search for} {sources|articles} {on|about} {jetson power modes|whisper models}"],
    "3 compare": ["compare jetson orin nx and raspberry pi 5", "compare {the|} {jetson orin nx|iphone 15|vim} "
                  "{and|with|vs|versus|to} {the|} {raspberry pi 5|pixel 8|emacs}",
                  "{what's the difference|which is better} between {jetson orin nx|vim} and {raspberry pi 5|emacs}",
                  "{jetson orin nx|vim} {vs|versus} {raspberry pi 5|emacs}"],
    "3 deep research": ["deep research on solid state batteries", "{do|start} {a|some} deep research on "
                        "{solid state batteries|edge ai chips}", "{deep dive|in-depth research} {on|into} "
                        "{solid state batteries|edge ai chips}"],
    "3 fill form": ["fill in the form on this page with my work email", "fill {in|out} {the|this} form {on this page|"
                    "here|} {with my work email|with my details|for me|}",
                    "{complete|fill} {the|this} {form|signup form|sign-up form} {for me|with my email}"],
    "3 agent task": ["in the agent browser, find the cheapest flight to goa", "{in|using|with} the agent browser,? "
                     "{find the cheapest flight to goa|check the train times to pune|look up the weather in delhi}",
                     "use the browser agent to {find the cheapest flight to goa|book nothing just check prices}",
                     "{ask|get|have} the browser agent {to|} find the cheapest flight to goa"],
    "3 show agent": ["show the agent browser", "{show|open|let me see|bring up} the {agent browser|browser agent|"
                     "agent's browser}", "{let me|i want to} {watch|take over} the {agent|browser agent}"],
    "3 agent login": ["log in to github in the agent browser", "{log|sign} {in|into|in to} {github|amazon|jira} "
                      "{in|on|with} the {agent browser|browser agent}"],
    "3 download url": ["download https://example.com/file.zip", "{download|fetch|grab|save} "
                       "{https://example.com/file.zip|https://example.com/report.pdf}"],
    "3 download from page": ["download the pdf on this page", "download the {pdf|file|zip|image|attachment} "
                             "{on|from} this page", "{save|grab} the {pdf|file} {on|from} this page"],
    "3 upload": ["upload ~/cv.pdf to this page", "upload {~/cv.pdf|my resume|resume.pdf} {to|on|onto} "
                 "{this page|this site|the form}", "attach {~/cv.pdf|resume.pdf} {to|on} this {page|form}"],
    "3 open bookmark": ["open my jira bookmark", "open {my|the} {jira|github|gmail|recipes} bookmark",
                        "{go to|take me to} my {jira|github} bookmark"],
    "3 search bookmarks": ["search my bookmarks for recipes", "{search|look through|find in} my bookmarks "
                           "{for|about} {recipes|python|jetson}", "{find|any} bookmarks {for|about|on} "
                           "{recipes|python|jetson}", "do i have {a|any} bookmarks {for|about|on} {recipes|python}"],
    "3 bookmarked about": ["what did i bookmark about python", "{what did i bookmark|what have i bookmarked} {about|on} "
                           "{python|recipes|jetson}"],
    "3 bookmark folder": ["show my bookmarks in the recipes folder", "{show|list|open} {my|the} bookmarks in "
                          "{the|my} {recipes|work|tools} folder", "what's in my {recipes|work} bookmarks folder"],
    "3 bookmark this": ["bookmark this", "bookmark {this|this page|this site|it}", "{save|add} {this page|this} "
                        "{to|as a} bookmark{s|}", "{add|save} a bookmark {for this page|}"],
    "3 summarise video": ["summarise this video", "what's this video about",
                          "{summarise|summarize|sum up} {this|the} {video|youtube video}",
                          "summarise https://youtube.com/watch?v=abc123", "give me the gist of this video",
                          "what {is|'s} {this|the} {video|youtube video} about"],

    # 4A. Application control
    "4A anything frozen": ["is anything frozen", "{is|are} {anything|any app|any apps|any window|any windows} "
                           "{frozen|stuck|hung|not responding}", "what's {frozen|stuck|hung|not responding}",
                           "{check|look} for frozen {apps|windows}"],
    "4A app not responding": ["chrome isn't responding", "<app> {isn't|is not|has stopped|stopped} responding",
                              "<app> {is|} {frozen|stuck|hung|hanging|locked up}", "<app> froze",
                              "is <app> {frozen|stuck|hung}"],
    "4A this app stuck": ["this app is stuck", "{this|the} {app|window|program} {is|seems} {stuck|frozen|hung|"
                          "not responding}", "{this|the} {app|window} froze"],
    "4A computer frozen": ["is my computer frozen", "{is|has} my {computer|pc|laptop|system|jetson} "
                           "{frozen|hung|locked up|stuck}"],
    "4A force quit": ["force quit this", "force {quit|close|kill} {this|this app|this window|<app>}",
                      "{kill|end} {this|the} {frozen|stuck} {app|window}", "{kill|end} <app>"],
    "4A copy into": ["copy this into slack", "{copy|put|paste|send} {this|the selection|what i selected|it} "
                     "{into|in|to|over to} {slack|gmail|vs code|the terminal|chrome}"],
    "4A paste clipboard into": ["paste my clipboard into the terminal", "paste {my clipboard|what i copied|it} "
                                "{into|in|to} {the terminal|slack|vs code|gmail}"],
    "4A copy link into": ["copy the link from chrome into vs code", "{copy|grab|take} the {link|url|address} "
                          "from {chrome|firefox|the browser} {into|to|and paste it in|and put it in} "
                          "{vs code|slack|the terminal}"],
    "4A copy error into": ["copy the error from the terminal into chrome", "{copy|grab|take} the error "
                           "{from|in} the terminal {into|to} {chrome|slack|gmail}"],
    "4A copy window into": ["copy this window into slack", "copy {this|the} {window|terminal|terminal's text} "
                            "{into|to} {slack|gmail|chrome}"],
    "4A transform into": ["summarise this and put it in gmail", "{summarise|summarize|translate|shorten} "
                          "{this|it|the selection} and {put|paste} it {in|into} {gmail|slack|vs code}",
                          "translate this {to|into} hindi and put it in slack"],

    # 4B. File intelligence
    "4B find by kind": ["find pdfs from last week", "find {my|the|} <doctype>s from <range>",
                        "{show|list|find} {me|} {the|my|} <kind> files from <range>",
                        "{any|which} <doctype>s did i get <range>"],
    "4B find in folder": ["show the zip files in downloads from this week", "find .docx files in downloads",
                          "find python files from today in projects",
                          "{show|find|list} {the|my|all|} <kind> files in <folder>",
                          "{show|find|list} {the|} <kind> files in <folder> from <range>"],
    "4B changed files": ["what files did i change today", "which files have i edited this week in projects",
                         "{what|which} files {did i|have i} {change|changed|edit|edited|touch|touched|modify|"
                         "modified|work on|worked on} <range>",
                         "{show|list} {me|} {the|my|} files i {changed|edited} <range>",
                         "{recently|most recently} {changed|edited|modified} files"],
    "4B read document": ["summarise this pdf", "{summarise|summarize|sum up} {this|the|my} "
                         "{pdf|document|doc|file|spreadsheet|presentation}", "{summarise|summarize} <file>",
                         "what's in <file>", "what does <file> say", "{read|go over} <file> {for me|}"],
    "4B ask document": ["what does my resume say about python", "what's the total in invoice.pdf",
                        "in this document, what's the deadline", "{in|from} this {document|pdf|file}, "
                        "{what's the deadline|who signed it|what's the total}",
                        "what's the {deadline|total|date} in {this document|<file>}",
                        "does my resume {mention|say anything about} {python|docker}"],
    "4B go through with claude": ["go through resume.pdf with claude", "go through {this|<file>|the pdf} with claude",
                                  "{open|read} <file> {with|in} claude {code|}"],
    "4B summarise folder": ["summarise the pdfs in downloads", "summarise the documents in ~/Documents/reports",
                            "summarise what i downloaded today", "{summarise|summarize|sum up} {the|all the|my} "
                            "{pdfs|documents|docs|files} in <folder>", "summarise {what|everything} i downloaded <range>"],
    "4B compare files": ["compare offer.pdf and offer_signed.pdf", "compare {budget.xlsx|offer.pdf|report.docx} "
                         "{and|with|to|against} {budget_v2.xlsx|offer_signed.pdf|report_final.docx}",
                         "what's the difference between offer.pdf and offer_signed.pdf"],
    "4B duplicates": ["find duplicate files in downloads", "any duplicates on my desktop",
                      "{find|show|look for|check for} {duplicate files|duplicates|copies|duplicate copies} "
                      "{in|on} <folder>", "{are there|do i have} {any|} {duplicates|duplicate files} {in|on} <folder>"],
    "4B make folder": ["make a folder called invoices in documents", "{make|create|add} a {new|} folder "
                       "{called|named} {invoices|receipts|old stuff} {in|inside|on} {documents|downloads|my desktop}",
                       "new folder {invoices|receipts} in {documents|downloads}"],
    "4B rename": ["rename this file to final report", "rename {this|this file|that|it|<file>} to "
                  "{final report|budget 2026|old notes}", "change {the|this} file's name to final report"],
    "4B move file": ["move resume.pdf to documents", "move <file> {to|into} {documents|downloads|my desktop|"
                     "the invoices folder}", "move <file> to a new folder called {archive|old}",
                     "put <file> in {documents|the invoices folder}"],
    "4B trash": ["delete old.txt", "put old.txt in the trash", "{delete|trash|remove|bin|get rid of} {old.txt|<file>}",
                 "{put|move|throw} {old.txt|<file>} {in|into|to} the {trash|bin|rubbish}"],
    "4B tidy": ["tidy my downloads", "{tidy|tidy up|clean up|organise|organize|sort} {my|the} {downloads|"
                "downloads folder|desktop}", "{sort out|clean} {my|the} {downloads|desktop} {folder|}"],
    "4B undo": ["undo that", "{undo|reverse|revert} {that|the last change|what you just did|it}",
                "put {it|them|everything} back", "{bring|get} {them|it} back"],
    "4B meaning search": ["find my job offer", "find my {job offer|offer letter|cv|bank statement|insurance papers}",
                          "where's my {job offer|offer letter|cv}", "{look for|search for} my {job offer|cv}"],

    # 4C. System intelligence
    "4C network": ["why is my internet so slow", "what's wrong with my wifi", "diagnose my network",
                   "my wifi keeps dropping", "websites aren't loading", "why is {my|the} {internet|wifi|network|"
                   "connection} {so|} {slow|bad|not working|down|flaky}", "what's wrong with {my|the} "
                   "{internet|network|connection}", "{diagnose|check|test|troubleshoot} {my|the} {network|wifi|internet|"
                   "connection}", "{my|the} {wifi|internet} {keeps|is} {dropping|cutting out|disconnecting}",
                   "{websites|pages|sites} {aren't|won't|are not} {loading|opening}", "{is the|} internet {down|broken}"],
    "4C wifi signal": ["how strong is my wifi signal", "how {strong|good|weak} is {my|the} wifi {signal|}",
                       "{what's|check} {my|the} wifi {signal|strength|signal strength}"],
    "4C list audio": ["what audio devices are there", "{what|which} {audio|sound} devices {are there|do i have|are "
                      "connected}", "{list|show} {my|the} {audio|sound|speaker} devices", "{list|show} {my|the} speakers"],
    "4C switch audio": ["switch audio to the echo dot", "use the usb speaker", "switch to headphones",
                        "{switch|change|move|send} {the|} {audio|sound|output} to {the|my} {echo dot|echo|bluetooth|"
                        "usb speaker|headphones|jack}", "{use|play through|play on} {the|my} {echo dot|usb speaker|"
                        "headphones|bluetooth speaker}", "{switch|go} to {the|my} {headphones|speaker|echo dot}"],
    "4C which speaker": ["which speaker am i using", "{which|what} {speaker|audio output|output device} "
                         "{am i using|is on|is playing}", "where is the sound {coming from|going}"],
    "4C which mic": ["which mic are you using", "{which|what} {mic|microphone} {are you using|is on|am i using}",
                     "what {mic|microphone} {do you|are you} {use|listening with}"],
    "4C mic volume": ["set the mic volume to 60", "{set|put|change} the {mic|microphone} {volume|level|gain} to "
                      "{60|80|50} {percent|}", "{mic|microphone} {volume|level} {60|80}"],
    "4C mic up": ["turn the mic up", "turn {the|my} {mic|microphone} {up|down}", "{raise|lower|increase|decrease} "
                  "the {mic|microphone} {volume|level|}"],
    "4C use mic": ["use the usb mic", "{use|switch to} the {usb|webcam|headset} {mic|microphone}"],
    "4C notifications": ["any new notifications", "read my notifications", "what notifications did i miss",
                         "what was that notification", "{any|got any|do i have any} {new|} notifications",
                         "{read|show|check} {me|} my notifications", "what {did that notification say|was that "
                         "popup|just popped up}", "did i miss {any|} notifications"],
    "4C notifications from": ["notifications from slack", "what did slack send me", "{any|show} notifications from "
                              "{slack|gmail|discord|chrome}", "what did {slack|discord|gmail} {send|tell} me",
                              "{any|} messages from {slack|discord}"],
    "4C summarise notifications": ["summarise my notifications", "{summarise|summarize|sum up} {my|the|all my} "
                                   "notifications"],
    "4C forget notifications": ["forget my notifications", "{clear|forget|dismiss|delete} {my|the|all} notifications"],
    "4C resolution": ["what's my screen resolution", "what {resolution|refresh rate} {is my screen|am i using|"
                      "is the screen at}", "what screens are connected", "{which|how many} {screens|monitors|displays} "
                      "{are connected|do i have}"],
    "4C set resolution": ["set the resolution to 1280 by 720", "{set|change|switch} the {screen|} resolution to "
                          "{1280 by 720|1920 by 1080|1080p|full hd|4k|720p}", "{use|go to} {1080p|full hd|4k} "
                          "{resolution|}", "use the recommended resolution", "{use|set} the {highest|best|recommended|"
                          "native} resolution"],
    "4C refresh rate": ["change the refresh rate to 60", "{set|change|switch} the refresh rate to {60|75|144} "
                        "{hz|hertz|}", "use the highest refresh rate", "{max|maximum|highest} refresh rate"],
    "4C install": ["install vlc", "please install obs on my computer", "install {vlc|obs|gimp|htop|ffmpeg} "
                   "{on my computer|for me|}", "{get|download and install|set up} {vlc|obs|gimp} {for me|}",
                   "i need {vlc|obs} installed"],
    "4C pip install": ["install the python package requests", "pip install numpy", "install {the|} python "
                       "{package|library|module} {requests|numpy|flask}", "pip3 install {numpy|flask}"],
    "4C uninstall": ["uninstall gimp", "{uninstall|remove|get rid of} {gimp|vlc|obs} {from my computer|}"],
    "4C is installed": ["is ffmpeg installed", "do i have docker installed", "{is|have i got} {ffmpeg|docker|vlc|git} "
                        "installed", "do i have {ffmpeg|docker|vlc} {installed|on my computer|}"],

    # 5. Personal memory
    "5 remember preference": ["remember that i prefer battery life over performance",
                              "remember that i prefer {dark mode|vim|firefox|tea over coffee|battery life}",
                              "{remember|note} {that|} my favourite {editor|browser|colour} is {vim|firefox|blue}"],
    "5 casual preference": ["i prefer battery life over performance", "my favourite editor is vim",
                            "i'd rather use firefox", "i really hate loud notifications",
                            "i {prefer|like|love} {dark mode|vim|tea over coffee|short answers}",
                            "my favourite {browser|colour|editor} is {firefox|blue|vim}",
                            "i {hate|can't stand|don't like} {loud notifications|light mode}"],
    "5 show memory": ["what do you know about me", "show me your memory", "{what|tell me what} do you "
                      "{know|remember} about me", "{show|open|list} {me|} {your|my} {memory|memories}",
                      "what have you {saved|stored|remembered} about me"],
    "5 forget number": ["forget number 3", "forget the second one", "forget {number|} <num>", "forget the "
                        "{first|second|third|last} one", "{delete|remove} {number|} <num> from your memory"],
    "5 change number": ["change number 2 to i prefer tea", "{change|edit|update} number <num> to i prefer tea"],
    "5 private number": ["make number 3 private", "make {number|} <num> {private|public}",
                         "make that {private|public}"],
    "5 forget secret": ["forget my locker code", "{forget|delete|remove} my {locker code|wifi password|pin}"],
    "5 recall": ["do you remember where i parked", "where did i park", "who is my doctor",
                 "{do you remember|do you know} {where i parked|who my doctor is}",
                 "{where's|where is} my car {parked|}", "what do you remember about {the car|my doctor|parking}"],
    "5 forget conversation": ["forget this conversation", "{forget|clear|reset|wipe} {this|the|our} "
                              "{conversation|chat}", "start a new conversation"],
    "5 remember privately": ["remember privately that my bank is hdfc", "remember that my bank is hdfc, keep it private",
                             "{privately|secretly} remember that my bank is hdfc", "make that private"],
    "5 secret": ["remember my locker code is 4512", "my wifi password is hunter22",
                 "remember my {locker code|bike lock code|gate code} is {4512|1234}"],
    "5 read secret": ["what's my locker code", "what's my {locker code|wifi password|gate code}",
                      "{show|tell} me my {locker code|wifi password}"],
    "5 list secrets": ["list my secrets", "{list|show|what are} my {secrets|passwords|saved secrets}",
                       "what secrets {do you have|have you saved}"],

    # 6. Autonomous agent
    "6 task status": ["how's the task going", "{how's|how is} {the|my} task {going|coming along|doing}",
                      "{what's|how far is} {the|} task {at|along}", "is the task done {yet|}"],
    "6 show task": ["show the task", "{show|open|bring up} {the|my} {task|task card}", "show me the task"],
    "6 task history": ["what tasks did you do", "{what|which} tasks {did you do|have you done|did you run} {today|}",
                       "{show|list} {me|} {the|my|} {past|recent|old} tasks"],
    "6 pause task": ["pause the task", "{pause|hold|stop for a moment} {the|this|my} task", "put the task on hold"],
    "6 continue task": ["continue the task", "{continue|resume|carry on with|keep going with|unpause} {the|this|my} task"],
    "6 skip step": ["skip that step", "skip {that|this|the} step", "{move on|go on} to the next step"],
    "6 retry step": ["try that step again", "{try|run|do} {that|this|the} step again", "retry {the|that} step"],
    "6 stop task": ["stop the task", "{stop|cancel|end|abort|kill} {the|this|my} task"],
    "6 hand to claude code": ["hand the task to claude code", "{hand|give|pass} {the|this} task {over|} to claude code",
                              "let claude code {do|finish} {the|this} task"],

    # 7. Productivity
    "7 briefing": ["brief me", "morning briefing", "what's my day look like", "what do i have today",
                   "what's on my plate today", "{give me|read me|start} {my|the} {morning|daily} briefing",
                   "{what does|how does} my day look {like|} {today|}", "what's {on|} {my plate|the agenda} today",
                   "{good morning|} what's {happening|on} today"],
    "7 briefing off": ["turn off the daily briefing", "{turn off|stop|disable|no more} {the|my} {daily|morning} "
                       "briefing{s|}"],
    "7 briefing on": ["start the daily briefings", "{turn on|start|enable} {the|my} {daily|morning} briefing{s|}"],
    "7 remember note": ["remember that the spare key is under the mat", "{remember|note|make a note} that "
                        "{the spare key is under the mat|the meeting moved to friday}",
                        "{note|jot} down that the meeting moved to friday"],
    "7 what do you remember": ["what do you remember", "what do you remember about {the key|the meeting}"],
    "7 add todo": ["add buy milk to my todo list", "{add|put} {buy milk|call raj|pay rent} {to|on} my "
                   "{todo|to-do|to do} list", "{add a|new} {todo|task} {buy milk|call raj}"],
    "7 read todos": ["what's on my todo list", "{what's|read|show me} {on|} my {todo|to-do|to do} list",
                     "what {do i have|is left} to do"],
    "7 todo done": ["mark buy milk done", "{mark|tick|check} {buy milk|call raj} {as|} {done|complete|finished}"],
    "7 remind in": ["remind me in 20 minutes to stretch", "remind me {in 20 minutes|in an hour|in 5 minutes|"
                    "in half an hour} to {stretch|drink water|call raj}", "remind me to {stretch|drink water} "
                    "{in 20 minutes|in an hour}", "set a reminder {for|in} {20 minutes|5 minutes} to stretch"],
    "7 remind at": ["remind me at 5 pm to call the vendor", "remind me at {5 pm|9 am|noon|6:30} to {call the vendor|"
                    "send the report}", "remind me {tomorrow at 9 am|at 5} to send the report"],
    "7 reminders": ["what are my reminders", "{what|which} reminders {do i have|are set}", "{list|show|read} my reminders"],
    "7 cancel reminder": ["cancel the stretch reminder", "{cancel|delete|remove|forget} the {stretch|water} reminder"],
    "7 timer": ["set a timer for 2 minutes", "{set|start} a timer for {2|5|10} minutes", "{set|start} a {2|5} minute timer",
                "timer {for|} {2|5} minutes"],
    "7 day tracker": ["start studying", "how's my day", "how long did i work today", "{start|begin} {studying|working}",
                      "how {long|much} did i {work|study} {today|this week}"],

    # 10. Proactive
    "10 alerts off": ["stop the cpu alerts", "don't warn me about that again", "{stop|turn off|disable} the "
                      "{cpu|memory|disk|temperature} {alerts|warnings}", "{don't|stop} {warn|warning} me about "
                      "{that|the cpu|the disk} {again|}"],
    "10 alerts on": ["turn the cpu alerts back on", "{turn|switch} the {cpu|memory|disk} {alerts|warnings} {back|} on",
                     "{start|enable} the {cpu|disk} alerts {again|}"],
    "10 quiet hours set": ["quiet hours from 10 pm to 6 am", "{set|} quiet hours {from|} {10 pm|11 pm} {to|till|until} "
                           "{6 am|7 am}"],
    "10 quiet hours off": ["turn off quiet hours", "{turn off|disable|no more|stop} quiet hours"],
    "10 quiet hours what": ["what are my quiet hours", "{when|what} are {my|the} quiet hours"],
    "10 why notification": ["why did that notification appear", "why did you say that",
                            "why did {that|this} {notification|alert|message|popup} {appear|show up|come up|pop up}",
                            "why {did you|are you} {say|saying|tell|telling me} that"],
    "10 recent alerts": ["show my recent alerts", "what did i miss", "{show|list} {me|} {my|the|} {recent|today's} "
                         "{alerts|heads-ups|warnings}"],
    "10 tell when done": ["tell me when this finishes", "let me know when the build is done",
                          "notify me when npm install finishes", "{tell me|let me know} when {this|it|the build|"
                          "the install|the command} {finishes|is done|is finished|completes|ends}",
                          "{notify|ping|alert} me when {this|the build|npm install} {finishes|is done}"],

    # 13. Advanced
    "13 everything about": ["everything about study buddy", "what do i have on avtec", "what's linked to the camera "
                            "calibration", "all my stuff about the nano", "everything {about|on} <project>",
                            "what do i have {on|about} <topic>", "{show|tell} me everything {about|on} <project>",
                            "all {my|the} {stuff|things|notes|files} {about|on} <topic>",
                            "what's {linked|connected|related} to <topic>"],
    "13 dashboard": ["show my dashboard", "what are you working on", "show running tasks", "what's in progress",
                     "{show|open|bring up} {my|the} dashboard", "{what|which} tasks are running",
                     "{show|list} {me|} {the|} running {tasks|jobs}", "what's {going on|happening} {right now|}"],
    "13 close dashboard": ["close the dashboard", "{close|hide|dismiss|get rid of} {the|my} dashboard"],
}


def expand(template):
    """Every string a template stands for."""
    parts = re.split(r"(\{[^{}]*\}|<\w+>)", template)
    choices = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            choices.append(part[1:-1].split("|"))
        elif part.startswith("<") and part.endswith(">") and part[1:-1] in SLOTS:
            choices.append(SLOTS[part[1:-1]])
        else:
            choices.append([part])
    for combo in itertools.product(*choices):
        phrase = "".join(combo)
        if re.search(r"\{|<\w+>", phrase):  # a slot inside a choice
            yield from expand(phrase)
        else:
            yield re.sub(r"\s+", " ", phrase.replace(",?", ",")).strip(" ,")


def variants():
    """{feature: [phrase, ...]}, doc wording first, then templates, then polite forms of the first."""
    out = {}
    for name, templates in FEATURES.items():
        seen = []
        for t in templates:
            for phrase in expand(t):
                if phrase not in seen:
                    seen.append(phrase)
        for wrap in POLITE:
            phrase = wrap.format(seen[0])
            if phrase not in seen:
                seen.append(phrase)
        out[name] = seen
    return out


def check(groups):
    sys.argv = [sys.argv[0]]
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import dryrun
    dry = dryrun.DryJarvis()
    total = bad = 0
    for name, phrases in groups.items():
        want, _ = dry.route(phrases[0])
        misses = []
        for phrase in phrases[1:]:
            total += 1
            got, why = dry.route(phrase)
            if got != want:
                misses.append((phrase, got, why))
        bad += len(misses)
        print("%-30s %-28s %4d/%-4d" % (name, want, len(phrases) - 1 - len(misses), len(phrases) - 1))
        for phrase, got, why in misses:
            print("    MISS %-60s got %s  %s" % (phrase, got, why[:60]))
    print("\n%d/%d variants route like the doc's wording" % (total - bad, total))


def main(argv):
    groups = variants()
    if argv[:1] == ["--check"]:
        if argv[1:]:
            groups = {k: v for k, v in groups.items() if argv[1].lower() in k.lower()}
        check(groups)
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Generated by tools/variants.py from docs/feature-gaps.md (built features). Do not edit.\n")
        f.write("# Each block starts with the doc's own wording.\n")
        for name, phrases in groups.items():
            f.write("\n## %s (%d)\n" % (name, len(phrases)))
            f.writelines(p + "\n" for p in phrases)
    print("%d wordings for %d features -> %s" % (sum(map(len, groups.values())), len(groups),
                                                os.path.relpath(OUT, HERE)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
