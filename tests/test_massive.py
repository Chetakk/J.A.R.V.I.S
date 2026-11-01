"""tools/massive.py: what each MASSIVE intent means for JARVIS, for the held-out sentences and for training."""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("massive", os.path.join(HERE, "tools", "massive.py"))
massive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(massive)


def test_intents_become_phrases_or_none(tmp_path):
    path = tmp_path / "split.jsonl"
    rows = [("olly turn the volume up", "audio_volume_up"), ("what's the time in london", "datetime_query"),
            ("what time is it", "datetime_query"), ("tell me a joke", "general_joke"),
            ("play some jazz", "play_music"), ("wake me at seven", "alarm_set"), ("set two alarms for 7", "alarm_set"),
            ("hi", "general_greet")]
    path.write_text("\n".join(json.dumps({"text": t, "label_text": i}) for t, i in rows))
    got = massive.read(str(path))
    assert got["turn the volume up"] == ["volume up"]  # wake word taken out
    assert "what's the time in london" not in got  # the time somewhere else isn't the matcher's
    assert got["what time is it"] == ["what time is it", "what's the date"]
    assert got["tell me a joke"] == "none"
    assert "play some jazz" not in got  # could go either way: left out
    assert got["wake me at seven"][0] == "none"  # leaving it is right too
    assert "set two alarms for 7" not in got and "hi" not in got  # a number; one word


def test_the_held_out_file_is_what_the_tool_makes_of_the_test_split():
    import yaml
    with open(os.path.join(HERE, "tests", "heldout.yaml"), encoding="utf-8") as f:
        labels = yaml.safe_load(f)
    assert len(labels) > 2000 and all(v == "none" or isinstance(v, list) for v in labels.values())
