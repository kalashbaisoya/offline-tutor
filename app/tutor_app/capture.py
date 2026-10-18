"""Full-screen capture of the monitor under the mouse. Only called on activation (circle click / hotkey)."""

import mss
from PIL import Image


def grab_monitor_at(x, y):
    """Screenshot (PIL RGB, physical pixels) of the monitor containing physical point (x, y), plus its rect."""
    with getattr(mss, "MSS", mss.mss)() as sct:  # mss >= 10.2 renamed the class
        mons = sct.monitors[1:]
        mon = next((m for m in mons if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]),
                   mons[0])
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), mon
