"""The work Google account, read-only: Calendar, Gmail, Drive, Chat and Contacts.

The OAuth client (an Internal app in the "jarvis" Cloud project) is ~/.config/jarvis/google_client.json,
outside the repo. The sign-in token lives only in the GNOME Keyring as "google token". Sign in once with
    python3 -m system.google_account
which opens the browser (pick the work account, Allow) and then lists the next few calendar events.
"""
import json
import os

from system import keyring

CLIENT = os.path.expanduser("~/.config/jarvis/google_client.json")
TOKEN = "google token"
SCOPES = ["https://www.googleapis.com/auth/" + s for s in (
    "calendar.readonly", "gmail.readonly", "drive.readonly",
    "chat.spaces.readonly", "chat.messages.readonly", "contacts.readonly")]


def creds():
    """Signed-in credentials (refreshed and re-saved when expired), or None before the first sign-in."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    saved = keyring.lookup(TOKEN)
    if not saved:
        return None
    c = Credentials.from_authorized_user_info(json.loads(saved), SCOPES)
    if not c.valid and c.refresh_token:
        c.refresh(Request())
        keyring.store(TOKEN, c.to_json())
    return c


def service(name, version):
    """A Google API client, e.g. service("calendar", "v3"), or None when not signed in."""
    from googleapiclient.discovery import build
    c = creds()
    return c and build(name, version, credentials=c, cache_discovery=False)


def sign_in():
    from google_auth_oauthlib.flow import InstalledAppFlow
    c = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES).run_local_server(port=0, prompt="consent")
    if not keyring.store(TOKEN, c.to_json()):
        raise SystemExit("Signed in, but couldn't save the token to the GNOME Keyring.")
    return c


if __name__ == "__main__":
    import datetime
    sign_in()
    now = datetime.datetime.utcnow().isoformat() + "Z"
    events = service("calendar", "v3").events().list(
        calendarId="primary", timeMin=now, maxResults=3, singleEvents=True, orderBy="startTime").execute()
    print("Signed in. Next %d calendar events found." % len(events.get("items", [])))
