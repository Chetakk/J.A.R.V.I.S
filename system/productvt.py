"""Your ProducTVT todos: the ones in the top-bar tray (ProductVT/tray/productvt-tray.py) and the web app.

Talks straight to the ProducTVT database with the connection string in ProductVT/.env, as the tray does, and
reads the tray's settings (~/.config/productvt-tray/config.json) for whose todos they are. Not set up on this
machine (no config, no .env, no psycopg2): `available()` is False and JARVIS keeps todos in the Day Tracker.
"""
import json
import os
import random
import string
import time

try:
    import psycopg2
except ImportError:  # python3-psycopg2 missing
    psycopg2 = None

CONFIG = "~/.config/productvt-tray/config.json"  # expanded when read, so a test can point HOME elsewhere


class Unavailable(Exception):
    """The database can't be reached (or the user isn't found): the message is safe to say."""


def _config():
    try:
        with open(os.path.expanduser(CONFIG)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _db_url(env_path):
    env = {}
    with open(env_path) as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                env[key.strip()] = value.strip().strip("\"'")
    url = env.get("DIRECT_URL") or env.get("DATABASE_URL")
    if not url:
        raise Unavailable("ProducTVT has no database address in its .env.")
    return url.split("?")[0]


def new_id():
    """A cuid-shaped id, like the ones Prisma makes."""
    t, stamp = int(time.time() * 1000), ""
    while t:
        t, r = divmod(t, 36)
        stamp = (string.digits + string.ascii_lowercase)[r] + stamp
    return "c" + stamp + "".join(random.choices(string.ascii_lowercase + string.digits, k=24 - len(stamp)))


def available():
    cfg = _config()
    return bool(psycopg2 and cfg.get("username") and cfg.get("env") and os.path.exists(cfg["env"]))


class Todos:
    """Add, list and tick off the user's ProducTVT todos. Ticking mirrors what the tray does: the project task
    a todo came from and its weekly-plan row go to Done as well."""

    def __init__(self):
        self.conn = None
        self.uid = None

    def _run(self, fn, *args):
        cfg = _config()
        for attempt in range(2):
            try:
                if self.conn is None or self.conn.closed:
                    self.conn = psycopg2.connect(_db_url(cfg["env"]), connect_timeout=10, application_name="jarvis")
                with self.conn, self.conn.cursor() as cur:
                    return fn(cur, *args)
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                self.conn = None  # dropped by the pooler: reconnect once
                if attempt:
                    raise Unavailable("I can't reach the ProducTVT database.") from e
            except OSError as e:
                raise Unavailable("I can't read ProducTVT's settings.") from e

    def _user(self, cur):
        if self.uid is None:
            name = _config()["username"]
            cur.execute('SELECT id FROM "User" WHERE active AND (username = lower(%s) OR lower(name) = lower(%s)) '
                        "ORDER BY (username = lower(%s)) DESC LIMIT 1", (name, name, name))
            row = cur.fetchone()
            if not row:
                raise Unavailable("I can't find your ProducTVT login.")
            self.uid = row[0]
        return self.uid

    def open(self):
        """Open todos, newest first, as {id, title}."""
        def q(cur):
            cur.execute('SELECT id, title FROM "Todo" WHERE "ownerId" = %s AND NOT done ORDER BY "createdAt" DESC',
                        (self._user(cur),))
            return [{"id": i, "title": t} for i, t in cur.fetchall()]
        return self._run(q)

    def add(self, title):
        def q(cur):
            uid = self._user(cur)
            cur.execute('SELECT COALESCE(MAX("order"), -1) + 1 FROM "Todo" WHERE "ownerId" = %s', (uid,))
            cur.execute('INSERT INTO "Todo" (id, "ownerId", title, "order") VALUES (%s, %s, %s, %s)',
                        (new_id(), uid, title, cur.fetchone()[0]))
        self._run(q)

    def set_done(self, tid):
        def q(cur):
            uid = self._user(cur)
            cur.execute('UPDATE "Todo" SET done = true WHERE id = %s AND "ownerId" = %s RETURNING "sourceTaskId"',
                        (tid, uid))
            row = cur.fetchone()
            if not row:
                return
            if row[0]:  # the project task it mirrors
                cur.execute("UPDATE \"ProjectTask\" SET status = 'DONE' WHERE id = %s", (row[0],))
            cur.execute('SELECT id FROM "PlanStatus" WHERE "ownerId" = %s AND name = \'Done\'', (uid,))
            status = cur.fetchone()
            if status:
                for col, src in (('"sourceTodoId"', tid), ('"sourceTaskId"', row[0])):
                    if src:
                        cur.execute('UPDATE "PlanItem" SET "statusId" = %s, "updatedAt" = now() WHERE ' + col +
                                    ' = %s AND "statusId" IS DISTINCT FROM %s', (status[0], src, status[0]))
        self._run(q)
