"""The phone skill (skills/phone.py): adb and GSConnect are faked, so nothing reaches a real phone."""
import pytest

from skills import phone

CONTACTS = ("Row: 0 display_name=Mom, data1=+91 98765 43210\n"
            "Row: 1 display_name=Rahul Sharma, data1=098111 22233\n")
UI = ('<?xml version="1.0"?><hierarchy><node text="" content-desc="" bounds="[0,0][1080,2400]">'
      '<node text="Hello" content-desc="" bounds="[0,100][500,200]"/>'
      '<node text="" content-desc="Send" bounds="[900,2200][1000,2300]"/></node></hierarchy>')
BATTERY = "((true, signature '(bsii)', [<(true, 'battery-good-symbolic', 64, 3000)>]),)\n"


@pytest.fixture
def ph(jv, monkeypatch):
    monkeypatch.setattr(phone.time, "sleep", lambda s: None)
    jv.shell.on(r"^adb -s \S+ shell content query", out=CONTACTS)
    jv.shell.on(r"^adb -s \S+ shell pm list packages", out="package:com.spotify.music\npackage:com.android.chrome\n")
    jv.shell.on(r"^adb -s \S+ shell cat /sdcard/jarvis-ui.xml", out=UI)
    jv.shell.on(r"^adb -s \S+ shell wm size", out="Physical size: 1080x2400\n")
    jv.shell.on(r"^adb -s \S+ shell dumpsys battery", out="  status: 2\n  level: 76\n")
    jv.shell.on(r"^adb devices$", out="List of devices attached\n192.168.1.7:5555\tdevice\n")
    jv.shell.on(r"^gjs .*--list-devices", out="abc123\n")
    jv.shell.on(r"Actions\.Describe battery", out=BATTERY)
    monkeypatch.setattr(phone.os.path, "exists", lambda p, _e=phone.os.path.exists: p == phone.GSCONNECTS[0] or _e(p))
    monkeypatch.setattr(phone.files, "recent_file", lambda: None)
    monkeypatch.setattr(phone.files, "front_files", lambda j: [])
    monkeypatch.setattr(phone, "_fresh_screenshot", lambda: [])
    return jv


def action(jv, name):
    """The GSConnect actions run on the phone abc123 by that name."""
    return jv.shell.ran(r"Device/abc123 -m org\.gtk\.Actions\.Activate %s " % name)


def test_open_app_by_package_name(ph):
    ph.do("open spotify on my phone")
    assert ph.shell.ran(r"monkey -p com\.spotify\.music ")
    assert ph.says("Opening spotify")


def test_call_a_contact(ph):
    ph.do("call mom")
    assert ph.shell.ran(r"android\.intent\.action\.CALL -d tel:\+919876543210")


def test_call_someone_unknown_is_handed_on(ph):
    ph.do("call the bank is done")
    assert not ph.shell.ran(r"action\.CALL")


def test_text_asks_then_sends_through_gsconnect(ph):
    ph.do("text rahul saying i'm running late", "yes")
    assert ph.questions == ["Text Rahul Sharma: i'm running late?"]
    sent = action(ph, "sendSms")
    assert len(sent) == 1 and "09811122233" in sent[0] and "running late" in sent[0]


def test_text_not_sent_on_no(ph):
    ph.do("text rahul saying i'm running late", "no")
    assert not action(ph, "sendSms") and ph.says("not sent")


def test_whatsapp_opens_chat_and_taps_send(ph):
    ph.do("whatsapp rahul saying ok see you", "yes")
    assert ph.shell.ran(r"phone=919811122233&text=ok%20see%20you")
    assert ph.shell.ran(r"input tap 950 2250")


def test_tap_finds_words_on_screen(ph):
    ph.do("tap hello on my phone")
    assert ph.shell.ran(r"input tap 250 150") and ph.says("Tapped Hello")


def test_tap_missing_word_says_so(ph):
    ph.do("tap goodbye on my phone")
    assert not ph.shell.ran(r"input tap") and ph.says("can't see goodbye")


def test_battery_from_gsconnect(ph):
    ph.do("phone battery")
    assert ph.says("64 percent") and not ph.says("charging") and not ph.shell.ran(r"dumpsys battery")


def test_battery_from_adb_without_gsconnect(ph):
    ph.shell.on(r"^gjs .*--list-devices", out="")
    ph.do("phone battery")
    assert ph.says("76 percent and charging")


def test_keys(ph):
    ph.do("pause my phone")
    ph.do("lock my phone")
    assert ph.shell.ran(r"keyevent 85") and ph.shell.ran(r"keyevent 223")


def test_type_on_the_phone(ph):
    ph.do("type hello there on my phone")
    assert ph.shell.ran(r"input text hello%sthere")


def test_no_phone_and_no_saved_address_is_said(ph, monkeypatch):
    monkeypatch.setattr(phone, "_saved_ip", lambda: None)
    ph.shell.on(r"^adb devices$", out="List of devices attached\n\n")
    ph.do("lock my phone")
    assert ph.says("isn't on adb", "plug it in")


def test_usb_phone_is_moved_to_wifi(ph, monkeypatch):
    saved = []
    monkeypatch.setattr(phone, "_save_ip", saved.append)
    ph.shell.on(r"^adb devices$", out="List of devices attached\n42121db5\tdevice\n")
    ph.shell.on(r"^adb -s 42121db5 shell ip ", out="    inet 192.168.1.9/24 brd 192.168.1.255 scope global wlan0\n")
    ph.shell.on(r"^adb connect 192\.168\.1\.9:5555$", out="connected to 192.168.1.9:5555\n")
    ph.do("lock my phone")
    assert ph.shell.ran(r"^adb -s 42121db5 tcpip 5555$") and saved == ["192.168.1.9"]
    assert ph.shell.ran(r"^adb -s 192\.168\.1\.9:5555 shell input keyevent 223")


def test_dropped_wifi_link_reconnects_to_the_last_address(ph, monkeypatch):
    monkeypatch.setattr(phone, "_saved_ip", lambda: "192.168.1.7")
    ph.shell.on(r"^adb devices$", out="List of devices attached\n\n")
    ph.shell.on(r"^adb connect 192\.168\.1\.7:5555$", out="connected to 192.168.1.7:5555\n")
    ph.do("lock my phone")
    assert ph.shell.ran(r"^adb -s 192\.168\.1\.7:5555 shell input keyevent 223")


def test_ring_through_gsconnect(ph):
    ph.do("find my phone")
    assert action(ph, "ring") and ph.says("Ringing")


def test_gsconnect_not_seeing_the_phone_is_said(ph):
    ph.shell.on(r"^gjs .*--list-devices", out="")
    ph.do("find my phone")
    assert ph.says("GSConnect can't see it")


def test_send_this_page_from_chrome(ph, monkeypatch):
    ph.desktop.add("Docs - Google Chrome", "google-chrome")
    monkeypatch.setattr(phone.chrome_link, "page", lambda: {"url": "https://example.com/a", "title": "A"})
    ph.do("send this to my phone")
    assert "https://example.com/a" in action(ph, "shareUri")[0]


def test_send_what_youre_looking_at(ph, monkeypatch):
    monkeypatch.setattr(phone.files, "front_files", lambda j: ["/home/me/a.jpg", "/home/me/b.mp4"])
    ph.do("send these photos to my phone")
    sent = action(ph, "shareFile")
    assert len(sent) == 2 and "a.jpg" in sent[0] and "b.mp4" in sent[1] and "false" in sent[0]
    assert ph.says("Sending 2 files")


def test_a_screenshot_just_taken_is_it(ph, monkeypatch):
    monkeypatch.setattr(phone, "_fresh_screenshot", lambda: ["/home/me/Pictures/Screenshot from now.png"])
    monkeypatch.setattr(phone.files, "front_files", lambda j: ["/home/me/notes.txt"])
    ph.do("send it to my phone")
    sent = action(ph, "shareFile")
    assert len(sent) == 1 and "Screenshot from now.png" in sent[0]


def test_send_a_screenshot_takes_one(ph):
    ph.do("send a screenshot to my phone")
    shot = ph.shell.ran(r"^gnome-screenshot -f ")
    assert shot and "Pictures/Screenshot from " in shot[0] and "Screenshot from" in action(ph, "shareFile")[0]


def test_open_this_on_the_phone(ph, monkeypatch):
    monkeypatch.setattr(phone.files, "front_files", lambda j: ["/home/me/talk.pdf"])
    ph.do("open this on my phone")
    sent = action(ph, "shareFile")
    assert len(sent) == 1 and "talk.pdf" in sent[0] and "true" in sent[0] and ph.says("Opening talk.pdf")


def test_send_a_file_by_name(ph, monkeypatch):
    monkeypatch.setattr(phone.files, "_find_files", lambda q: ["/home/me/Documents/plan.pdf"])
    ph.do("send plan.pdf to my phone")
    assert "/home/me/Documents/plan.pdf" in action(ph, "shareFile")[0]


def test_a_note_on_the_phone(ph):
    ph.do("send a note to my phone saying buy milk")
    sent = action(ph, "sendNotification")
    assert len(sent) == 1 and "buy milk" in sent[0] and ph.says("on your phone")


def test_send_it_when_jarvis_just_found_a_file(ph, monkeypatch):
    ph.desktop.add("Terminal", "gnome-terminal-server")
    monkeypatch.setattr(phone.files, "recent_file", lambda: ["/home/me/plan.pdf"])
    ph.do("send it to my phone")
    assert "/home/me/plan.pdf" in action(ph, "shareFile")[0] and ph.says("plan.pdf")


def test_send_this_with_nothing_else_sends_the_clipboard(ph):
    ph.do("send this to my phone")
    assert action(ph, "clipboardPush")


def test_send_words_to_the_phone(ph):
    ph.do("send buy milk and eggs to my phone")
    assert "buy milk and eggs" in action(ph, "shareText")[0]


def test_pull_the_phones_clipboard(ph):
    ph.do("paste from my phone")
    assert action(ph, "clipboardPull")


def test_gvariant_strings_are_escaped():
    assert phone._gstr("it's a \\ test") == "'it\\'s a \\\\ test'"


def test_international():
    assert phone._international("+919876543210") == "919876543210"
    assert phone._international("09811122233") == "919811122233"
    assert phone._international("9811122233") == "919811122233"


# --- the phone's notifications (GSConnect, read by system/notifications.py) ---
WHATSAPP = ("WhatsApp", "Mom", "are you coming?",
            {"key": "0|com.whatsapp|7|null|10123", "package": "com.whatsapp", "reply": "uuid-7"})
SMS = ("Messages", "Rahul Sharma", "landed", {"key": "0|com.google.android.apps.messaging|3|null|10111",
                                              "package": "com.google.android.apps.messaging", "reply": None})


def heads_ups(jv):
    out = []
    while not jv.j.inbox.empty():
        kind, line = jv.j.inbox.get_nowait()
        if kind == "say":
            out.append(line)
    return out


@pytest.fixture
def heard(ph):
    ph.j.settings["quiet_hours"] = {"enabled": False}
    while not ph.j.inbox.empty():
        ph.j.inbox.get_nowait()
    return ph.j.skills.notifications.watcher


def test_a_phone_message_is_said_as_it_comes(ph, heard):
    heard.add(*WHATSAPP)
    assert heads_ups(ph) == ["WhatsApp from Mom: are you coming?"]


def test_a_call_is_said(ph, heard):
    heard.add("Phone", "Mom", "Incoming call", {"call": "ringing"})
    assert heads_ups(ph) == ["Mom is calling."]


def test_app_notifications_that_cant_be_answered_stay_quiet(ph, heard):
    heard.add("System UI", "", "USB debugging connected", {"key": "0|com.android.systemui|1|null|1",
                                                          "package": "com.android.systemui", "reply": None})
    heard.add("Slack", "general", "new post")  # not the phone's
    assert heads_ups(ph) == []


def test_reply_answers_the_phone_message(ph, heard):
    heard.add(*WHATSAPP)
    ph.do("reply saying on my way", "yes")
    assert ph.questions == ['Reply to Mom on WhatsApp: "on my way"?']
    sent = action(ph, "replyNotification")
    assert len(sent) == 1 and "uuid-7" in sent[0] and "on my way" in sent[0]


def test_reply_to_a_text_texts_back(ph, heard):
    heard.add(*SMS)
    ph.do("reply saying welcome home", "yes")
    sent = action(ph, "sendSms")
    assert len(sent) == 1 and "09811122233" in sent[0] and "welcome home" in sent[0]


def test_reply_with_no_phone_message_stays_google_chats(ph, heard):
    ph.do("reply saying hi")
    assert ph.says("Google isn't connected") and not action(ph, "replyNotification")


def test_dismiss_clears_it_on_the_phone(ph, heard):
    heard.add(*WHATSAPP)
    ph.do("dismiss that notification")
    assert "0|com.whatsapp|7|null|10123" in action(ph, "closeNotification")[0]


def test_mute_the_call(ph):
    ph.do("mute the call")
    assert action(ph, "muteCall") and ph.says("Muted")


def test_read_texts(ph):
    ph.shell.on(r"content query --uri content://sms/inbox",
                out="Row: 0 address=+919811122233, date=2, body=landed safely\nRow: 1 address=12345, date=1, body=OTP 99\n")
    ph.do("read my texts")
    assert ph.says("2 recent texts", "Rahul Sharma: landed safely")
    assert "12345: OTP 99" in ph.card_text()


def test_texts_from_someone_asks_the_phone_for_theirs(ph):
    ph.shell.on(r"content query --uri content://sms/inbox", out="Row: 0 address=+919876543210, date=2, body=call me\n")
    ph.do("any texts from mom")
    assert ph.shell.ran(r"address LIKE") and ph.shell.ran(r"9876543210") and ph.says("Mom: call me")


def test_latest_photo_is_pulled_and_shown(ph, monkeypatch):
    ph.shell.on(r"shell 'ls -t /sdcard/DCIM/Camera \| head -1'", out="IMG_1.jpg\n")
    ph.do("get my latest photo from my phone")
    assert ph.shell.ran(r"pull /sdcard/DCIM/Camera/IMG_1\.jpg .*Pictures/Phone/IMG_1\.jpg")
    assert ph.cards[-1]["image"].endswith("Pictures/Phone/IMG_1.jpg")


def test_reading_gsconnect_ids():
    from system.notifications import from_phone
    assert from_phone("d|0|com.whatsapp|1|null|5|u1", "WhatsApp", "Mom: hi") == (
        "WhatsApp", "Mom", "hi", {"key": "0|com.whatsapp|1|null|5", "package": "com.whatsapp", "reply": "u1"})
    assert from_phone("d|ringing|Mom", "Mom", "Incoming call") == ("Phone", "Mom", "Incoming call", {"call": "ringing"})
    assert from_phone("d|0|com.google.android.apps.messaging|3|null|1", "Rahul", "ok")[:3] == ("Messages", "Rahul", "ok")


# --- a file into WhatsApp or the phone's share menu ---
INDEXED = "Row: 0 _id=1000334835, mime_type=image/png\n"


@pytest.fixture
def shot(ph, monkeypatch):
    """A screenshot just taken, which the phone has indexed once it's sent; the phone awake and unlocked."""
    monkeypatch.setattr(phone, "_fresh_screenshot", lambda: ["/home/me/Pictures/shot.png"])
    monkeypatch.setattr(phone.os.path, "getsize", lambda p: 1200000)
    sent = []
    ph.shell.on(r"Actions\.Activate shareFile", out=lambda line: sent.append(line) or "")
    ph.shell.on(r"shell content query --uri content://media/external/file ", out=lambda line: INDEXED if sent else "")
    ph.shell.on(r"shell dumpsys window", out="mDreamingLockscreen=false\n")
    return ph


def test_share_opens_the_phones_share_menu_with_it(shot):
    shot.do("share it on my phone")
    assert "shot.png" in action(shot, "shareFile")[0]
    opened = shot.shell.ran(r"am start -a android\.intent\.action\.SEND -t image/png --eu android\.intent\.extra\.STREAM "
                            r"content://media/external/images/media/1000334835 --grant-read-uri-permission$")
    assert opened and shot.says("share menu")


def test_whatsapp_a_file_asks_then_sends_it_to_them(shot):
    shot.do("send it to rahul on whatsapp", "yes")
    assert shot.questions == ["Send shot.png to Rahul Sharma on WhatsApp?"]
    assert shot.shell.ran(r"content://media/external/images/media/1000334835 .*-p com\.whatsapp --es jid "
                          r"919811122233@s\.whatsapp\.net")
    assert shot.shell.ran(r"input tap 950 2250") and shot.says("Sent to Rahul Sharma on WhatsApp")


def test_whatsapp_a_file_not_sent_on_no(shot):
    shot.do("whatsapp it to rahul", "no")
    assert not action(shot, "shareFile") and not shot.shell.ran(r"com\.whatsapp")


def test_a_file_already_on_the_phone_isnt_sent_again(shot):
    shot.shell.on(r"shell content query --uri content://media/external/file ", out=INDEXED)
    shot.do("share it on my phone")
    assert not action(shot, "shareFile") and shot.shell.ran(r"_display_name LIKE .*shot%\.png.* AND _size=1200000")
    assert shot.shell.ran(r"action\.SEND")


def test_a_locked_phone_is_asked_to_unlock(shot):
    shot.shell.on(r"shell dumpsys window", out="mDreamingLockscreen=true\n")
    shot.do("share it on my phone")
    assert shot.says("locked", "Unlock it") and not shot.shell.ran(r"action\.SEND")


def test_whatsapp_words_with_to_in_them_stay_a_text(ph):
    ph.do("whatsapp rahul saying i'm going to the gym", "no")
    assert ph.questions == ["WhatsApp Rahul Sharma: i'm going to the gym?"]


def test_copy_my_clipboard_to_the_phone(ph):
    ph.do("copy my clipboard to phone")
    assert action(ph, "clipboardPush") and ph.says("Your phone has your clipboard")


def test_copy_the_phones_clipboard_here(ph):
    ph.do("copy phone clipboard")
    assert action(ph, "clipboardPull")


def test_whats_on_the_phones_clipboard_is_read_out(ph, monkeypatch):
    monkeypatch.setattr(phone, "clipboard", lambda: "flight AI 302 at 6:40")
    ph.do("what's on my phone's clipboard")
    assert action(ph, "clipboardPull") and ph.says("Your phone's clipboard says: flight AI 302 at 6:40")
