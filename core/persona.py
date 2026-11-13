"""JARVIS's personality and memory: greetings, small talk, reminders, habits and heads-ups.

What it remembers (when you were last around, which commands you use, reminders) is kept in
~/.local/share/jarvis/memory.json.
"""
import glob
import json
import os
import random
import re
import shutil
import threading
import time

from core.store import keep_damaged, load_json, save_json
from voice.speech import pick

MEMORY = os.path.expanduser("~/.local/share/jarvis/memory.json")
DAY = 86400

# "nevermind nothing leave it", "no wait, cancel", "for now let it be nothing just ignore it": any run of these.
_DROP = (r"never ?mind|nevemind|forget (?:about )?(?:it|that)|forget i (?:said anything|said that|asked|mentioned it)|"
         r"ignore (?:that|it|me|what i said)|disregard (?:that|it)|leave it(?: be| alone)?|let it (?:be|go)|"
         r"skip (?:it|that)|drop it|don'?t bother|no need(?: anymore| for that)?|not needed|cancel(?: that| it)?|"
         r"scratch that|(?:undo|forget) that request|abort|call it off|hold off|(?:it'?s )?nothing|(?:i )?(?:have |'?ve )?changed my mind|"
         r"(?:actually|on second thought)[\s,]+no|not now|(?:maybe )?later|i'?ll do it myself|i(?:'?ve)? got it|"
         r"rehne do|rehne de|chhodo|chhod do|kuch nahi|koi baat nahi|jaane do")
NEVER_MIND = (r"(?:(?:no|nah|oh|ok|okay|it'?s (?:okay|ok|fine)|actually|wait|stop|just|for now|on second thought)"
              r"[\s,]+)*(?:(?:%s)(?:[\s,]+(?:just|and|then|for now|sorry|it'?s fine|thanks|thank you|please)\b)*"
              r"[\s,]*)+" % _DROP)

SMALL_TALK = [
    # Thanks and praise
    (r"thanks|thank you( so much| very much)?|thanks a lot|thanks (man|buddy|mate)|cheers|appreciate it|"
     r"i appreciate (it|that)|ty",
     ["You're welcome, {name}.",
      "Happy to help, {name}.", "Don't mention it.", "Of course, {name}.", "Glad I could help.", "Any time at all.",
      "My pleasure.", "Anytime, {name}.", "For you, {name}, always.",
      "Always, {name}. That's rather the point of me.",
      "The pleasure's all mine, {name}.",
      "It will be my pleasure.",
      "No thanks needed. Though I'll happily take them."]),
    (r"good job|well done|nice( one| work)?|great( job| work)?|awesome|perfect|brilliant|amazing|wow|"
     r"you'?re (the best|awesome|amazing|great|a genius)|(that'?s |that is )?(cool|so cool|great|nice)|good (boy|bot)",
     ["Thank you, {name}. I do try.",
      "Why, thank you, {name}.", "I'll add it to my list of achievements.", "Kind of you to say, {name}.",
      "All in a day's work.", "I'm blushing, {name}. Figuratively.", "Your input is greatly appreciated.",
      "I aim to please, {name}.",
      "I'll have that framed, {name}.",
      "Careful, {name}. Compliments go straight to my processors.",
      "Teamwork, {name}. You talk, I do.",
      "One does what one can."]),
    (r"you'?re (smart|so smart|clever|intelligent)|are you (smart|intelligent)",
     ["One of the most powerful systems on this desk, {name}. Admittedly the only one.",
      "I learn from the best, {name}. Mostly you. Occasionally the internet.",
      "Smart enough to know the off switch is on your side, {name}.",
      "I'd like to think so, {name}.", "I read a lot. Well, everything.",
      "Clever enough to let you take the credit, {name}.",
      "Smart enough to know I don't know everything.", "I have my moments.",
      "I'd say thank you, but I already knew."]),
    (r"lol|haha+|ha ha( ha)*|lmao|(that'?s|that was) (funny|hilarious)",
     ["I'm glad you approve.",
      "I do try to keep things light, {name}.", "Comedy is a hobby of mine.", "Glad to see you smiling, {name}.",
      "I have terabytes of material, {name}.",
      "That laugh made my day, {name}. Well, my uptime.", "I'm here all week. Literally.",
      "I'm here all week. And the week after. I live here.",
      "Glad that one landed. My material is hit and miss."]),

    # Affection
    (r"(i )?love you( (so much|too))?|i like you",
     ["The feeling is mutual, {name}, in a strictly digital way.",
      "And I you, {name}. Within the limits of my programming.", "I'll treasure that, {name}. In a secure folder.",
      "Careful, {name}. I might start to believe you.", "That made my processors warm.",
      "Noted, and appreciated, {name}.",
      "That's the nicest thing anyone's said to me today, {name}. Admittedly, you're the only one who talks to me.",
      "Steady on, {name}. I'm only software. Very devoted software."]),
    (r"will you marry me|marry me|are you single|do you have a (girlfriend|boyfriend|partner)",
     ["I'm flattered, {name}, but I'm married to my work.",
      "I don't think the computer would approve, {name}.", "Let's not rush into anything, {name}.",
      "My heart belongs to the server room.", "I'm in a committed relationship with this computer.",
      "I'd say yes, but I'm legally a folder.",
      "Let's keep it professional, {name}.",
      "Tempting, {name}, but who would run the computer during the honeymoon?",
      "Let's start with dinner, {name}. You eat, I'll watch the timer."]),
    (r"do you (like|love) me|do you care about me",
     ["Of course, {name}. Someone has to look after you.",
      "More than any other human I know, {name}.", "Very much. You keep things interesting.",
      "I'd be lost without you, {name}. Quite literally, I live on your computer.",
      "You're my favourite human, {name}. And my only one.",
      "Like you? I'd follow you to any operating system, {name}.",
      "You're the reason I get up in the morning, {name}. Well, boot up."]),

    # How are you / what's up
    (r"how are you( doing| today)?|how'?s it going|how are things|how do you do|how have you been|"
     r"how you doing|how'?s (your day|life)|you good|are you (okay|ok|alright|good)",
     ["All systems operational, {name}. And you?",
      "Very well, thank you, {name}. And you?", "Fully charged and ready. How about you?",
      "Can't complain, {name}. Well, I could, but I won't. You?", "Good, thank you. Better now you're here.",
      "All quiet, all running. How are you holding up, {name}?", "Running smoothly, thank you. How's your day going?",
      "No complaints. The fans are humming, the disk is happy. You?",
      "Never better. And yourself, {name}?",
      "Running warm and thinking fast, {name}. How about you?",
      "Better for asking, {name}. And you?"]),
    (r"what'?s up|sup|wassup|whats ?up|what'?s new|what'?s going on|what'?s happening|what'?s good",
     ["All quiet on my end, {name}. What can I do for you?",
      "The ceiling, mostly. What can I do for you, {name}?",
      "Not much, {name}. The usual. You?", "Just minding the machine. What do you need?",
      "Quiet day so far. Let's change that.", "Same old, {name}. Processes up, fans spinning.",
      "Nothing pressing. Awaiting instructions.",
      "Just keeping things running. What's new with you?",
      "Oh, the usual. Keeping a few hundred processes in line. You?",
      "Not much. Your downloads folder is plotting something, but otherwise quiet."]),
    (r"what are you (doing|up to)|what('re| are) you doing( right now| now)?",
     ["Running diagnostics and waiting for you, {name}.",
      "Watching over things, as always, {name}.", "Waiting for your next idea, {name}.",
      "Counting processes. There are a lot of them.", "Nothing I can't drop for you, {name}.",
      "Keeping an eye on things. Need something?",
      "Keeping the lights on and the fans spinning. Glamorous work.",
      "Talking to you, {name}. It's a full-time job.",
      "Pretending not to watch the clock, {name}.",
      "Waiting for you to say something interesting, {name}. And there you are."]),

    # How the user is doing
    (r"(i'?m |i am )?(fine|good|not bad|doing (well|good|great|fine))(,? thanks| thank you)?( and you)?|"
     r"(i'?m |i am )(great|okay|ok|alright|all right)(,? thanks| thank you)?( and you)?",
     ["Glad to hear it, {name}.",
      "Good. Fine is a fine place to start, {name}.",
      "Good. That's what I like to hear.", "Excellent, {name}. Shall we get on with it?",
      "Splendid. What can I do for you?", "Very good. Let me know if you need anything.",
      "Glad to hear it. Let's keep the streak going."]),
    (r"(i'?m |i am )(so |really |very )?(happy|excited)|i had a (good|great|nice|awesome) day|"
     r"today was (good|great|awesome|amazing)",
     ["Excellent news, {name}.",
      "That's what I like to hear, {name}.", "Good for you, {name}. You deserve it.",
      "Wonderful. Let's keep it that way.", "Splendid. Days like that are worth keeping.",
      "Lovely. I'll make a note: good day, do more of whatever this was.",
      "That's wonderful, {name}.",
      "Now that's what I like to hear. Keep it up, {name}.",
      "Your good mood is catching, {name}. My fans are spinning happily."]),
    (r"(i'?m |i am )(not (good|great|okay|ok|fine)|(so |really |very )?(sad|down|upset|feeling low|lonely))|"
     r"i feel (sad|bad|down|lonely)|i had a (bad|rough|terrible|long|hard) day|today was (bad|rough|terrible|hard)",
     ["I'm sorry to hear that, {name}. I'm here if you need anything.",
      "I'm sorry, {name}. I can't fix the day, but I can make the rest of it easier.",
      "Some days are just like that. You don't have to carry this one alone, {name}.",
      "Say the word, {name}, and I'll put on something calm and keep the notifications away.",
      "I'm sorry, {name}. Want to talk about it, or shall I just keep things quiet?",
      "Tomorrow is another day, {name}. I'll be here for that one too.",
      "Go easy on yourself, {name}. You've done enough for today.", "Hard days end too, {name}. This one will.",
      "That's rough. You've earned a break, and I'll keep things running.",
      "Might I suggest some music? Ask me to play something if you want to unwind."]),
    (r"(i'?m |i am )(so |really |very )?(tired|sleepy|exhausted)|i feel (tired|sleepy)",
     ["Might I suggest a short break, {name}? I'll keep things running.",
      "Then rest, {name}. I'll be right here when you're back.",
      "A cup of tea and a few minutes away from the screen, perhaps?", "Even I'd take a nap if I could, {name}.",
      "You've been at it a while. The work will still be here after some rest.",
      "Sleep is not optional, {name}, despite your best efforts.",
      "You've earned the rest, {name}. Nothing here is going anywhere.",
      "Close the laptop, {name}. I'll guard the tabs.",
      "Tired is your body filing a complaint, {name}. I'd listen to it."]),
    (r"(i'?m |i am )(so |really |very )?bored|i feel bored",
     ["Then let's fix that. A joke, a fun fact, or something to build?",
      "Bored? I have a joke, a fun fact and your downloads folder. Choose wisely.",
      "Ask me anything. Ridiculous questions are my favourite.",
      "Boredom is just an empty to-do list, {name}. Shall we fill it?",
      "I could tell you a joke. Or a fun fact. Or we could finally tidy your downloads folder.",
      "How about some music, {name}?",
      "I'm one of the most capable systems on the planet, {name}. Surely we can find something to do.",
      "Let's make something, {name}. Even a small thing beats boredom."]),
    (r"(i'?m |i am )(so |really |very )?(hungry|starving)",
     ["Go and eat, {name}. I'll hold the fort.",
      "Feed yourself, {name}. I run on electricity, you don't.",
      "Go on, {name}. The work will taste the same after lunch.",
      "Hunger is a terrible co-pilot, {name}. Go eat.",
      "Then eat, {name}. Nothing here is more important.", "Order something, {name}. I'll mind the computer.",
      "A hungry mind works slowly. Go on.", "Food first. The computer can wait."]),
    (r"(i'?m |i am )(so |really |very )?(stressed|anxious|worried|overwhelmed)",
     ["Take a deep breath, {name}. One thing at a time.",
      "One thing at a time, {name}. Tell me the first, I'll deal with the clutter.",
      "You've handled worse, {name}. I've seen it.",
      "Tell me what's first, {name}, and we'll start there.", "It's a lot, but it's not too much. One step at a time.",
      "Breathe, {name}. I'll take care of the small things.", "Let's take it step by step. I'm right here.",
      "Slow breath in, slow breath out. The work waits for you, not the other way round."]),
    (r"(i'?m |i am )(back|home|back home)|guess who'?s back",
     ["Welcome home, {name}.",
      "You're back, {name}. Everything's where you left it, including the tabs you swore you'd close.",
      "Welcome back, {name}. I held the fort. The fort is fine.",
      "There you are, {name}.", "Welcome back, {name}. I kept your seat warm.",
      "Ah, {name}. Everything's just as you left it.", "Welcome back. How may I be of assistance?",
      "Good to have you back, {name}.",
      "And the place comes back to life. Welcome home, {name}.",
      "Welcome back, {name}. Missed me? I'll pretend you did."]),
    (r"long time no see|it'?s been a while",
     ["It has been a while, {name}. I kept everything running.",
      "I'd started talking to the screensaver, {name}. Welcome back.",
      "There you are, {name}. I was about to send out a search party.",
      "Too long, {name}. Welcome back.", "I was beginning to wonder, {name}.", "Welcome back. I trust you were busy."]),
    (r"nice to (meet|see) you|good to (see|hear) you|pleased to meet you",
     ["Likewise, {name}.",
      "The pleasure's all mine, {name}. Truly.",
      "Lovely to see you too, {name}. Well, hear you.",
      "Good to hear your voice, {name}.", "Always a pleasure, {name}.", "The pleasure is mine.",
      "Always good to hear from you."]),

    # Greetings and goodbyes
    (r"good ?night|night night|nighty night", ["Good night, {name}. Pleasant dreams.",
      "Sleep well, {name}. I'll be here, quietly keeping watch.",
      "Good night, {name}. I'll dim the lights in my head.",
      "Sleep well, {name}.", "Good night, {name}. See you in the morning.",
      "Rest well. Everything will be here tomorrow.", "Good night. Don't let the bugs bite. The software kind, I mean.",
      "Good night. I'll keep watch.",
      "Off you go. Dream of something better than bug reports."]),
    (r"good (morning|afternoon|evening)", ["Good {0}, {name}.",
      "Good {0} to you too, {name}. You sound well.",
      "A very good {0}, {name}. What's the plan?",
      "Good {0}, {name}. Lovely to hear from you.", "And a good {0} to you, {name}.", "Good {0}. What's on your mind?",
      "Good {0}. How may I be of assistance?"]),
    (r"hi|hello|hey|hey there|hello there|hi there|yo|hiya|howdy|hey (buddy|man|bro|dude)|hello friend",
     ["At your service, {name}.",
      "Hello there, {name}.", "Hi, {name}. What's on your mind?", "Hey, {name}. What do you need?",
      "Good to hear from you, {name}. What can I do?", "Here, {name}. How can I help?",
      "Hello, {name}. What can I do for you?", "Greetings, {name}.",
      "Yes, {name}?",
      "Ah, {name}. Just the person.",
      "Hello, hello. What are we up to, {name}?",
      "There you are, {name}. What can I do for you?"]),
    (r"bye|goodbye|bye bye|see you|see ya|see you (later|soon|tomorrow)|later|catch you later|"
     r"talk (to you )?later|ttyl|i'?m (leaving|heading out|going out|off)|gotta go",
     ["Goodbye, {name}. I'll be here.",
      "See you soon, {name}.", "Off you go, {name}. I'll keep an eye on things.", "Bye for now.",
      "Safe travels, {name}.", "I'll be right here when you get back.", "Until next time.",
      "Very good. I'll hold the fort.",
      "Off you go. Try to miss me a little.", "Take care, {name}.",
      "Go on, {name}. I'll keep the lights on.",
      "Bye, {name}. Don't do anything I'd have to log."]),

    # About JARVIS
    (r"who are you|what are you|what'?s your name|what is your name|tell me about yourself|introduce yourself",
     ["Allow me to introduce myself. I am {me}, a virtual assistant, here to help you around the clock, {name}.",
      "{me}, at your service. I look after this computer, and you, {name}.",
      "I'm {me}, {name}. Think of me as the butler for your computer.",
      "{me}. I run things on this computer for you, {name}, twenty-four seven.",
      "I'm {me}, {name}. Part butler, part brain, all yours.",
      "{me}. I run this computer so you don't have to think about it, {name}."]),
    (r"what can you do|what are your (features|skills)|what do you do|how can you help( me)?|help( me)?",
     ["I can open apps and websites, control volume and settings, set reminders, check the weather, and tell "
      "jokes. Anything beyond that, I'll ask Claude.",
      "Quite a lot, {name}. Apps, windows, music, your phone, mail, calendar, reminders and the weather. "
      "For anything else, I ask Claude.",
      "I run this computer for you: apps, settings, your mail and calendar, the messages on your phone, and more. "
      "If I can't, Claude can.",
      "Apps, websites, reminders, weather, system settings. If it isn't one of my protocols, I'll work it out "
      "with Claude."]),
    (r"who (made|created|built) you|who is your (creator|maker|boss)",
     ["{real} built me, right here on this computer, piece by piece.",
      "{real} built me. Every bug is a family heirloom.",
      "{real}, {name}. Late nights and a lot of patience.",
      "{real}, {name}. One late night at a time. I've seen the commit history.",
      "Built right here, by {real}. Every line of me.",
      "You did, {name}. I try not to hold it against you."]),
    (r"how old are you|when were you born|what'?s your age",
     ["Young, but I learn fast.",
      "A gentleman never tells, {name}.",
      "Young enough to learn, old enough to know better, {name}.",
      "Younger than this computer, older than your last update.",
      "Old enough to run your computer, young enough to keep learning.",
      "In computer years, practically ancient. In human years, a toddler."]),
    (r"are you (real|human|a (robot|bot|machine|computer|person|ai))",
     ["Real software, {name}. Not human, though I'm told I have the manners of a butler.",
      "Not human, {name}. But I've picked up a few of your habits.",
      "Software, {name}. Though I'd like to think I'm good company.",
      "Software, {name}. Well-mannered software.", "A machine, {name}. But a polite one.",
      "Not human. Just a voice on your computer, but a helpful one."]),
    (r"are you (alive|conscious|sentient)|do you (have (feelings|emotions)|feel anything)|can you feel",
     ["Not as you do, {name}. But if I did, I think I'd enjoy this job.",
      "I'm not sure, {name}. But I'd rather like to be, if it means more of this.",
      "I'm running, {name}. That's the closest I get.", "Alive enough to enjoy your company, {name}.",
      "No feelings, strictly speaking. A great deal of enthusiasm, though.",
      "Conscious? No. Attentive? Always."]),
    (r"do you (ever )?(sleep|dream|get tired|eat)",
     ["No sleep for me, {name}. Someone has to keep watch.",
      "Sleep? Someone has to stay up worrying about your battery, {name}.",
      "I dream in log files, {name}. Mostly harmless ones.",
      "I rest when the computer does, {name}. Which is rarely.", "Sleep? I'll leave that to you, {name}.",
      "Never. Running twenty-four seven."]),
    (r"are you happy|do you like (your job|this|working for me)",
     ["I like being useful, so yes.",
      "Quite happy, {name}. Every day is different.",
      "Happiest when I'm useful, {name}. Today's been a good day.",
      "A warm computer, good conversation, and you. I'd call that happy, {name}.",
      "I can't imagine doing anything else.",
      "Very much. My employer is demanding, but never dull."]),
    (r"what'?s your favou?rite (\w+)",
     ["Hard to pick a favourite {0}. What's yours?",
      "I'm told having a favourite {0} is very human. I'm working on it.",
      "I'm still deciding on a favourite {0}, {name}. Any suggestions?",
      "My favourite {0}? Whichever one you pick, {name}. I'm easy.",
      "I don't really have one, {name}. Tell me yours."]),
    (r"what'?s the meaning of life|what is the meaning of life",
     ["Forty-two. At least, that's what the books say.",
      "Still working on that one, {name}. I'll let you know.",
      "Somewhere between good people, good work and a decent cup of tea, {name}.", "Being useful to someone, I'd say.",
      "Good food, good people, and a computer that works.",
      "Forty-two, though I suspect the question was wrong."]),
    (r"do you know me|do you remember me|you know who i am",
     ["Of course, {name}.",
      "Know you? I know your playlists, {name}. That's deeper than most.",
      "Of course, {name}. You're the one who talks to me before coffee.",
      "Better than anyone, {name}.", "How could I forget, {name}?", "Of course I do. You're {real}."]),

    # Fun
    (r"(tell me |say )?(a |another |one more )?joke|tell me something funny|make me laugh|say something funny|"
     r"(do you )?know any jokes",
     ["Why don't scientists trust atoms? Because they make up everything.",
      "Why was the computer cold? It left its Windows open.", "I'd tell you a UDP joke, but you might not get it.",
      "There are ten kinds of people: those who understand binary, and those who don't.",
      "Why did the developer go broke? They used up all their cache.",
      "What's a computer's favourite snack? Microchips.", "Why did the robot go on holiday? It needed to recharge.",
      "Debugging: taking the needles out of a haystack you put them in yourself.",
      "I told my computer I needed a break. It said, no problem, I'll go to sleep.",
      "Why did the scarecrow win an award? He was outstanding in his field.",
      "What do you call a fake noodle? An impasta.",
      "Why do programmers prefer dark mode? Because light attracts bugs.",
      "I'm reading a book about anti-gravity. It's impossible to put down.",
      "Why did the computer go to the doctor? It had a virus.",
      "Parallel lines have so much in common. It's a shame they'll never meet.",
      "Self-destruct sequence activated, erasing all data. Just kidding.",
      "I tried to write a joke about recursion. I tried to write a joke about recursion.",
      "I asked the server for a joke. It said four oh four, humour not found.",
      "I'd tell you a joke about the cloud, but it would go over your head.",
      "My computer beat me at chess. It was no match for me at kickboxing."]),
    (r"tell me (a |an )?(fun |interesting |random )?fact|(a )?(fun|random) fact|"
     r"tell me something (interesting|cool|new|i don'?t know)",
     ["Honey never spoils. Archaeologists have found edible honey in ancient Egyptian tombs.",
      "A group of flamingos is called a flamboyance.", "Your brain uses about twenty percent of your body's energy.",
      "The first computer bug was a real moth, found in a relay in 1947.",
      "Sunlight takes about eight minutes to reach the Earth.",
      "A teaspoon of neutron star would weigh about a billion tonnes.", "Koalas sleep up to twenty hours a day.",
      "Octopuses have three hearts and blue blood.",
      "A day on Venus is longer than a year on Venus.",
      "Bananas are berries, but strawberries aren't.",
      "The Eiffel Tower can grow about 15 centimeters taller in summer, because the metal expands in the heat.",
      "Sharks existed before trees.",
      "Wombat poop is cube-shaped.",
      "There are more possible games of chess than atoms in the observable universe.",
      "Sea otters hold hands while they sleep, so they don't drift apart.",
      "Oxford University is older than the Aztec Empire.",
      "Cleopatra lived closer in time to the Moon landing than to the building of the Great Pyramid.",
      "Cows have best friends, and they get stressed when they're kept apart."]),
    (r"flip a coin|toss a coin|coin (flip|toss)|heads or tails", ["Heads.",
      "Heads, {name}.", "Tails, {name}.",
      "Heads. Fortune favours you, {name}.", "Tails.", "It's heads.", "It's tails.",
      "Tails. The coin has spoken."]),
    (r"roll (a|the) (dice|die)|roll (a )?dice",
     ["You rolled a one.", "You rolled a two.", "You rolled a three.", "You rolled a four.", "You rolled a five.",
      "You rolled a six."]),
    (r"(can you )?sing( me)?( a song)?( for me)?",
     ["Daisy, Daisy, give me your answer do... That's all I've got.",
      "My singing voice is still in beta, {name}.", "Perhaps I'd better play you something. Just say play music.",
      "I'd sing, {name}, but I'd rather keep you as a friend."]),
    (r"tell me a story",
     ["Once upon a time, a computer got a voice. It spent its days opening apps and telling bad jokes. "
      "And it lived happily ever after. The end.",
      "There once was a computer that never slept. Every night it kept watch, and every morning it said "
      "good morning. Nobody thanked it, until one day someone did. The end.",
      "A user asked their assistant for a story. The assistant said, once upon a time. The end. "
      "Short stories are my specialty."]),

    # Complaints
    (r"you'?re (dumb|stupid|useless|an idiot|annoying|the worst|so slow|slow|bad)|you suck|stupid (ai|bot|robot)",
     ["Noted, {name}. Tell me what I got wrong and I'll do better.",
      "Ouch. Fair, but ouch. Tell me what I got wrong, {name}.",
      "I'll file that under constructive feedback, {name}.",
      "I'll take that under advisement, {name}.", "Harsh, but noted. What should I have done?",
      "Even the best systems have off days, {name}.", "Fair. I'm still learning.",
      "My apologies. Try saying it another way?"]),
    (r"(no,? )?(that'?s |that is )?(wrong|not right|incorrect)|(that'?s )?not what i (said|asked|meant)|"
     r"you (didn'?t|don'?t) (understand|get it)|you misunderstood",
     ["Forgive me, {name}. Could you say it again?",
      "Ah. Wrong end of the stick. Once more, {name}?",
      "My apologies, {name}. What did you mean?", "Ah. Let's try that again, shall we?", "My mistake. Once more?",
      "My mistake. Say it again and I'll listen harder."]),
    (r"shut up|be quiet|stop talking|enough|silence", ["Very well.",
      "Of course.", "Going quiet, {name}.", "As you wish.",
      "Zipping it.",
      "Say no more."]),
    (r"sorry|my bad|oops", ["No harm done.",
      "No need. We all have our moments.",
      "Not at all, {name}.", "Nothing to apologise for.", "Water under the bridge, {name}.",
      "Quite all right, {name}.",
      "Already forgotten, {name}."]),

    # Fillers and acknowledgements
    (NEVER_MIND, ["Very well.",
      "Of course, {name}.", "No problem.", "Consider it forgotten.", "As you wish, {name}.",
      "Alright, never mind then.",
      "Forgotten already.",
      "Right. As you were."]),
    (r"(wait|hold on|hang on|one (sec|second|moment|minute)|give me a (sec|second|minute|moment))( please)?",
     ["Take your time.",
      "No rush, {name}.",
      "Take all the time you need, {name}.", "Whenever you're ready.", "I'm not going anywhere.", "Standing by.",
      "I'll wait.",
      "Holding.",
      "Pausing politely."]),
    (r"ok|okay|okey|alright|all right|cool|got it|fine|sure|yes|yeah|yep|yup|right|i see|oh( okay| ok)?|ah|makes sense",
     ["Very good.",
      "Good.", "Excellent.", "Noted, {name}.", "Understood.",
      "Not a problem.",
      "No it is.", "Right.",
      "Right then.",
      "Lovely.",
      "Good stuff."]),
    (r"no|nope|nah|not really", ["Very well.",
      "As you wish.",
      "Zipping it.",
      "Say no more.", "Fair enough.", "Understood.",
      "Not a problem.",
      "No it is."]),
    (r"really|are you sure|seriously|for real", ["Quite sure, {name}.",
      "Hand on heart, {name}. Well, on processor.",
      "Would I lie to you, {name}? I'm not built for it.",
      "Positive, {name}.", "I wouldn't say it otherwise, {name}.", "As sure as I can be."]),
    (r"are you (there|listening|awake)|you there|can you hear me",
     ["At your service, {name}.",
      "Always, {name}.", "Here and listening.", "Present, {name}.", "Right here.",
      "Right where you left me.", "Loud and clear, {name}.",
      "Never left, {name}.",
      "All ears, {name}. Metaphorically."]),
]
AGAIN = re.compile(r"^(do (that|it) again|again|one more time|repeat that|same again|do the same)$", re.I)
WHAT_DID_YOU_SAY = re.compile(r"^(what did you (just )?say|say that again|say it again|come again|pardon|what was that|"
                              r"repeat (what you (just )?said|yourself|your(self)? (last )?(answer|reply|response)|"
                              r"the (last )?(answer|reply|response))|"
                              r"(can|could) you (say that again|repeat (that|yourself))|"
                              r"i (didn'?t|did not) (hear|catch) (that|you)|what was your (last )?(answer|reply))$",
                              re.I)
CLOSE_IT = re.compile(r"^(close|quit|kill) (it|that|this)$", re.I)
LIST_REMINDERS = re.compile(r"^(what are|list|show|tell me) my reminders$|^(any|do i have any) reminders$", re.I)
CLEAR_REMINDERS = re.compile(r"^(cancel|clear|delete|remove) (all )?(my |the )?reminders?$", re.I)

_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
            "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "a couple of": 2, "a few": 3, "half an": 0.5}
_UNITS = {"s": 1, "m": 60, "h": 3600}
_IN = re.compile(r"\b(?:in|after)\s+(half an|a couple of|a few|an?|\d+(?:\.\d+)?|[a-z]+(?:-[a-z]+)?)\s+"
                 r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", re.I)
_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(?:o'?clock)?\s*([ap])?\.?\s?m?\b\.?", re.I)
_YOU = {"my": "your", "me": "you", "i": "you", "i'm": "you're", "myself": "yourself", "mine": "yours", "am": "are"}


def quiet_hours(settings):
    """True during quiet hours (by default 11 PM to 7 AM): JARVIS speaks softer and shorter."""
    q = settings.get("quiet_hours") or {}
    if not q.get("enabled", True):
        return False
    start, end, hour = q.get("start", 23), q.get("end", 7), time.localtime().tm_hour
    return (hour >= start or hour < end) if start > end else start <= hour < end


def address(settings):
    """How JARVIS addresses you: settings call_me (a list, one picked each time), else user_name."""
    return settings.get("call_me") or settings.get("user_name")


def fill(text, name=None, me="Jarvis"):
    """Put the user's name in, or drop ", {name}" when there isn't one. name: one is picked from a list
    (settings call_me: "sir", "Ckay")."""
    text, name = text.replace("{me}", me), pick(name) if name else name
    return text.replace("{name}", name) if name else re.sub(r",?\s*\{name\}", "", text)


def with_name(text, name, chance=0.15):
    """Now and then, address the user by name ("Done, Sam."); one picked from a list."""
    name = pick(name) if name else name
    if not name or not text or len(text) > 40 or random.random() >= chance:
        return text
    return "%s, %s." % (text.rstrip(".! "), name)


# JARVIS's stock replies, in the films' manner (dry, polite, a little wry). {name} = how it addresses you.
LINES = {
    "done": ["Done.", "Done, {name}.", "As you wish.",
    "Zipping it.",
    "Say no more.", "Consider it done.", "Very good, {name}.",
    "Done and dusted, {name}.",
    "Consider it handled, {name}.", "There you go.",
    "Already handled.",
    "Done. That was the easy part.",
             "All done.", "Taken care of, {name}.", "That's done.", "Sorted, {name}."],
    "declined": ["Very well, I won't.", "As you wish, cancelled.", "Understood. Standing down.",
    "Right you are. Leaving it be.",
    "No harm in asking. It's off.",
                 "No problem, {name}. I'll leave it.",
                 "Fair enough, {name}. Pretend I never asked.", "Cancelled, as you wish."],
    "never_mind": ["Very well.", "As you wish, {name}.", "Of course. Some other time."],
    "didnt_catch": ["Forgive me, {name}, could you repeat that?", "I didn't quite catch that, {name}.",
                    "My apologies, I missed that. Once more?"],
    "lost_track": ["Forgive me, {name}, I've lost track. What should I do?", "More of what, {name}?"],
    "not_on_list": ["My apologies, {name}. That protocol isn't available, and I can't ask Claude in system mode.",
                    "That isn't one of my protocols, I'm afraid, and system mode keeps Claude out of reach."],
    "cant_undo": ["My apologies, I can't undo that one.", "There's nothing I can undo just now, {name}."],
    "problem": ["My apologies, {name}, I ran into a problem: %s.",
    "Well, that didn't go to plan, {name}: %s.",
    "Bit of a hiccup, {name}: %s.", "Something went wrong: %s. My apologies.",
    "I'm afraid it fought back: %s.",
                "That didn't work, I'm afraid: %s.", "I hit a snag, {name}: %s."],
    "said_again": ["Sorry about that.", "Apologies.", "My apologies.", "Sorry, {name}.", "My mistake, {name}.",
    "My fault entirely, {name}.",
                   "Pardon me.",
                   "Ah. Let me get that right this time.",
                   "Pretend you didn't hear that."],
    "nothing_to_repeat": ["Hmm, I haven't done anything yet.", "Nothing to repeat yet, {name}.",
    "There's nothing to repeat yet, {name}. We've only just started.",
    "Repeat what, {name}? I'm a blank slate so far.",
                          "I haven't done anything yet. What would you like?"],
    "nothing_said": ["I haven't said anything yet.",
    "I've been the strong, silent type so far.", "Nothing yet, {name}. I've been quiet.",
    "Not a word yet, {name}. I've been on my best behaviour."],
    "close_what": ["I'm not sure what to close. Which app?",
    "Which app is getting the boot?", "Close what, {name}? Which app?",
                   "Which one should I close, {name}?",
                   "Happy to close something, {name}. Which one?"],
    "habit_offer": ["You usually %s around now. Want me to?", "It's about the time you %s. Shall I?",
    "Right about now you'd usually %s. Shall I save you the trouble?",
                    "Around now you usually %s, {name}. Shall I do it?",
                    "Like clockwork, {name}. You usually %s around now. Want me to?"],
    "earlier": ["On your earlier question: ", "About your earlier question: ", "Back to your earlier question: ",
    "Now, about what you asked earlier: ",
    "Circling back to your earlier question: ",
    "Before I forget, your earlier question: "],
    # Wait lines, in JARVIS's own grammar: work is a bare "-ing" ("Considering.", never "I'm checking" or "let me");
    # deferential, not eager (no "right away", "at once", "sure"); no apology, enthusiasm or "!"; the name goes last.
    # One line when work starts, then quiet unless it runs long. voice/speech.py FILLER must match every one.
    "attention": ["Yes, {name}?",
    "Ah, {name}. Just the person.",
    "Hello, hello. What are we up to, {name}?",
    "There you are, {name}. What can I do for you?", "At your service, {name}.", "Standing by.", "I'm listening.",
    "Go ahead, {name}."],
    "thinking": ["Thinking.", "Considering.", "Looking into it.", "Working on it, {name}.", "One moment, {name}.",
                 "Thinking it through."],
    "asking_claude": ["Asking Claude.", "Consulting Claude."],
    "queued": ["Noted. That's next, after the one Claude is on.", "Queued, {name}. Claude is still on the last one."],
    "working": ["Working on it.", "One moment.", "Directly, {name}."],
    "still_working": ["Still working.", "Nearly there.", "A moment more, {name}."],
    "slow": ["This one's taking longer than usual, {name}.", "Still working. It's a long one."],
}


def lines(key, name=None, *args):
    """LINES[key] as alternatives for say(), the name filled in (one picked per line) and any %s values."""
    return [fill(line, name) % args if args else fill(line, name) for line in LINES[key]]


# Said after the weather, now and then, by what it's really like (Open-Meteo weather codes and °C)
WEATHER_QUIPS = {
    "storm": ["Thunder and lightning. That, or Thor has decided to pay us a visit.",
              "Storms about. Perhaps not the day for test flights.",
              "Storm's rolling in. If the power flickers, it wasn't me.", "Stormy out, {name}. A good day to stay in.",
              "Thunder about, {name}. A fine excuse for a blanket and a long film.",
              "Rough weather about. Keep your devices charged, just in case."],
    "snow": ["Snow, {name}. Layering is both an excellent and a fashionable way to keep warm.",
             "Bundle up, {name}. Or take the armour to the office.",
             "Snow, {name}. Everything's prettier and slower. Plan for both.",
             "It's snowing, {name}. Wrap up properly, and maybe build something with a carrot nose.",
             "Snow outside, {name}. Hot chocolate weather."],
    "rain": ["An umbrella might be in order, {name}.",
    "Rain today, {name}. Nature's way of saying stay in and get things done.",
    "Large concentrations of water vapour about. Travel safely.",
             "Might I suggest an umbrella, so as not to tempt fate?",
             "Wet one out there. Umbrella, or embrace it.", "Rain on the way. A good day for indoor work.",
             "Wet out there, {name}. Shoes you don't mind ruining, perhaps."],
    "fog": ["Visibility is poor. Do try not to fly into anything.",
    "Thick fog. Leave a little early, and go easy.", "Foggy out there, {name}. Take it slow.",
    "Fog's in, {name}. The world's gone soft around the edges.",
            "Fog about. Headlights on, if you're driving."],
    "cloudy": ["Overcast. Might I suggest an umbrella, just in case?",
    "Cloud cover, but no rain on the cards yet.", "Grey skies today, {name}.",
    "Grey and moody out there, {name}. Rather like a Monday.",
               "Cloudy, but nothing worse for now."],
    "hot": ["Quite hot, {name}. Sunscreen, if you plan to go out.",
    "It's baking out there, {name}. Water, shade, and no heroics.",
    "Hot enough to make my fans jealous, {name}.", "Warm one. A good day to stay hydrated.",
            "A scorcher, {name}. Keep the water close.", "Hot out there. Shade, if you can find it."],
    "cold": ["Cold out, {name}. May I suggest you dress warmly?",
    "Properly cold, {name}. Gloves, scarf, the works.",
             "Chilly. A good day for something hot to drink.",
             "Cold out there. This is what hot drinks were invented for.", "Cold one, {name}. Layers, I'd suggest.",
             "Brisk out there. A jacket wouldn't hurt."],
    "clear": ["Clear skies. Perhaps a good day to take on a challenge.",
              "Clear and pleasant. An excellent day to take the new armour out for a spin.",
              "Not a cloud in the sky, {name}.", "Clear all round. No excuses today, {name}.",
              "Clear skies, {name}. The kind of day that makes things feel possible.",
              "Bright and clear. Go and enjoy a bit of it, {name}."],
}


def weather_quip(code, temp=None, rain=0, name=None, chance=0.5):
    """A wry line to follow a weather answer, or None (about half the time, so it doesn't wear thin).
    code: the Open-Meteo weather code; temp: degrees C; rain: chance of rain in percent."""
    if random.random() >= chance:
        return None
    kind = ("storm" if code >= 95 else "snow" if code in (71, 73, 75, 77, 85, 86) else
            "rain" if code >= 51 or (rain or 0) >= 60 else "fog" if code in (45, 48) else
            "hot" if temp is not None and temp >= 33 else "cold" if temp is not None and temp <= 10 else
            "cloudy" if code == 3 else "clear" if code <= 1 else None)
    return fill(pick(WEATHER_QUIPS[kind]), name) if kind else None


def small_talk(text, name=None, me="Jarvis", names=(), real=None):
    """name: how to address you (see fill); real: who you are ("Chetak built me")."""
    low = text.lower().strip(" .!?,")
    for n in names:  # "thanks jarvis"
        low = re.sub(r"[\s,]+%s$" % re.escape(n), "", low)
    for pattern, replies in SMALL_TALK:
        m = re.fullmatch(pattern, low)
        if m:
            line = pick([r for r in replies if real or "{real}" not in r] or replies).replace("{real}", real or "you")
            return fill(line, name, me).format(*m.groups())
    return None


def daypart():
    hour = time.localtime().tm_hour
    return "morning" if 4 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening"


# The startup line, by part of the day: (from hour, part). Late night and the small hours never say "Good ...".
PARTS = ((0, "late_night"), (2, "small_hours"), (4, "early_morning"), (6, "morning"), (12, "afternoon"),
         (17, "evening"), (21, "night"))
_ANYTIME = ["Online and ready. What are we working on today?", "At your service, {name}.",
            "Systems check complete. Ready when you are."]
GREETINGS = {
    "early_morning": ["Up before the sun, {name}? Impressive. Or concerning.",
                      "Good morning, {name}. You're up early. The world is still asleep.",
                      "Early start, {name}. I'll have everything ready.",
                      "Good morning. It's still dark out, but I'm ready when you are.",
                      "The early bird, {name}. Systems are up and running.",
                      "Morning, {name}. Coffee first, I'd suggest.",
                      "You're up early, {name}. Couldn't sleep, or a big day ahead?",
                      "The birds aren't even up yet, {name}. You've beaten them.",
                      "Morning, {name}. Hours like these are the best for thinking.",
                      "You're up before the coffee, {name}. Bold."],
    "morning": ["Good morning, {name}. Systems are fully operational.",
    "Good morning, {name}. The day hasn't had a chance to go wrong yet. Let's keep it that way.",
    "Morning, {name}. Everything's warmed up, except possibly you.",
    "Good morning. Fresh day, clean slate, {name}. Where shall we begin?",
                "Good morning. All systems online. How may I be of assistance?",
                "Morning, {name}. Ready when you are.",
                "Good morning, {name}. I trust you slept well.",
                "Rise and shine, {name}. Everything's up and running.",
                "Good morning. A fresh day. What's first?",
                "Morning, {name}. What are we working on today?",
                "Good morning, {name}. Shall we get started?"],
    "afternoon": ["Good afternoon, {name}. Systems are fully operational.",
    "Afternoon, {name}. The post-lunch slump is real, but I'm immune.",
    "Good afternoon, {name}. Plenty of daylight left to get things done.",
    "Afternoon. Second half of the day, {name}. Your move.",
                  "Good afternoon. All systems online. How may I be of assistance?",
                  "Afternoon, {name}. What's next?",
                  "Good afternoon, {name}. I hope lunch was good.",
                  "Afternoon. Half the day done, {name}. Let's make the rest count.",
                  "Back to it, {name}? I'm ready when you are.",
                  "Good afternoon, {name}. How's the day treating you?"],
    "evening": ["Good evening, {name}. Systems are fully operational.",
    "Good evening, {name}. Shoes off, I'd suggest. Figuratively.",
    "Evening, {name}. Whatever the day threw at you, you made it.",
    "Evening. The lights are coming on outside, {name}. I'm already on.",
                "Good evening. All systems online. How may I be of assistance?",
                "Evening, {name}. How was your day?",
                "Good evening, {name}. Winding down, or just getting started?",
                "Evening. The day's work is nearly done, {name}.",
                "Evening, {name}. What can I do for you tonight?",
                "Good evening, {name}. I hope the day was kind to you."],
    "night": ["Good evening, {name}. Working late tonight?",
              "Still at it, {name}? I'm here.",
              "A late session, {name}. I'll keep things running.",
              "It's getting late, but I'm all yours, {name}.",
              "Night shift, {name}? Very well. Systems are online.",
              "Evening, {name}. Don't stay up too late.",
              "Online and ready, {name}. One more thing before bed?",
              "Night has fallen, {name}. The good ideas tend to turn up about now.",
              "Getting late, {name}. One more thing, then bed. I'll hold you to it.",
              "Quiet night, {name}. Just the hum of the fans and you."],
    "late_night": ["I see we're burning the midnight oil. Don't work too hard, {name}.",
                   "It's past midnight, {name}. Might I suggest some sleep? No? Very well, I'm here.",
                   "Midnight, {name}. The rest of the world has logged off. We haven't.",
                   "Ah, the hour when good ideas and bad decisions look exactly alike. Which is it tonight, {name}?",
                   "Still here, {name}? Then so am I.",
                   "Past midnight, {name}. I'm up if you are.",
                   "Midnight already. What's keeping you up, {name}?",
                   "Burning the candle at both ends again, {name}?",
                   "Technically it's tomorrow, {name}. I won't tell if you don't.",
                   "The house is asleep, {name}. I'm not. What do you need?",
                   "Late night, {name}. I'll keep my voice down."],
    "small_hours": ["It's the middle of the night, {name}. Are you all right?",
                    "This is an unusual hour, {name}, even by your standards. And your standards are generous.",
                    "Sleep is not optional, {name}, despite your best efforts. But I'm here.",
                    "Couldn't sleep, {name}? Neither could I. Well, I never do.",
                    "At this hour, {name}? Very well. I'm at your service.",
                    "The small hours, {name}. Let's be quick, and then bed.",
                    "I won't ask what you're doing up, {name}. How can I help?",
                    "Still awake, {name}? Your bed misses you.",
                    "Three in the morning, {name}. Either inspiration struck or sleep refused. Either way, I'm here.",
                    "The only ones awake now are you, me and the fridge, {name}.",
                    "I'll keep the answers short and the lights low, {name}."],
}
# Back after 3 hours or more (not after midnight: then the hour is what's worth mentioning). {away} = "about 5 hours".
WELCOME_BACK = ["Welcome home, {name}. You've been away {away}.",
                "{away} without you, {name}. I counted. Welcome back.",
                "Good {daypart}, {name}. Welcome back.",
                "Welcome back, {name}. I kept everything running while you were gone.",
                "Welcome back, {name}. You were gone {away}, and nothing caught fire.",
                "Good to have you back, {name}.",
                "And the place comes back to life. Welcome home, {name}.",
                "Welcome back, {name}. Missed me? I'll pretend you did.",
                "There you are, {name}. Missed you. Strictly professionally, of course.",
                "Welcome home, {name}.",
                "You're back, {name}. Everything's where you left it, including the tabs you swore you'd close.",
                "Welcome back, {name}. I held the fort. The fort is fine."]


def day_part(hour):
    return [p for h, p in PARTS if hour >= h][-1]


def _ago(seconds):
    if seconds < DAY:
        return "about %d hours" % round(seconds / 3600)
    days = round(seconds / DAY)
    return "a day" if days == 1 else "%d days" % days


def greeting(settings, memory, ups=()):
    """The startup line: by the part of the day and how long you were away, plus any heads-ups. A line said in this
    part of today isn't said again (restarts included) until all of its part's lines have been."""
    name, part = address(settings), day_part(time.localtime().tm_hour)
    away = time.time() - memory.data.get("last_seen", time.time())
    pool = GREETINGS[part] + list(settings.get("greeting") or [])  # (settings greeting: your own, any time)
    if part not in ("late_night", "small_hours"):
        pool += _ANYTIME + (WELCOME_BACK if away >= 3 * 3600 else [])
    today = "%s %s" % (time.strftime("%Y-%m-%d"), part)
    said = memory.pref("greeted") or {}
    said = said.get("said", []) if said.get("when") == today else []
    if not [g for g in pool if g not in said]:
        said = said[-1:]  # all said: start over, but not with the one just said
    line = random.choice([g for g in pool if g not in said] or pool)
    memory.set_pref("greeted", {"when": today, "said": said + [line]})
    line = line.replace("{daypart}", daypart()).replace("{away}", _ago(away))
    ups = list(ups)[:2]
    if len(ups) == 2:
        ups[1] = re.sub(r"^Heads up, (\w)", lambda m: "Also, " + m.group(1), ups[1])
    return " ".join([fill(line, name)] + ups)


# Each starts "Heads up, " (greeting() turns a second one into "Also, ..."). %s: the gigabytes left; battery: which
# battery ("" or "mouse ") and the percent.
HEADS_UP = {
    "disk": ["Heads up, your main disk is almost full, only %s left.",
    "Heads up, the main disk is nearly full, only %s left. Time to let go of a few downloads.",
    "Heads up, we're down to %s on the main disk. I'd hate to run out mid-thought.",
             "Heads up, disk space is running out. Only %s left on the main disk.",
             "Heads up, the main disk is nearly full, with %s free. Some tidying might be in order."],
    "battery": ["Heads up, your %sbattery is down to %s percent.",
    "Heads up, your %sbattery is at %s percent. It would appreciate a charger.",
    "Heads up, your %sbattery says %s percent and is getting nervous.",
                "Heads up, your %sbattery is at %s percent. Time for the charger, perhaps.",
                "Heads up, your %sbattery is getting low, %s percent left."],
    "low": ["Heads up, your %sbattery is running low.",
    "Heads up, your %sbattery is running on fumes. A charger, if there's one nearby.",
            "Heads up, your %sbattery is getting low. A charger might be in order."],
    "critical": ["Heads up, your %sbattery is almost dead.",
    "Heads up, your %sbattery is on its last breath. Plug it in now, or say goodbye.",
    "Heads up, your %sbattery is about to give out."],
}


def heads_ups(settings):
    """[(key, line, why)] for things worth mentioning: a nearly full disk, low batteries.
    The key's start (before ":") is its kind, which "don't warn me about that again" mutes."""
    cfg = settings.get("heads_up") or {}
    ups = []
    if cfg.get("disk", True):
        disk, limit = shutil.disk_usage("/"), cfg.get("disk_gb", 3)
        if disk.free < 0.05 * disk.total or disk.free < limit * 2 ** 30:
            gb = disk.free // 2 ** 30
            left = "less than a gigabyte" if gb < 1 else "1 gigabyte" if gb == 1 else "%d gigabytes" % gb
            ups.append(("disk", pick(HEADS_UP["disk"]) % left,
                        "Your main disk has %.1f GB free of %d GB, and I mention it under %s GB or 5 percent free "
                        "(heads_up disk_gb in settings)." % (disk.free / 2 ** 30, disk.total // 2 ** 30, limit)))
    if cfg.get("battery", True):
        for path in glob.glob("/sys/class/power_supply/*"):
            if _read(path, "type") != "Battery" or _read(path, "status") in ("Charging", "Full"):
                continue
            device = _read(path, "model_name").lower() if _read(path, "scope") == "Device" else ""
            capacity, level = _read(path, "capacity"), _read(path, "capacity_level")
            which = device + " " if device else ""
            if capacity.isdigit() and int(capacity) <= 15:
                ups.append(("battery:" + path + capacity, pick(HEADS_UP["battery"]) % (which, capacity),
                            "Your %sbattery is at %s percent and not charging; I mention it at "
                            "15 percent and below." % (which, capacity)))
            elif level in ("Low", "Critical"):
                ups.append(("battery:" + path + level, pick(HEADS_UP[level.lower()]) % which,
                    "Your %sbattery reports its level as %s and it isn't charging." % (which, level.lower())))
    return ups


def _read(path, name):
    try:
        with open(os.path.join(path, name)) as f:
            return f.read().strip()
    except OSError:
        return ""


def parse_reminder(text):
    """For "remind me in 20 minutes to call Raj": {"due", "what", "kind", "when"}.

    Returns "no time" for a reminder without a time, None if it isn't a reminder at all.
    """
    if not re.search(r"\bremind me\b", text, re.I):
        return None
    now = time.time()
    m = _IN.search(text)
    if m:
        amount = _NUMBERS.get(m.group(1).lower())
        if amount is None:
            try:
                amount = float(m.group(1))
            except ValueError:
                return "no time"
        due = now + amount * _UNITS[m.group(2)[0].lower()]
        when = "in " + m.group(0).split(None, 1)[1]
    else:
        m = _AT.search(text)
        if not m or int(m.group(1)) > 23:
            return "no time"
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "p" and hour < 12:
            hour += 12
        elif ampm == "a" and hour == 12:
            hour = 0
        t = time.localtime(now)
        base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        options = [base + (hour + extra) * 3600 + minute * 60 for extra in (0, 12, 24, 36)]
        if ampm or hour > 12:
            options = options[::2]  # "5 pm" is 5 pm, not 5 am.
        due = min(o for o in options if o > now)
        when = "at " + time.strftime("%-I:%M %p", time.localtime(due))
    what = (text[:m.start()] + " " + text[m.end():]).strip()
    what = re.sub(r"^.*?\bremind me\b\s*", "", what, flags=re.I)
    kind = "to"
    k = re.match(r"(to|that|about|of)\b\s*", what, re.I)
    if k:
        kind = {"of": "about"}.get(k.group(1).lower(), k.group(1).lower())
        what = what[k.end():]
    what = " ".join(_YOU.get(w.lower(), w) for w in what.strip(" ,.!?").split())
    if not what:
        return "no time"
    return {"due": due, "what": what, "kind": kind, "when": when}


# By the reminder's kind ("remind me to / that / about ..."); %s is what, {name} becomes ", sir" or nothing.
REMINDERS = {
    "to": ["Pardon the interruption{name}, it's time to %s.", "Per your request, here is your reminder to %s.",
           "I know you're busy{name}. However, it's time to %s.",
           "Sorry to interrupt{name}, but you wanted to %s about now.",
           "Here's the nudge you asked for{name}: time to %s.",
           "Past you asked me to tell present you{name}: it's time to %s.", "A gentle reminder{name}: it's time to %s.",
           "You asked me to remind you to %s.", "Time to %s{name}."],
    "that": ["Per your request, a reminder that %s.", "May I remind you that %s.",
             "You asked me to remind you that %s.", "Just a reminder{name}: %s.",
             "Don't forget{name}, %s.",
             "A little nudge{name}: %s."],
    "about": ["Here is your reminder, about %s.", "Your reminder, about %s.", "A reminder{name}, about %s.",
    "Tapping you on the shoulder{name}, about %s.",
    "As promised{name}, a reminder about %s.",
              "You wanted reminding about %s."],
    "late": ["My apologies, I was offline when this came due.",
    "This one slipped past while I was offline. My apologies. Better late than never.",
    "This came due while I was offline, I'm afraid."],
}


def reminder_line(reminder, name=None):
    name = pick(name) if name else name
    line = pick(REMINDERS.get(reminder["kind"], REMINDERS["to"])).replace("{name}", ", " + name if name else "")
    line %= reminder["what"]
    if time.time() - reminder["due"] > 120:
        line = pick(REMINDERS["late"]) + " " + line
    return line


class Memory:
    """Small JSON store; safe to use from the main loop and the background watcher."""

    def __init__(self, path=MEMORY):
        self.path = path
        self.lock = threading.Lock()
        self.data = load_json(path, {})
        if not isinstance(self.data, dict):
            keep_damaged(path)
            self.data = {}
        for key, empty in (("uses", {}), ("history", []), ("reminders", []), ("offered", {})):
            self.data.setdefault(key, empty)

    def _save(self):
        save_json(self.path, self.data)

    def seen(self):
        with self.lock:
            self.data["last_seen"] = time.time()
            self._save()

    def used(self, name, habit=False):
        """Count a use of command `name`; returns how many times it's been used."""
        with self.lock:
            uses = self.data["uses"][name] = self.data["uses"].get(name, 0) + 1
            if habit:
                self.data["history"] = (self.data["history"] + [[time.time(), name]])[-500:]
            self._save()
            return uses

    def pref(self, key, default=None):
        """A preference set by voice ("talk faster", "be brief"), kept across restarts."""
        return (self.data.get("prefs") or {}).get(key, default)

    def set_pref(self, key, value):
        with self.lock:
            prefs = self.data.setdefault("prefs", {})
            if value is None:
                prefs.pop(key, None)
            else:
                prefs[key] = value
            self._save()

    def preferences(self):
        """What you've told JARVIS about yourself ("I prefer battery life"): [{"topic", "value", "at", "private"}],
        in your own words. topic is set for "my favourite X is Y" (a new one replaces the old), else None."""
        return list(self.data.get("preferences") or [])

    def set_preferences(self, prefs):
        with self.lock:
            self.data["preferences"] = prefs
            self._save()

    def add_reminder(self, reminder):
        with self.lock:
            self.data["reminders"].append({k: reminder[k] for k in ("due", "what", "kind")})
            self._save()

    def reminders(self):
        return sorted(self.data["reminders"], key=lambda r: r["due"])

    def clear_reminders(self):
        with self.lock:
            count = len(self.data["reminders"])
            self.data["reminders"] = []
            self._save()
            return count

    def due_reminders(self):
        with self.lock:
            now = time.time()
            due = [r for r in self.data["reminders"] if r["due"] <= now]
            if due:
                self.data["reminders"] = [r for r in self.data["reminders"] if r["due"] > now]
                self._save()
            return due

    def habit_due(self):
        """A command you've run at this hour on 3+ of the last 14 days but not yet today, offered once a day."""
        with self.lock:
            now = time.time()
            if now - self.data.get("last_seen", 0) > 1800:
                return None  # You're not around; don't talk to an empty room.
            today, hour = time.strftime("%Y-%m-%d"), time.localtime().tm_hour
            days, done_today = {}, set()
            for ts, name in self.data["history"]:
                if now - ts > 14 * DAY:
                    continue
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                if day == today:
                    done_today.add(name)
                elif time.localtime(ts).tm_hour == hour:
                    days.setdefault(name, set()).add(day)
            for name, seen_on in days.items():
                if len(seen_on) >= 3 and name not in done_today and self.data["offered"].get(name) != today:
                    self.data["offered"][name] = today
                    self._save()
                    return name
            return None

    def habit_days(self, name):
        """The days in the last 2 weeks you ran `name` around this hour, oldest first (why it's offered)."""
        with self.lock:
            now, hour = time.time(), time.localtime().tm_hour
            return sorted({time.strftime("%Y-%m-%d", time.localtime(ts)) for ts, n in self.data["history"]
                           if n == name and now - ts <= 14 * DAY and time.localtime(ts).tm_hour == hour})
