"""version.py - Single source of truth for AcellusBot releases.

Bump on every update: 5.0 -> 5.1 -> 5.2 ...
Shown in the GUI title bar + About tab, logged at startup, embedded in
the exe, and compared against update.json by the auto-updater.
"""

VERSION = "5.0"
APP_NAME = "AcellusBot"
BUILD_TAG = f"v{VERSION}-prod"


def version_tuple(v=None):
    """'5.2' -> (5, 2). Tolerant of 'v5.2', '5.2.1', whitespace."""
    import re as _re
    s = str(v if v is not None else VERSION).strip().lower()
    s = s[1:] if s.startswith("v") else s
    parts = []
    for p in s.split("."):
        m = _re.match(r"(\d+)", p)
        parts.append(int(m.group(1)) if m else 0)
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote, local=None):
    """True if remote version string is newer than local."""
    try:
        return version_tuple(remote) > version_tuple(local or VERSION)
    except Exception:
        return False
