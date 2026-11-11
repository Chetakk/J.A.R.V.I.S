"""The work Google account: Calendar, Gmail, Drive, Chat and Contacts (skills/google_*.py, skills/gmail.py,
skills/drive.py use it).

The OAuth client (an Internal app in the "jarvis" Cloud project) is ~/.config/jarvis/google_client.json,
outside the repo. The sign-in token lives only in the GNOME Keyring as "google token". Sign in with
"connect Google" or
    python3 -m system.google_account
which opens the browser (pick the work account, Allow) and then counts the next few calendar events.

Reading needs only the first sign-in's permissions; scheduling meetings, answering invites, sending a Chat
message and saving Gmail drafts need the newer ones (WRITES), granted by signing in again.
"""
import json
import os
import threading

from system import keyring

CLIENT = os.path.expanduser("~/.config/jarvis/google_client.json")
TOKEN = "google token"
BASE = "https://www.googleapis.com/auth/"
READS = ("calendar.readonly", "gmail.readonly", "drive.readonly", "chat.spaces.readonly", "chat.messages.readonly",
         "contacts.readonly")
WRITES = ("calendar.events", "gmail.compose", "chat.messages.create", "directory.readonly", "userinfo.profile")
SCOPES = [BASE + s for s in READS + WRITES]
TIMEOUT = 15  # seconds for one request: a hung connection mustn't stall the watch loop
_local = threading.local()  # httplib2 isn't thread-safe: one set of API clients per thread
_gen = [0]  # bumped on sign-in, so clients built with the old token are dropped


class GoogleError(Exception):
    """Something to say instead of the answer: not signed in, a permission missing, Google unreachable."""


def connected():
    return bool(keyring.lookup(TOKEN))


def creds():
    """Signed-in credentials (refreshed and re-saved when expired), or None before the first sign-in."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    saved = keyring.lookup(TOKEN)
    if not saved:
        return None
    c = Credentials.from_authorized_user_info(json.loads(saved))  # (its own "scopes": what was granted)
    if not c.valid and c.refresh_token:
        c.refresh(Request())
        keyring.store(TOKEN, _saved(c, json.loads(saved).get("scopes")))
    return c


def can(*scopes):
    """True if the sign-in granted these ("calendar.events")."""
    saved = keyring.lookup(TOKEN)
    granted = set(json.loads(saved).get("scopes") or ()) if saved else set()
    return all(BASE + s in granted for s in scopes)


def service(name, version):
    """A Google API client for this thread, e.g. service("calendar", "v3"), or None when not signed in."""
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build
    cache = _local.__dict__.setdefault("clients", {})
    key = (name, version, _gen[0])
    if key not in cache:
        c = creds()
        if not c:
            return None
        http = google_auth_httplib2.AuthorizedHttp(c, http=httplib2.Http(timeout=TIMEOUT))
        cache[key] = build(name, version, http=http, cache_discovery=False)
    return cache[key]


def api(name, version, *needs):
    """service(), or GoogleError when not signed in or a permission in `needs` wasn't granted."""
    if needs and connected() and not can(*needs):
        raise GoogleError("I don't have Google's permission for that yet. Say connect Google to sign in again, "
                          "and allow the new permissions")
    s = service(name, version)
    if s is None:
        raise GoogleError("Google isn't connected yet. Say connect Google to sign in")
    return s


def run(request):
    """request.execute(), with Google's failures as GoogleError (a plain reason to say)."""
    from googleapiclient.errors import HttpError
    try:
        return request.execute(num_retries=1)
    except HttpError as e:
        status = getattr(e.resp, "status", 0)
        if status in (401, 403) and "scope" in str(e).lower():
            raise GoogleError("I don't have Google's permission for that yet. Say connect Google to sign in again")
        if status == 404:
            raise GoogleError("Google couldn't find that")
        raise GoogleError("Google said no (%s)" % status)
    except GoogleError:
        raise
    except Exception as e:  # timeouts, no network, a revoked sign-in
        if "invalid_grant" in str(e):
            raise GoogleError("Google signed me out. Say connect Google to sign in again")
        raise GoogleError("I couldn't reach Google")


def sign_in():
    from google_auth_oauthlib.flow import InstalledAppFlow
    try:
        c = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES).run_local_server(
            port=0, prompt="consent", timeout_seconds=300)
    except AttributeError:  # (what the library raises when nobody finished signing in within the timeout)
        raise GoogleError("the sign-in timed out")
    if not keyring.store(TOKEN, _saved(c, c.granted_scopes or SCOPES)):
        raise GoogleError("Signed in, but I couldn't save the sign-in to the keyring")
    _gen[0] += 1
    return c


def _saved(c, granted):
    info = json.loads(c.to_json())
    info["scopes"] = sorted(granted or info.get("scopes") or ())  # what was granted, not what was asked for
    return json.dumps(info)


if __name__ == "__main__":
    import datetime
    sign_in()
    now = datetime.datetime.utcnow().isoformat() + "Z"
    events = run(service("calendar", "v3").events().list(
        calendarId="primary", timeMin=now, maxResults=3, singleEvents=True, orderBy="startTime"))
    print("Signed in. Next %d calendar events found. Permissions: %d of %d." % (
        len(events.get("items", [])), sum(can(s) for s in READS + WRITES), len(READS + WRITES)))
