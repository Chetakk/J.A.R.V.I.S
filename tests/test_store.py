"""Your files JARVIS keeps (core/store.py and its users): a file that can't be read is kept aside before anything
is saved over it, and saves never leave half a file."""
import json
import os

import pytest

import conftest
from core import learned, store, tasks
from skills import layouts, where_was_i

DATA = conftest.DATA


def damaged_copies(path):
    folder, name = os.path.split(path)
    return [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith(name + ".damaged-")]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def test_load_and_save(jv):
    path = os.path.join(DATA, "x.json")
    assert store.load_json(path, []) == []
    store.save_json(path, {"a": 1})
    assert store.load_json(path, None) == {"a": 1} and not os.path.exists(path + ".tmp")


def test_a_damaged_file_is_kept_once(jv):
    path = os.path.join(DATA, "x.json")
    write(path, '{"cut off')
    assert store.load_json(path, {}) == {}
    assert store.load_json(path, {}) == {}
    copies = damaged_copies(path)
    assert len(copies) == 1 and open(copies[0]).read() == '{"cut off'


def test_memory_json_damaged_is_kept(jv):
    from core import persona
    path = os.path.join(DATA, "memory.json")
    write(path, '{"prefs": {"speed": 0.9}, "uses": {')
    m = persona.Memory(path)
    m.set_pref("answers", "brief")
    assert json.load(open(path))["prefs"]["answers"] == "brief"
    assert open(damaged_copies(path)[0]).read().startswith('{"prefs": {"speed": 0.9}')


def test_memory_json_that_isnt_an_object_is_kept(jv):
    from core import persona
    path = os.path.join(DATA, "memory.json")
    write(path, "[1, 2, 3]")
    persona.Memory(path).set_pref("answers", "brief")
    assert damaged_copies(path)


def test_learned_yaml_with_a_hand_editing_slip_is_kept(jv):
    path = os.path.join(DATA, "learned.yaml")
    slip = "wordings:\n  turn it down a notch: volume down\n  bad: [unclosed\n"
    write(path, slip)
    learned.remember("make it louder please", "volume up", path)
    assert learned.load(path)["wordings"] == {"make it louder please": "volume up"}
    assert open(damaged_copies(path)[0]).read() == slip  # your wordings are still there to copy back


def test_saved_layouts_damaged_are_kept(jv):
    write(layouts.SAVED, '{"reading": {"windows": [')
    jv.desktop.add("Docs - Google Chrome", "google-chrome Google-chrome")
    jv.do("save this layout as study")
    assert list(json.load(open(layouts.SAVED))) == ["study"]
    assert damaged_copies(layouts.SAVED)


def test_task_history_damaged_is_kept(jv):
    write(tasks.HISTORY_FILE, '[{"title": "old task"')
    assert tasks.past() == []
    assert damaged_copies(tasks.HISTORY_FILE)


def test_where_was_i_keeps_logging_after_damage(jv):
    write(where_was_i.WHERE_FILE, "[[1, ")
    jv.j.skills.where_was_i.log("open youtube")
    assert json.load(open(where_was_i.WHERE_FILE))[-1][1] == "open youtube"
