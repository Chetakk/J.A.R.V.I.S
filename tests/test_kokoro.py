"""Kokoro as JARVIS's voice: turning text into its phonemes, and Piper covering whenever it isn't up."""
import io
import json

from voice import kokoro, speech


def test_known_words_come_from_the_lexicon_and_punctuation_stays():
    lexicon = {"consider": "kənsˈɪdə", "it": "ɪt", "done": "dˈʌn", "sir": "sˌɜː"}
    assert kokoro.phonemes("Consider it done, sir.", lexicon, None) == ["kənsˈɪdə ɪt dˈʌn, sˌɜː."]


def test_unknown_words_go_to_espeak_in_kokoros_spelling():
    assert kokoro.phonemes("“Chay-tuck”", {}, lambda word: "tʃˈeɪtʌk") == ["ʧˈAtʌk"]


def test_a_very_long_sentence_is_split_into_pieces_the_model_can_take():
    pieces = kokoro.phonemes(" ".join(["it"] * 400) + ".", {"it": "ɪt"}, None)
    assert len(pieces) > 1 and all(len(p) <= 510 for p in pieces)
    assert sum(p.count("ɪt") for p in pieces) == 400


def _speaker(tmp_path, monkeypatch, up):
    voice = tmp_path / "voice.onnx"
    voice.write_text("")
    (tmp_path / "voice.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 22050}}))
    piper = tmp_path / "piper"
    piper.write_text("")
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    started = []

    class Proc:
        pid = 1

        def __init__(self, command, **_):
            started.append(command)
            self.stdin, self.stdout = io.BytesIO(), io.BytesIO()

        def poll(self):
            return None

    monkeypatch.setattr(speech.subprocess, "Popen", Proc)
    monkeypatch.setattr(speech.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(kokoro, "is_up", lambda: True)  # nothing tries to start a real Kokoro
    speaker = speech.Speaker({"piper_bin": str(piper), "voice": str(voice), "kokoro_voice": "bm_george",
                              "kokoro_dir": str(tmp_path), "length_scale": 1.25, "sentence_silence": 0.2})
    monkeypatch.setattr(kokoro, "is_up", lambda: up)
    speaker._kokoro_next_try = float("inf")
    return speaker, started


def test_kokoro_speaks_once_it_is_up(tmp_path, monkeypatch):
    speaker, started = _speaker(tmp_path, monkeypatch, True)
    proc = speaker._pipeline()
    assert started[0].startswith("socat ") and "24000" not in started[0] and "-r 24000" in started[1]
    assert json.loads(proc.stdin.getvalue()) == {"voice": "bm_george", "speed": 0.8, "silence": 0.2}


def test_piper_speaks_until_kokoro_is_up(tmp_path, monkeypatch):
    speaker, started = _speaker(tmp_path, monkeypatch, False)
    proc = speaker._pipeline()
    assert "--model" in started[0] and "-r 22050" in started[1] and proc.stdin.getvalue() == b""
