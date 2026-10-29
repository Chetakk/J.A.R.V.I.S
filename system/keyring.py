"""Secrets in the GNOME Keyring (libsecret through gi, already on the desktop: nothing to install).

They're encrypted on disk with the login password and unlocked when you log in. JARVIS keeps no copy:
names and values live only in the keyring, never in notes.json or memory.json, and never go to Claude.
Each is an item labelled "JARVIS: <name>" in the default (login) collection; Seahorse ("Passwords and
Keys") shows them too.
"""
_schema = None


def _secret():
    """(Secret module, schema), or (None, None) when libsecret isn't there."""
    global _schema
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None, None
    if _schema is None:
        _schema = Secret.Schema.new("org.jarvis.Secret", Secret.SchemaFlags.NONE,
                                    {"name": Secret.SchemaAttributeType.STRING})
    return Secret, _schema


def available():
    Secret, _ = _secret()
    if not Secret:
        return False
    try:
        Secret.Service.get_sync(Secret.ServiceFlags.NONE, None)
        return True
    except Exception:  # no keyring daemon on the session bus
        return False


def store(name, value):
    Secret, schema = _secret()
    return bool(Secret) and Secret.password_store_sync(schema, {"name": name}, Secret.COLLECTION_DEFAULT,
                                                       "JARVIS: %s" % name, value, None)


def lookup(name):
    Secret, schema = _secret()
    return Secret.password_lookup_sync(schema, {"name": name}, None) if Secret else None


def delete(name):
    Secret, schema = _secret()
    return bool(Secret) and Secret.password_clear_sync(schema, {"name": name}, None)


def names():
    """The names of JARVIS's secrets, oldest first (values aren't read)."""
    Secret, schema = _secret()
    if not Secret:
        return []
    try:
        service = Secret.Service.get_sync(Secret.ServiceFlags.NONE, None)
        items = service.search_sync(schema, {}, Secret.SearchFlags.ALL, None)
    except Exception:
        return []
    return [i.get_attributes().get("name") for i in sorted(items, key=lambda i: i.get_created())
            if i.get_attributes().get("name")]
