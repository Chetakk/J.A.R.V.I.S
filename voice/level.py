"""How loud the mic is right now, for the orb to bounce with your voice. No dependencies but numpy."""
import math
import time

import numpy as np

_now = [0.0, 0.0]  # (level 0..1, when)


def hear(raw):
    """Note one chunk of 16-bit mic audio."""
    if raw:
        rms = float(np.sqrt(np.mean(np.frombuffer(raw, dtype=np.int16).astype(np.float32) ** 2)))
        _now[:] = min(1.0, max(0.0, (math.log10(rms + 1) - 2.3) / 1.4)), time.time()  # ~200 quiet, ~5000 loud


def level():
    """The latest level, or 0 once the mic has gone quiet for a moment."""
    value, at = _now
    return value if time.time() - at < 0.3 else 0.0
