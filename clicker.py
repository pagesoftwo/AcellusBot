"""clicker.py - Clicking/typing for AcellusAuto.

Two input modes (config "input_mode"):
  "foreground" (default): classic pyautogui - moves the real cursor,
      activates the Acellus window first.
  "background": Win32 messages via bginput - real cursor never moves,
      window never activated. Acellus can sit behind other windows.
      Window must NOT be minimized (minimized windows don't paint/receive
      hit-tested input). Minimizing the *VirtualBox host window* is fine -
      the guest desktop keeps rendering; only the guest window itself must
      stay restored (it may be covered).

Prefers vision coords (click_x/click_y 0-1000 from LLM) which handle
moving buttons + 3-vs-4 options. Falls back to CHOICE_POS fractions.
Only uses: pyautogui (+ stdlib). Background adds stdlib-only bginput.
"""

import random
import time

import pyautogui

import bginput

INPUT_MODE = "foreground"
SPEED = 1.0  # typing/motion speed multiplier (>1 = faster)


def set_input_mode(mode):
    """Set 'foreground' or 'background'. Returns the effective mode."""
    global INPUT_MODE
    m = (mode or "foreground").strip().lower()
    INPUT_MODE = "background" if m in ("background", "bg") else "foreground"
    return INPUT_MODE


def use_background():
    return INPUT_MODE == "background"


def set_speed(mult):
    """Set typing/motion speed multiplier. Returns effective value."""
    global SPEED
    try:
        SPEED = max(0.25, min(4.0, float(mult)))
    except Exception:
        SPEED = 1.0
    return SPEED

# Fallback fractional (x, y) inside the Acellus window for each radio button.
# Only used when the LLM returns no click_x/click_y.
CHOICE_POS = {
    "A": (0.08, 0.55),
    "B": (0.08, 0.63),
    "C": (0.08, 0.71),
    "D": (0.08, 0.79),
}


def set_choice_pos(letter, fx, fy):
    """Update fallback calibration at runtime. fx, fy in 0.0-1.0."""
    letter = letter.upper()
    if letter in CHOICE_POS:
        CHOICE_POS[letter] = (float(fx), float(fy))


def _activate(win):
    """Bring Acellus to front so the click lands in it, not our Debug UI.

    Skipped in background mode (activation would steal focus - the whole
    point of bg mode is leaving the user's session alone).
    """
    if use_background():
        return
    try:
        # pygetwindow activate can throw if already active; ignore.
        win.activate()
    except Exception:
        try:
            # Fallback: click title bar area? Just wait a tick.
            pass
        except Exception:
            pass
    time.sleep(0.2)


def _human_click(x, y, quick=True):
    # Quick mode: snappy but still human (was 0.3-0.9s, now ~0.12-0.28s)
    base = (0.12, 0.28) if quick else (0.3, 0.9)
    try:
        sp = max(0.25, min(4.0, float(SPEED)))
    except Exception:
        sp = 1.0
    duration = random.uniform(*base) / sp
    try:
        pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeOutQuad)
    except Exception:
        pyautogui.moveTo(x, y, duration=duration)
    pyautogui.sleep(random.uniform(0.02, 0.07))
    # mouseDown/up is more reliable than click() for Electron/web apps
    pyautogui.mouseDown(x, y)
    pyautogui.sleep(random.uniform(0.02, 0.05))
    pyautogui.mouseUp(x, y)
    return x, y


def _type_interval():
    base = random.uniform(0.03, 0.08)
    try:
        return max(0.005, base / max(0.25, float(SPEED)))
    except Exception:
        return base


def _frac(x1000, y1000):
    """Vision 0-1000 -> fractions with tiny human jitter."""
    fx = float(x1000) / 1000.0 + random.uniform(-0.003, 0.003)
    fy = float(y1000) / 1000.0 + random.uniform(-0.003, 0.003)
    return fx, fy


def _bg_click_frac(win, fx, fy):
    """Background click at fractions. Returns screen (x, y)."""
    return bginput.bg_click(win, fx, fy)


def get_click_point(win, letter):
    """Fallback: fractional CHOICE_POS to absolute screen coords."""
    letter = (letter or "").upper()
    if letter not in CHOICE_POS:
        raise ValueError(f"Invalid letter: {letter}")
    fx, fy = CHOICE_POS[letter]
    x = int(win.left + win.width * fx)
    y = int(win.top + win.height * fy)
    x += random.randint(-4, 4)
    y += random.randint(-4, 4)
    return x, y


def get_vision_point(win, x1000, y1000):
    """Convert 0-1000 normalized vision coords to absolute screen coords.

    NOTE: vision coords are relative to the *screenshot* (client area),
    while win.left/top include OS chrome. We re-read the live rect at
    click time, and screenshots from grab_window() start at win.left/top,
    so mapping is 1:1 except for title-bar offset (~30px). The LLM aims
    at the radio circle center, so small chrome offset is tolerable;
    jitter below keeps it human.
    """
    fx, fy = float(x1000) / 1000.0, float(y1000) / 1000.0
    # Re-read live geometry (window may have moved since capture)
    try:
        left, top, width, height = win.left, win.top, win.width, win.height
    except Exception:
        left, top, width, height = win.left, win.top, win.width, win.height
    x = int(left + width * fx) + random.randint(-3, 3)
    y = int(top + height * fy) + random.randint(-3, 3)
    return x, y


def click_choice(win, letter, x1000=None, y1000=None):
    """Click the answer. Returns (x, y, method).

    Args:
        win: pygetwindow.Window
        letter: 'A'|'B'|'C'|'D'
        x1000/y1000: vision coords 0-1000 or None -> fallback fractions.
    """
    _activate(win)
    # Refresh geometry after activate (front window may restore/maximize)
    try:
        _ = win.width  # touch to refresh cached rect
    except Exception:
        pass
    if x1000 is not None and y1000 is not None:
        if use_background():
            try:
                fx, fy = _frac(x1000, y1000)
                x, y = _bg_click_frac(win, fx, fy)
                return x, y, "bg-vision"
            except Exception:
                pass  # fall through to fractional fallback
        else:
            try:
                x, y = get_vision_point(win, x1000, y1000)
                _human_click(x, y, quick=True)
                return x, y, "vision"
            except Exception:
                pass  # fall through to fractional fallback
    if use_background():
        fx, fy = CHOICE_POS[(letter or "").upper()]
        x, y = _bg_click_frac(win, fx, fy)
        return x, y, "bg-fallback"
    x, y = get_click_point(win, letter)
    _human_click(x, y, quick=True)
    return x, y, "fallback"


def click_vision_point(win, x1000, y1000, activate=True):
    """Click any vision 0-1000 point (gear icon, menu item). Returns (x, y)."""
    if use_background():
        fx, fy = _frac(x1000, y1000)
        return _bg_click_frac(win, fx, fy)
    if activate:
        _activate(win)
    x, y = get_vision_point(win, x1000, y1000)
    _human_click(x, y, quick=True)
    return x, y


def click_textbox_and_type(win, text, x1000=None, y1000=None):
    """Click a green-box / bottom answer bar and type the answer. Returns (x, y).

    Acellus textbox questions (e.g. "x > [?]" + "Enter the number..."
    + bottom gray bar + orange Submit) take input in the BOTTOM bar, not
    the green [?] in the problem statement. So we:
      1) click the vision text_x/text_y (green box) if given, else the
         bottom-bar fallback, to focus,
      2) triple-click to select any existing content, Backspace to clear,
      3) typewrite the answer,
      4) press Tab/Enter-light defocus is done by the caller before Submit.
    Does NOT submit by itself - use click_submit() after.
    """
    _activate(win)
    try:
        _ = win.width  # refresh cached rect
    except Exception:
        pass
    if x1000 is None or y1000 is None:
        # bottom answer bar fallback: centered horizontally, ~92% down
        # (your screenshot: gray bar left, orange Submit right).
        x1000, y1000 = 380, 925
    if use_background():
        fx, fy = _frac(x1000, y1000)
        # focus field, triple-click to select old content, then type.
        # (Triple-click selects all in single-line web inputs, same as fg.)
        x, y = bginput.bg_multi_click(win, fx, fy, clicks=3)
        time.sleep(random.uniform(0.1, 0.2))
        s = str(text or "").strip()
        if s:
            try:
                bginput.bg_press_key(win, fx, fy, bginput.VK_BACK)
            except Exception:
                pass
            x, y = bginput.bg_type(win, fx, fy, s)
        time.sleep(random.uniform(0.2, 0.35))
        return x, y
    x, y = get_vision_point(win, x1000, y1000)
    _human_click(x, y, quick=True)
    time.sleep(random.uniform(0.15, 0.3))
    # triple-click to select existing [?] / old value
    try:
        pyautogui.click(x, y, clicks=3, interval=0.08)
    except Exception:
        pyautogui.click(x, y)
    time.sleep(random.uniform(0.1, 0.2))
    s = str(text or "").strip()
    if s:
        # clear selected text first (triple-click already selects; this covers partial)
        try:
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
        except Exception:
            pass
        try:
            pyautogui.press("backspace")
        except Exception:
            pass
        # typewrite char-by-char is more reliable than copy/paste in web apps
        try:
            pyautogui.typewrite(s, interval=_type_interval())
        except Exception:
            pyautogui.typewrite(s, interval=0.05)
    time.sleep(random.uniform(0.2, 0.35))
    return x, y


# Bottom-right orange Submit fallback (fraction inside Acellus window).
# Your screenshot: gray input bar spans bottom, Submit pill at far right.
SUBMIT_POS = (0.88, 0.945)

# Default bottom-bar fractions (fractional, not 0-1000).
BAR_POS = (0.38, 0.925)


def press_tab(win, x1000=None, y1000=None):
    """Move to the next input field (ordered-pair 2nd box). No cursor move in bg."""
    if use_background():
        fx, fy = BAR_POS if x1000 is None or y1000 is None else _frac(x1000, y1000)
        try:
            bginput.bg_press_key(win, fx, fy, bginput.VK_TAB)
        except Exception:
            pass
        time.sleep(random.uniform(0.15, 0.3))
        return
    try:
        pyautogui.press("tab")
    except Exception:
        pass
    time.sleep(random.uniform(0.15, 0.3))


def type_focused(win, text, x1000=None, y1000=None):
    """Type into whichever field has focus (after Tab). Returns (x, y).

    bg needs coords only to resolve the target widget - reuses the first
    box position since Tab keeps focus inside the same render widget.
    """
    s = str(text or "").strip()
    if use_background():
        fx, fy = BAR_POS if x1000 is None or y1000 is None else _frac(x1000, y1000)
        x, y = bginput.bg_type(win, fx, fy, s)
        time.sleep(random.uniform(0.2, 0.35))
        return x, y
    try:
        pyautogui.typewrite(s, interval=_type_interval())
    except Exception:
        pyautogui.typewrite(s, interval=0.05)
    time.sleep(random.uniform(0.2, 0.35))
    try:
        left, top, width, height = win.left, win.top, win.width, win.height
    except Exception:
        left, top, width, height = win.left, win.top, win.width, win.height
    fx = (x1000 / 1000.0) if x1000 is not None else BAR_POS[0]
    fy = (y1000 / 1000.0) if y1000 is not None else BAR_POS[1]
    return int(left + width * fx), int(top + height * fy)


def click_submit(win, x1000=None, y1000=None):
    """Click the orange Submit button after typing. Returns (x, y, method).

    Uses vision submit_x/submit_y when available, else SUBMIT_POS fallback.
    """
    _activate(win)
    try:
        _ = win.width
    except Exception:
        pass
    if x1000 is not None and y1000 is not None:
        if use_background():
            try:
                fx, fy = _frac(x1000, y1000)
                x, y = _bg_click_frac(win, fx, fy)
                return x, y, "bg-vision"
            except Exception:
                pass
        else:
            try:
                x, y = get_vision_point(win, x1000, y1000)
                _human_click(x, y, quick=True)
                return x, y, "vision"
            except Exception:
                pass
    fx, fy = SUBMIT_POS
    if use_background():
        x, y = _bg_click_frac(win, fx, fy)
        return x, y, "bg-fallback"
    fx, fy = SUBMIT_POS
    try:
        left, top, width, height = win.left, win.top, win.width, win.height
    except Exception:
        left, top, width, height = win.left, win.top, win.width, win.height
    x = int(left + width * fx) + random.randint(-3, 3)
    y = int(top + height * fy) + random.randint(-3, 3)
    _human_click(x, y, quick=True)
    return x, y, "fallback"


# Top-right "Skip" pill fallback (fraction inside Acellus window).
SKIP_POS = (0.93, 0.05)

# In-lesson back arrow "<" top-left fallback (goes home to dashboard).
BACK_POS = (0.03, 0.035)
def click_skip(win, x1000=None, y1000=None):
    """Click the Skip button (stuck/wrong-loop escape). Returns (x, y, method).

    Uses vision skip_x/skip_y when available, else SKIP_POS fallback.
    """
    _activate(win)
    try:
        _ = win.width
    except Exception:
        pass
    if x1000 is not None and y1000 is not None:
        if use_background():
            try:
                fx, fy = _frac(x1000, y1000)
                x, y = _bg_click_frac(win, fx, fy)
                return x, y, "bg-vision"
            except Exception:
                pass
        else:
            try:
                x, y = get_vision_point(win, x1000, y1000)
                _human_click(x, y, quick=True)
                return x, y, "vision"
            except Exception:
                pass
    fx, fy = SKIP_POS
    if use_background():
        x, y = _bg_click_frac(win, fx, fy)
        return x, y, "bg-fallback"
    try:
        left, top, width, height = win.left, win.top, win.width, win.height
    except Exception:
        left, top, width, height = win.left, win.top, win.width, win.height
    x = int(left + width * fx) + random.randint(-3, 3)
    y = int(top + height * fy) + random.randint(-3, 3)
    _human_click(x, y, quick=True)
    return x, y, "fallback"


def click_back(win, x1000=None, y1000=None):
    """Click the in-lesson back arrow (goes home to dashboard). Returns (x, y, method).

    Uses vision back_x/back_y when available, else BACK_POS fallback.
    """
    _activate(win)
    try:
        _ = win.width
    except Exception:
        pass
    if x1000 is not None and y1000 is not None:
        if use_background():
            try:
                fx, fy = _frac(x1000, y1000)
                x, y = _bg_click_frac(win, fx, fy)
                return x, y, "bg-vision"
            except Exception:
                pass
        else:
            try:
                x, y = get_vision_point(win, x1000, y1000)
                _human_click(x, y, quick=True)
                return x, y, "vision"
            except Exception:
                pass
    fx, fy = BACK_POS
    if use_background():
        x, y = _bg_click_frac(win, fx, fy)
        return x, y, "bg-fallback"
    try:
        left, top, width, height = win.left, win.top, win.width, win.height
    except Exception:
        left, top, width, height = win.left, win.top, win.width, win.height
    x = int(left + width * fx) + random.randint(-3, 3)
    y = int(top + height * fy) + random.randint(-3, 3)
    _human_click(x, y, quick=True)
    return x, y, "fallback"
