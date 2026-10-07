"""bginput.py - Background window input for AcellusAuto (no real mouse).

Sends clicks/keystrokes as Win32 messages (PostMessage) directly to the
target window's child control, so the real cursor never moves, the window
never needs focus, and it can sit behind other windows (or on another
virtual desktop) while you use the PC.

Requirements/limits:
  - Target window must exist and NOT be minimized (minimized windows don't
    paint and don't process hit-tested input). Covered/occluded is fine.
  - Works best when Acellus runs in Chrome/Edge/Electron: messages are
    routed to the exact child control under the point (generic hit-test,
    no browser-specific hacks).
  - Only stdlib (ctypes). Windows only.
"""

import ctypes
import random
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_GETTEXT = 0x000D
MK_LBUTTON = 0x0001
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
CWP_SKIPINVISIBLE = 0x0001

MAPVK_VK_TO_VSC = 0


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _setup_prototypes():
    try:
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, POINT,
                                                 wintypes.UINT]
        user32.ChildWindowFromPointEx.restype = wintypes.HWND
        user32.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND,
                                           ctypes.POINTER(POINT), wintypes.UINT]
        user32.MapWindowPoints.restype = ctypes.c_int
        user32.ClientToScreen.argtypes = [wintypes.HWND,
                                          ctypes.POINTER(POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        user32.MapVirtualKeyW.restype = wintypes.UINT
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetFocus.restype = wintypes.HWND
        user32.SetFocus.argtypes = [wintypes.HWND]
        user32.SetFocus.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                    ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                             wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
    except Exception:
        pass


_setup_prototypes()

_RENDER_CLASSES = (b"Chrome_RenderWidgetHostHWND",)


def hwnd_of(win):
    """Extract the Win32 HWND from a pygetwindow Window (or accept raw int)."""
    if isinstance(win, int):
        return win
    for attr in ("_hWnd", "_handle", "handle", "_HWND"):
        try:
            h = getattr(win, attr, None)
            if isinstance(h, int) and h:
                return h
        except Exception:
            continue
    try:
        g = getattr(win, "getHWND", None)
        if callable(g):
            h = g()
            if isinstance(h, int) and h:
                return h
    except Exception:
        pass
    raise RuntimeError("Could not resolve HWND from window object")


def is_minimized_hwnd(hwnd):
    try:
        return bool(user32.IsIconic(hwnd))
    except Exception:
        return False


def window_rect(hwnd):
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        raise RuntimeError("GetWindowRect failed")
    return (r.left, r.top, r.right, r.bottom)


def client_origin(hwnd):
    """Screen coords of the client area origin (i.e. past title bar/borders)."""
    p = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(p)):
        raise RuntimeError("ClientToScreen failed")
    return (p.x, p.y)


def _find_render_widget(hwnd):
    """Fallback: first Chrome_RenderWidgetHostHWND child (recursive)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(h, _):
        try:
            buf = ctypes.create_string_buffer(128)
            user32.GetClassNameA(h, buf, 128)
            if buf.value in _RENDER_CLASSES:
                found.append(h)
                return False  # stop
        except Exception:
            pass
        return True

    try:
        user32.EnumChildWindows(hwnd, _cb, 0)
    except Exception:
        pass
    return found[0] if found else None


def resolve_target(win, fx, fy):
    """Map window-fraction (fx, fy) to (child_hwnd, cx, cy).

    (cx, cy) are client-area coords relative to child_hwnd, ready for
    lParam packing. Hit-tests the real child under the point so Chrome/
    Edge/Electron input lands in the page, not the top-level frame.
    """
    hwnd = hwnd_of(win)
    if is_minimized_hwnd(hwnd):
        raise RuntimeError("Target window is minimized - restore it first "
                           "(background input needs a non-minimized window)")
    left, top, right, bottom = window_rect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError("Target window has zero size")
    sx = int(left + w * float(fx))
    sy = int(top + h * float(fy))
    ox, oy = client_origin(hwnd)
    cx, cy = sx - ox, sy - oy
    # Generic hit-test: which child is actually under this point?
    try:
        child = user32.ChildWindowFromPointEx(hwnd, POINT(cx, cy),
                                              CWP_SKIPINVISIBLE)
    except Exception:
        child = None
    if child and child != hwnd:
        p = POINT(cx, cy)
        try:
            user32.MapWindowPoints(hwnd, child, ctypes.byref(p), 1)
            return child, p.x, p.y
        except Exception:
            return child, cx, cy
    rw = _find_render_widget(hwnd)
    if rw:
        p = POINT(cx, cy)
        try:
            user32.MapWindowPoints(hwnd, rw, ctypes.byref(p), 1)
            return rw, p.x, p.y
        except Exception:
            return rw, cx, cy
    return hwnd, cx, cy


def _pack(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _post(hwnd, msg, wparam=0, lparam=0):
    if not user32.PostMessageW(hwnd, msg, wparam, lparam):
        raise RuntimeError(f"PostMessage {msg:#x} failed")


def _key_lparam(vk, keyup=False):
    try:
        sc = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF
    except Exception:
        sc = 0
    lp = 1 | (sc << 16)
    if keyup:
        lp |= (1 << 30) | (1 << 31)
    return lp


def _sleep(a=0.02, b=0.06):
    time.sleep(random.uniform(a, b))


def _ensure_focus(child):
    """Give the control real input focus without activating its window.

    AttachThreadInput lets SetFocus succeed cross-thread. No cursor move,
    no foreground switch, no taskbar flash - invisible to the user (who is
    on the host PC anyway). Without this, WM_CHAR typing is ignored by
    controls that check focus (e.g. modern rich edits, some web widgets).
    Never raises.
    """
    try:
        fg = user32.GetForegroundWindow()
        fore_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        target_thread = user32.GetWindowThreadProcessId(child, None)
        attached = False
        if fore_thread and target_thread and fore_thread != target_thread:
            try:
                attached = bool(user32.AttachThreadInput(fore_thread,
                                                         target_thread, True))
            except Exception:
                attached = False
        try:
            user32.SetFocus(child)
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(fore_thread, target_thread, False)
                except Exception:
                    pass
    except Exception:
        pass


def bg_click(win, fx, fy):
    """Background left-click at window fraction (fx, fy). Returns (sx, sy).

    (sx, sy) are screen coords for logging parity with foreground clicks.
    The real cursor is untouched and the window is not activated.
    """
    hwnd, cx, cy = resolve_target(win, fx, fy)
    _ensure_focus(hwnd)
    lp = _pack(cx, cy)
    _post(hwnd, WM_MOUSEMOVE, 0, lp)
    _sleep(0.01, 0.03)
    _post(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    _sleep()
    _post(hwnd, WM_LBUTTONUP, 0, lp)
    l, t, r, b = window_rect(hwnd_of(win))
    return int(l + (r - l) * float(fx)), int(t + (b - t) * float(fy))


def bg_multi_click(win, fx, fy, clicks=3, interval=0.08):
    """Repeat bg_click N times (triple-click selects field content)."""
    x = y = 0
    for i in range(max(1, clicks)):
        x, y = bg_click(win, fx, fy)
        if i < clicks - 1:
            time.sleep(interval + random.uniform(0, 0.03))
    return x, y


def bg_type(win, fx, fy, text):
    """Type text via WM_CHAR into the control at (fx, fy). Returns (sx, sy)."""
    hwnd, cx, cy = resolve_target(win, fx, fy)
    _ensure_focus(hwnd)
    deltas = []
    for ch in str(text or ""):
        if ch == "\n":
            _post(hwnd, WM_KEYDOWN, VK_RETURN, _key_lparam(VK_RETURN))
            _sleep(0.01, 0.03)
            _post(hwnd, WM_KEYUP, VK_RETURN, _key_lparam(VK_RETURN, True))
        else:
            _post(hwnd, WM_CHAR, ord(ch), 1)
        deltas.append(ch)
        if len(deltas) % 12 == 0:
            _sleep(0.01, 0.03)
    _sleep(0.05, 0.12)
    l, t, r, b = window_rect(hwnd_of(win))
    return int(l + (r - l) * float(fx)), int(t + (b - t) * float(fy))


def bg_press_key(win, fx, fy, vk):
    """Send a virtual-key press (e.g. VK_BACK/VK_RETURN) at (fx, fy)."""
    hwnd, cx, cy = resolve_target(win, fx, fy)
    _post(hwnd, WM_KEYDOWN, vk, _key_lparam(vk))
    _sleep(0.01, 0.04)
    _post(hwnd, WM_KEYUP, vk, _key_lparam(vk, True))
    return True


def bg_press_enter(win, fx=None, fy=None):
    """Press Enter on the control at (fx, fy), or top window if None."""
    if fx is None or fy is None:
        hwnd = hwnd_of(win)
        rw = _find_render_widget(hwnd) or hwnd
        _post(rw, WM_KEYDOWN, VK_RETURN, _key_lparam(VK_RETURN))
        _sleep(0.01, 0.04)
        _post(rw, WM_KEYUP, VK_RETURN, _key_lparam(VK_RETURN, True))
        return True
    return bg_press_key(win, fx, fy, VK_RETURN)
