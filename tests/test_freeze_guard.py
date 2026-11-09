"""The deadlock that froze JARVIS can't happen: numpy's OpenBLAS is made single-threaded before anything loads numpy.
(tools/freeze_guard.sh, which restarts a frozen JARVIS, runs real processes, so it was tried by hand, not here.)"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STDLIB = {"faulthandler", "fcntl", "json", "os", "queue", "random", "re", "shlex", "signal", "subprocess", "sys",
          "threading", "time", "collections"}


def test_openblas_is_single_threaded_before_anything_can_load_numpy():
    with open(os.path.join(ROOT, "jarvis.py")) as f:
        body = ast.parse(f.read()).body
    sets = [i for i, node in enumerate(body) if "OPENBLAS_NUM_THREADS" in ast.dump(node)]
    assert sets, "jarvis.py must set OPENBLAS_NUM_THREADS"
    before = body[:sets[0]]
    names = {a.name.split(".")[0] for n in before if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in before if isinstance(n, ast.ImportFrom)}
    assert names <= STDLIB, "imported before OPENBLAS_NUM_THREADS is set: %s" % (names - STDLIB)
    assert [a.value for a in body[sets[0]].value.args] == ["OPENBLAS_NUM_THREADS", "1"]
