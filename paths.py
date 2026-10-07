"""paths.py - Freeze-safe locations for AcellusAuto (dev + PyInstaller exe).

Dev (python main.py): everything lives next to the source files.
Frozen (AcellusAuto.exe): read-only bundled files come from sys._MEIPASS,
writable files (config.json, .env, logs, screenshots) live next to the exe.
Stdlib only.
"""

import os
import sys


def frozen():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """Writable dir: exe folder when frozen, source dir in dev."""
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource(name):
    """Bundled read-only file (e.g. vision_prompt.txt)."""
    base = getattr(sys, "_MEIPASS", None) if frozen() else None
    return os.path.join(base or app_dir(), name)


def config_path():
    return os.path.join(app_dir(), "config.json")


def dotenv_path():
    return os.path.join(app_dir(), ".env")


def web_dir():
    """Static dashboard site. Priority: web/ next to the exe (editable),
    bundled web/ inside the frozen exe, ../web next to the project (dev).
    Returns None if nowhere (server uses embedded fallback page)."""
    cands = [os.path.join(app_dir(), "web")]
    try:
        meipass = getattr(sys, "_MEIPASS", None) if frozen() else None
        if meipass:
            cands.append(os.path.join(meipass, "web"))
    except Exception:
        pass
    cands.append(os.path.join(os.path.dirname(app_dir()), "web"))
    for cand in cands:
        try:
            if os.path.isdir(cand):
                return cand
        except Exception:
            continue
    return None
