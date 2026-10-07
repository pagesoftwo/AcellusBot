"""capture.py - Window finding + screen capture for AcellusAuto.

Only uses: mss, pygetwindow, Pillow, pyautogui (fallback for black mss frames)
PrintWindow path uses stdlib ctypes only (no new deps) and can capture
windows that are covered by other windows (needed for background mode).
"""

import mss
import pygetwindow as gw
from PIL import Image

# Console hosts (cmd/powershell/terminal) often match only because the
# command line contains the target word, e.g. `... - python -c "...Acellus..."`
# or the prompt path does. Never pick them unless explicitly targeted.
CONSOLE_MARKERS = ("cmd.exe", "powershell", "pwsh.exe", "windows terminal",
                   " - python", "python.exe", "conhost.exe")
# File Explorer folder views ("acellus - File Explorer" / bare "acellus")
# beat the real "Acellus | Student" browser tab on exact/shortest-title
# ranking. Deprioritize them unless explicitly targeted.
EXPLORER_MARKERS = (" - file explorer",)

# Windows smaller than this are treated as noise (tooltips, taskbar chips).
# 160x28 black squares seen in the wild fall here. We prefer a real app window.
MIN_WIDTH = 400
MIN_HEIGHT = 300


def list_visible_windows():
    """Return list of non-empty visible window titles."""
    try:
        titles = gw.getAllTitles()
    except Exception:
        return []
    return [t for t in titles if t and t.strip()]


def find_window(substring="Acellus"):
    """Find first window whose title contains substring (case-insensitive).

    Excludes our own AcellusAuto windows so Target='Acellus' never
    matches 'AcellusAuto - Debug' / 'AcellusAuto - Select Capture Target'.
    Returns pygetwindow.Window or None.
    """
    if not substring:
        return None
    needle = substring.lower()
    # If user is NOT explicitly looking for our own UI, skip it.
    exclude_own = "acellusauto" not in needle
    # Same for console hosts and Explorer: skip unless explicitly targeted.
    exclude_console = not any(m in needle for m in CONSOLE_MARKERS)
    exclude_explorer_by_name = "explorer" not in needle and "file explorer" not in needle
    try:
        # Collect all candidates, then rank: exact > startswith > early position > short title.
        # This avoids matching "acellus_auto.log - acellus - Visual Studio Code"
        # when the real "Acellus" / "Acellus | Student" window exists.
        # Tiny windows (<MIN_WIDTH x MIN_HEIGHT, e.g. 160x28 chips) are
        # deprioritized - only used if nothing bigger matches.
        # Consoles deprioritized harder (command line often contains needle).
        # Explorer deprioritized (folder "acellus" beats browser tab otherwise).
        cands = []
        tiny = []
        for w in gw.getAllWindows():
            try:
                title = w.title or ""
            except Exception:
                continue
            low = title.lower()
            if needle not in low:
                continue
            if exclude_own and "acellusauto" in low:
                continue
            if exclude_console and any(m in low for m in CONSOLE_MARKERS):
                continue
            try:
                ww, hh = int(w.width), int(w.height)
                if ww <= 0 or hh <= 0:
                    continue
                is_tiny = ww < MIN_WIDTH or hh < MIN_HEIGHT
            except Exception:
                continue
            pos = low.find(needle)
            exact = 1 if low.strip() == needle else 0
            starts = 1 if low.strip().startswith(needle) else 0
            is_explorer = 1 if (exclude_explorer_by_name and any(m in low for m in EXPLORER_MARKERS)) else 0
            # bare folder title "acellus": same explorer penalty (no marker in title)
            if exclude_explorer_by_name and low.strip() == needle and len(title.strip()) <= len(needle) + 2:
                # ambiguous bare "Acellus": could be folder view, not browser.
                # Prefer longer browser titles ("Acellus | Student").
                is_explorer = 1
            # sort key: non-explorer first, then exact, startswith, earliest pos, shortest title
            entry = (is_explorer, -exact, -starts, pos, len(title), title, w)
            (cands if not is_tiny else tiny).append(entry)
        for pool in (cands, tiny):
            if pool:
                pool.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4]))
                return pool[0][6]
        return None
    except Exception:
        pass
    return None


def list_candidates(substring="Acellus"):
    """Debug helper: list (title, w, h) for all matches. Use in logs."""
    out = []
    needle = (substring or "").lower()
    try:
        for w in gw.getAllWindows():
            try:
                t = w.title or ""
            except Exception:
                continue
            if needle in t.lower():
                try:
                    out.append((t, int(w.width), int(w.height)))
                except Exception:
                    out.append((t, -1, -1))
    except Exception:
        pass
    return out


def is_mostly_black(img, sample=2000, thresh=12):
    """True if a downscaled sample averages below thresh (0-255)."""
    try:
        small = img.convert("L").resize((64, 64))
        px = list(small.getdata())
        return (sum(px) / max(1, len(px))) < thresh
    except Exception:
        return False


def get_window_rect(win):
    """Return {'left','top','width','height'} for a pygetwindow Window."""
    return {
        "left": int(win.left),
        "top": int(win.top),
        "width": int(win.width),
        "height": int(win.height),
    }


def list_monitors():
    """Return list of mss monitors excluding the 'all monitors' entry at index 0."""
    try:
        with mss.mss() as sct:
            # sct.monitors[0] = virtual full desktop, [1..n] = physical
            return list(sct.monitors[1:])
    except Exception:
        return []


def grab_window(win_or_title="Acellus"):
    """Capture only the target window region. Returns PIL.Image (RGB).

    Tries mss first (fast). If the frame is tiny or mostly black
    (minimized / hardware-accelerated / wrong window), retries once via
    pyautogui.screenshot which uses a different BitBlt path.
    Args:
        win_or_title: pygetwindow.Window object or title substring.
    Raises:
        RuntimeError if window not found.
    """
    win = win_or_title
    if isinstance(win_or_title, str):
        win = find_window(win_or_title)
    if win is None:
        raise RuntimeError("Target window not found")

    rect = get_window_rect(win)
    if rect["width"] <= 0 or rect["height"] <= 0:
        raise RuntimeError("Target window has zero size (minimized?)")
    if rect["width"] < MIN_WIDTH or rect["height"] < MIN_HEIGHT:
        raise RuntimeError(
            f"Matched window '{getattr(win, 'title', '?')}' "
            f"is only {rect['width']}x{rect['height']} - "
            "likely a tooltip/chip, not the Acellus app. "
            "Bring the Acellus window to front / unminimize it."
        )

    pw_first = (CAPTURE_MODE == "pw_first")

    def _mss():
        region = {
            "left": rect["left"],
            "top": rect["top"],
            "width": rect["width"],
            "height": rect["height"],
        }
        try:
            with mss.mss() as sct:
                shot = sct.grab(region)
                # Convert BGRA -> RGB PIL Image
                return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as e:
            raise RuntimeError(f"mss grab failed: {e}")

    def _pyautogui_fb():
        try:
            import pyautogui
            # pyautogui uses GDI BitBlt - different path, often works when mss is black.
            fb = pyautogui.screenshot(
                region=(rect["left"], rect["top"], rect["width"], rect["height"])
            ).convert("RGB")
            if not is_mostly_black(fb):
                return fb
        except Exception:
            pass
        return None

    if pw_first:
        # Background mode: PrintWindow renders the true window content even
        # when covered. mss would capture whatever is on top instead.
        try:
            img = grab_window_pw(win)
            if not is_mostly_black(img):
                return img
        except Exception:
            pass
        try:
            return _mss()
        except Exception:
            pass
        fb = _pyautogui_fb()
        return fb if fb is not None else _mss()

    # Foreground mode: mss first (fast).
    img = _mss()

    # Black-frame fallback chain: pyautogui, then PrintWindow.
    if is_mostly_black(img):
        fb = _pyautogui_fb()
        if fb is not None:
            return fb
        try:
            pw = grab_window_pw(win)
            if not is_mostly_black(pw):
                return pw
        except Exception:
            pass
        # all black: return mss frame anyway, caller logs rect for diagnosis
    return img


# ---------------- capture mode ----------------
# "mss_first": screen grab first (foreground bot, fastest).
# "pw_first": PrintWindow first (background bot: correct content even when
#   the window is covered by other windows; mss would capture the occluder).
CAPTURE_MODE = "mss_first"


def set_capture_mode(mode):
    """Set module capture order. mode in mss_first|pw_first|mss|printwindow."""
    global CAPTURE_MODE
    m = (mode or "mss_first").strip().lower()
    if m in ("mss_first", "pw_first", "mss", "printwindow", "pw"):
        CAPTURE_MODE = "pw_first" if m in ("printwindow", "pw") else (
            "mss_first" if m in ("mss_first", "mss") else m)


def get_work_area():
    """Usable desktop rect (screen minus taskbar). Returns dict or None."""
    try:
        import ctypes

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        r = _RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                        ctypes.byref(r), 0)
        if not ok:
            return None
        return {"left": r.left, "top": r.top,
                "right": r.right, "bottom": r.bottom}
    except Exception:
        return None


def fit_window_to_workarea(win, margin=8):
    """Move/resize window so it is fully visible above the taskbar.

    Fixes captures that include the taskbar and bottom answer bars hidden
    underneath it (window taller than the usable desktop). No activation,
    no focus steal. Returns (changed: bool, note: str). Never raises.
    """
    try:
        rect = get_window_rect(win)
        wa = get_work_area()
        if wa is None:
            return False, "work area unknown"
        nl, nt = rect["left"], rect["top"]
        nw, nh = rect["width"], rect["height"]
        max_w = wa["right"] - wa["left"] - margin * 2
        max_h = wa["bottom"] - wa["top"] - margin * 2
        if nw > max_w:
            nw = max_w
        if nh > max_h:
            nh = max_h
        if nl < wa["left"]:
            nl = wa["left"] + margin
        if nt < wa["top"]:
            nt = wa["top"] + margin
        if nl + nw > wa["right"]:
            nl = wa["right"] - nw - margin
        if nt + nh > wa["bottom"]:
            nt = wa["bottom"] - nh - margin
        if (nl, nt, nw, nh) == (rect["left"], rect["top"],
                                rect["width"], rect["height"]):
            return False, "already fits work area"
        try:
            if hasattr(win, "resizeTo"):
                win.resizeTo(int(nw), int(nh))
        except Exception:
            pass
        try:
            if hasattr(win, "moveTo"):
                win.moveTo(int(nl), int(nt))
        except Exception:
            pass
        return True, f"fitted to {int(nw)}x{int(nh)} at ({int(nl)},{int(nt)})"
    except Exception as e:
        return False, f"fit failed: {e}"


def grab_window_pw(win):
    """Capture a window via PrintWindow (works covered/occluded, not minimized).

    Returns PIL.Image (RGB). Raises RuntimeError on failure.
    """
    import ctypes
    from ctypes import wintypes
    import bginput as _bg

    hwnd = _bg.hwnd_of(win)
    if _bg.is_minimized_hwnd(hwnd):
        raise RuntimeError("Target window is minimized - PrintWindow cannot "
                           "capture minimized windows. Restore it (it may stay "
                           "behind other windows).")
    left, top, right, bottom = _bg.window_rect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError("Target window has zero size (minimized?)")

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    PW_RENDERFULLCONTENT = 0x00000002

    hdc_win = user32.GetWindowDC(hwnd)
    if not hdc_win:
        raise RuntimeError("GetWindowDC failed")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    if not hdc_mem or not hbmp:
        try:
            user32.ReleaseDC(hwnd, hdc_win)
        except Exception:
            pass
        raise RuntimeError("GDI objects failed")
    try:
        gdi32.SelectObject(hdc_mem, hbmp)
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        if not ok:  # older app: retry without full-content flag
            ok = user32.PrintWindow(hwnd, hdc_mem, 0)
        if not ok:
            raise RuntimeError("PrintWindow failed")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD),
                        ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf_len = w * h * 4
        buf = ctypes.create_string_buffer(buf_len)
        lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, h,
                                buf, ctypes.byref(bmi), 0)
        if not lines:
            raise RuntimeError("GetDIBits failed")
        img = Image.frombytes("RGB", (w, h), bytes(buf), "raw", "BGRX")
        return img
    finally:
        try:
            gdi32.DeleteObject(hbmp)
        except Exception:
            pass
        try:
            gdi32.DeleteDC(hdc_mem)
        except Exception:
            pass
        try:
            user32.ReleaseDC(hwnd, hdc_win)
        except Exception:
            pass


def grab_monitor(monitor_index=0):
    """Capture full monitor. monitor_index 0 = first physical monitor.

    Returns PIL.Image.
    """
    with mss.mss() as sct:
        monitors = sct.monitors[1:]  # skip virtual
        if not monitors:
            raise RuntimeError("No monitors found")
        idx = max(0, min(monitor_index, len(monitors) - 1))
        # mss list is 1-based internally; monitors var is already sliced
        mon = monitors[idx]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img


def show_display_numbers(tk_parent=None, duration_ms=3000):
    """Flash a big overlay number on the top-right of every monitor.

    Display indices match list_monitors() order:
      Display 0 = monitors[0], Display 1 = monitors[1], ...
    Same numbers shown in the startup picker listbox and config monitor_index.

    Args:
        tk_parent: an existing Tk/Toplevel to parent overlays to.
            If None, a temporary hidden Tk root is created.
        duration_ms: how long overlays stay visible.
    Returns:
        int: number of overlays shown.
    """
    mons = list_monitors()
    if not mons:
        return 0

    # Local import so capture.py stays usable without tkinter.
    import tkinter as tk

    own_root = False
    if tk_parent is None:
        tk_parent = tk.Tk()
        tk_parent.withdraw()
        own_root = True

    overlays = []
    for i, m in enumerate(mons):
        try:
            top = tk.Toplevel(tk_parent)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            # Position: top-right of this monitor, 20px inset.
            w, h = 220, 130
            x = int(m["left"] + m["width"] - w - 20)
            y = int(m["top"] + 20)
            top.geometry(f"{w}x{h}+{x}+{y}")
            top.configure(bg="#2563eb")
            lbl = tk.Label(
                top, text=str(i), font=("Segoe UI", 64, "bold"),
                bg="#2563eb", fg="white",
            )
            lbl.pack(expand=True)
            sub = tk.Label(
                top, text=f"Display {i}", font=("Segoe UI", 12, "bold"),
                bg="#2563eb", fg="white",
            )
            sub.pack()
            # Auto-close
            top.after(duration_ms, top.destroy)
            overlays.append(top)
        except Exception:
            continue

    # If we created our own root, run a short mainloop to show overlays.
    if own_root:
        try:
            tk_parent.after(duration_ms + 200, tk_parent.destroy)
            tk_parent.mainloop()
        except Exception:
            pass
    return len(overlays)
