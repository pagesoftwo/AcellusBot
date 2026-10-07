"""AcellusAuto - auto-solve Acellus questions via screen capture + free vision.

SETUP INSTRUCTIONS (Windows, Python 3.10+):
  1) pip install -r requirements.txt
     (mss, pygetwindow, pyautogui, keyboard, Pillow, openai, python-dotenv)
     NOTE: `keyboard` global hotkeys need admin on some PCs. If hotkeys fail,
     right-click terminal -> Run as administrator.
  2) Free vision, no card: key from aistudio.google.com in `.env` as:
       GEMINI_API_KEY=...
     config.json: llm_provider=gemini, model=gemini-3.5-flash-lite
     (Direct HTTPS vision. Check delay 2s; each AI call itself takes ~8s.)
     CLI alternative: llm_provider=opencode needs `opencode` on PATH +
     `opencode auth login` (slower, agent-based; free models are text-only).
  3) Run:  python main.py
  4) On startup a Monitor/Window picker appears. Select the "Acellus" window,
     click Save. Then press F8 (or Start button) to begin.
     F8 = Start/Resume | F9 = Pause/Stop | F10 = Emergency quit

CALIBRATION:
   clicker.py CHOICE_POS holds fractional A/B/C/D radio positions.
   Run once, click "Save Debug Screenshot", measure A/B/C/D relative
   positions inside the Acellus window, update CHOICE_POS.

INPUT MODES (config.json):
   "input_mode": "foreground" = real mouse via pyautogui (needs window front).
   "input_mode": "background" = Win32 messages via bginput.py, real cursor
      never moves, window never activated. Acellus may be covered/behind
      other windows (capture auto-switches to PrintWindow). The Acellus
      window itself must NOT be minimized; minimizing the VirtualBox host
      window is fine (guest desktop keeps rendering).
   "capture_mode": "auto" (follows input_mode) | "mss" | "printwindow".

CLASS ROTATION (config.json "class_rotation"):
   {"enabled": true, "minutes_per_class": 30, "per_class": {"MATH": 45}}
   Bot works one class for the budget, clicks the in-lesson back arrow to
   go home, then starts the next class card in line. Times accept minutes
   (30), "90m", "1h", "1h30m" or "1:30". "per_class" matches by substring.
   Budget is wall-clock from entering the class (videos included) unless
   "count_videos": false, which pauses the timer during video waits.
   "skip_classes": ["TECHNOLOGY"] never enters matching cards (substring).
   Timer starts when a class card is clicked; if F8 is pressed mid-lesson
   (or Continue auto-advances), it arms automatically on the first
   question/video. Best started on the dashboard so it can read the cards.

WINDOW FIT (config "auto_fit_window", default true):
   On startup the Acellus window is moved/resized to sit fully above the
   taskbar, so captures never include the taskbar and bottom answer bars
   stay visible and clickable.

WEB DASHBOARD (config "dashboard", default on port 8080):
   Serves ../web (index.html) on the LAN: live status, stats, preview,
   log tail, Start/Pause buttons. Open http://<this-PC-IP>:8080 on your
   phone/another PC (same WiFi). Windows Firewall asks once: Allow it.
   Set dashboard.token to require ?token=... on API calls.

PUBLIC TUNNEL (config "tunnel" - your own domain, automatic):
   {"enabled": true, "mode": "named", "token": "<paste-once>",
    "cloudflared_path": "", "auto_download": true}
   Bot downloads cloudflared itself (if missing), runs and supervises it.
   mode=named uses your domain: one-time setup in Cloudflare Zero Trust ->
   Networks -> Tunnels -> Create tunnel -> copy its Token -> set as
   tunnel.token, with the hostname routed to http://localhost:8080.
   mode=quick needs no account and prints a random trycloudflare URL.
   Public URL shows in the log and on the dashboard footer.
   Token source: tunnel.token in config, else TUNNEL_TOKEN (or
   CLOUDFLARED_TOKEN) env var from .env.

SAFETY:
  - 4-8s cooldown after each click, 3-5s check interval with jitter.
  - Low confidence (<0.75) answers are skipped, never clicked.
  - pyautogui FAILSAFE: slam mouse to a screen corner to abort motion.
"""

import datetime
import json
import os
import random
import threading
import time
import tkinter as tk
from tkinter import messagebox

# Local modules
import capture
import clicker
import paths
import solver
from ui import DebugWindow
from version import APP_NAME, BUILD_TAG, VERSION

# Bump on behavior changes so logs reveal exactly which build is running.
HERE = paths.app_dir()
CONFIG_PATH = paths.config_path()

# Load .env from the app dir explicitly (frozen exe CWD is not reliable).
try:
    from dotenv import load_dotenv
    load_dotenv(paths.dotenv_path())
except Exception:
    pass

DEFAULT_CONFIG = {
    "target_window": "Acellus",
    "monitor_index": 0,
    "llm_provider": "ollama",
    "model": "qwen2.5vl:7b",
    "check_interval_sec": [3, 5],
    "confidence_threshold": 0.75,
    "accuracy": 1.0,
    "typing_speed": 1.0,
    "action_delay_sec": 0.0,
    "auto_submit": False,
    "auto_submit_text": True,
    "max_repeats_before_skip": 3,
    "auto_fit_window": True,
    "class_rotation": {
        "enabled": False,
        "minutes_per_class": 30,
        "per_class": {},
        "count_videos": True,
        "skip_classes": []
    },
    "dashboard": {
        "enabled": True,
        "port": 8080,
        "bind": "0.0.0.0",
        "token": ""
    },
    "update": {
        "enabled": True,
        "manifest_url": "https://raw.githubusercontent.com/<user>/AcellusBot/main/update.json"
    },
    "tunnel": {
        "enabled": False,
        "mode": "named",
        "token": "",
        "cloudflared_path": "",
        "auto_download": True
    },
    "input_mode": "background",
    "capture_mode": "auto",
    "hotkeys": {"start": "f8", "pause": "f9", "quit": "f10"},
}

# ---- global runtime state ----
config = {}
running = threading.Event()   # set = automation active
quit_flag = False
ui = None
last_shot = None  # PIL image for "Save Debug Screenshot"
_lock = threading.Lock()
boot_time = time.time()
stats = {"answered": 0, "videos": 0, "skips": 0, "submits": 0,
         "last_answer": ""}


def bump_stat(key, val=1):
    try:
        stats[key] = int(stats.get(key, 0)) + int(val)
    except Exception:
        pass


# ---------------- config ----------------
def load_config():
    global config
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update(user)
            # merge nested hotkeys
            if "hotkeys" in user and isinstance(user["hotkeys"], dict):
                hk = dict(DEFAULT_CONFIG["hotkeys"])
                hk.update(user["hotkeys"])
                cfg["hotkeys"] = hk
    except Exception as e:
        print(f"[config] failed to load, using defaults: {e}")
    config = cfg
    return cfg


def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        if ui:
            ui.log(f"Failed to save config.json: {e}", "ERROR")


# ---------------- startup picker ----------------
def prompt_target_selection():
    """Blocking Tkinter dialog listing monitors + windows. Returns (target, mon_idx)."""
    sel = {"target": config.get("target_window", "Acellus"),
           "monitor": int(config.get("monitor_index", 0)), "ok": False}

    root = tk.Tk()
    root.title("AcellusAuto - Select Capture Target")
    root.geometry("520x480")

    tk.Label(root, text="Displays (numbered):", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8)
    mon_hdr = tk.Frame(root)
    mon_hdr.pack(fill="x", padx=8)
    tk.Button(mon_hdr, text="Show Displays",
              command=lambda: capture.show_display_numbers(root)).pack(side="left")
    tk.Label(mon_hdr, text="Number flashes top-right of each monitor.",
             font=("Segoe UI", 8)).pack(side="left", padx=8)
    mon_list = tk.Listbox(root, height=4)
    mon_list.pack(fill="x", padx=8)
    try:
        mons = capture.list_monitors()
        if not mons:
            mon_list.insert("end", "Display 0: (no mss monitors found)")
        for i, m in enumerate(mons):
            mon_list.insert("end", f"Display {i}: left={m['left']} top={m['top']} "
                                   f"{m['width']}x{m['height']}")
    except Exception as e:
        mon_list.insert("end", f"(monitor error: {e})")
    try:
        mon_list.selection_set(min(sel["monitor"], mon_list.size() - 1))
    except Exception:
        pass

    tk.Label(root, text="Visible windows (pick one containing 'Acellus'):",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(8, 0))
    win_list = tk.Listbox(root, height=12)
    win_list.pack(fill="both", expand=True, padx=8)
    try:
        titles = capture.list_visible_windows()
        if not titles:
            titles = ["(no windows found)"]
        for t in titles[:200]:
            win_list.insert("end", t)
        # pre-select Acellus match
        for i, t in enumerate(titles[:200]):
            if sel["target"].lower() in t.lower():
                win_list.selection_set(i)
                win_list.see(i)
                break
    except Exception as e:
        win_list.insert("end", f"(window error: {e})")

    entry_var = tk.StringVar(value=sel["target"])
    frm = tk.Frame(root)
    frm.pack(fill="x", padx=8, pady=6)
    tk.Label(frm, text="Target substring:").pack(side="left")
    tk.Entry(frm, textvariable=entry_var, width=30).pack(side="left", padx=6)

    def use_selected_window():
        try:
            idx = win_list.curselection()
            if idx:
                entry_var.set(win_list.get(idx[0])[:80])
        except Exception:
            pass

    tk.Button(root, text="Use highlighted window title",
              command=use_selected_window).pack(padx=8, pady=2)

    def on_save():
        sel["target"] = entry_var.get().strip() or "Acellus"
        try:
            mi = mon_list.curselection()
            if mi:
                sel["monitor"] = int(mi[0])
        except Exception:
            pass
        sel["ok"] = True
        root.destroy()

    tk.Button(root, text="Save & Continue", font=("Segoe UI", 11, "bold"),
              command=on_save).pack(pady=8)
    root.mainloop()
    return sel["target"], sel["monitor"]


# ---------------- actions ----------------
# Consecutive unclickable AI answers (other/low-conf/fail). At 5 the loop
# auto-pauses with the last reason instead of asking forever.
unclickable_streak = 0
last_unclickable_reason = ""


def do_start():
    global unclickable_streak, last_unclickable_reason
    unclickable_streak = 0
    last_unclickable_reason = ""
    running.set()
    if ui:
        ui.set_status("RUNNING")
        ui.set_activity("Running. Capturing screen...")
        ui.log("Started (F8).", "SUCCESS")


def do_pause():
    running.clear()
    try:  # kill a hung "Asking AI..." call so pause takes effect immediately
        import solver_opencode as _oo
        _oo.cancel_current()
    except Exception:
        pass
    if ui:
        ui.set_status("PAUSED")
        ui.set_activity("Paused. Press F8 to resume.")
        ui.log("Paused (F9).", "WARN")


# ---------------- web dashboard providers ----------------
def _dash_status():
    try:
        status = ui.status_var.get() if ui else "IDLE"
    except Exception:
        status = "IDLE"
    try:
        activity = ui.activity_var.get() if ui else ""
    except Exception:
        activity = ""
    try:
        if rotation.get("active"):
            _b = _rotation_budget_sec(rotation.get("active"))
            _el = time.time() - float(rotation.get("started", 0) or 0)
            _left = max(0.0, _b - _el)
            _bl = f"{int(_left // 60)}m {int(_left % 60)}s"
        else:
            _bl = None
        rot = {"active": rotation.get("active"), "budget_left": _bl}
    except Exception:
        rot = {"active": None, "budget_left": None}
    return {
        "status": status,
        "activity": activity,
        "uptime_s": int(time.time() - boot_time),
        "stats": dict(stats, strikes=unclickable_streak),
        "strike_limit": BREAKER_LIMIT,
        "rotation": rot,
        "public_url": _dash_public_url(),
        "config": {
            "model": config.get("model"),
            "input_mode": clicker.INPUT_MODE,
            "rotation_enabled": rotation_enabled(),
            "minutes_per_class": (_rotation_cfg().get("minutes_per_class", 30)),
        },
        "build": BUILD_TAG,
    }


def _dash_log(n=120):
    try:
        with open(os.path.join(HERE, "acellus_auto.log"), "r",
                  encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[-int(n):]
    except Exception as e:
        return [f"(log unavailable: {e})"]


def _dash_shot():
    try:
        img = last_shot
        if img is None:
            return None
        import io as _io
        c = img.convert("RGB")
        c.thumbnail((960, 960))
        buf = _io.BytesIO()
        c.save(buf, format="JPEG", quality=65)
        return buf.getvalue()
    except Exception:
        return None


def _dash_public_url():
    try:
        import tunnel as _tun
        return _tun.public_url()
    except Exception:
        return None


def start_tunnel(dashboard_port):
    """Launch self-managed Cloudflare Tunnel if enabled. Never raises."""
    try:
        import tunnel as _tun

        def _log(msg):
            if ui:
                try:
                    ui.log(f"Tunnel: {msg}", "INFO")
                except Exception:
                    pass

        tcfg = config.get("tunnel") or {}
        if not isinstance(tcfg, dict) or not tcfg.get("enabled", False):
            return False
        ok = _tun.start(tcfg, dashboard_port, _log)
        if ok and ui:
            try:
                ui.log("Tunnel starting (URL appears here + dashboard when up).",
                       "INFO")
            except Exception:
                pass
        return ok
    except Exception as e:
        if ui:
            try:
                ui.log(f"Tunnel failed: {e}", "WARN")
            except Exception:
                pass
        return False


def start_dashboard():
    """Launch the LAN dashboard if enabled in config. Never raises."""
    try:
        dcfg = config.get("dashboard") or {}
        if not isinstance(dcfg, dict) or not dcfg.get("enabled", True):
            return None
        import webserver as _ws
        prov = {"status": _dash_status, "log": _dash_log, "shot": _dash_shot,
                "start": do_start, "pause": do_pause}
        port = int(dcfg.get("port", 8080))
        token = str(dcfg.get("token") or "")
        bind = str(dcfg.get("bind", "0.0.0.0") or "0.0.0.0")
        _srv, _port = _ws.start_server(port, token, paths.web_dir(), prov,
                                       host=bind)
        if ui:
            _where = "this PC only" if bind == "127.0.0.1" else "this PC"
            ui.log(f"Dashboard: http://localhost:{_port} ({_where}).", "SUCCESS")
            if bind != "127.0.0.1":
                try:
                    for _ip in _ws.lan_ips():
                        ui.log(f"Dashboard on other devices: http://{_ip}:{_port}",
                               "SUCCESS")
                except Exception:
                    pass
            if not token:
                ui.log("Dashboard has no token (open LAN). Set dashboard.token "
                       "in config.json to lock it.", "WARN")
        return _srv
    except Exception as e:
        if ui:
            try:
                ui.log(f"Dashboard failed to start (port busy?): {e}", "WARN")
            except Exception:
                pass
        return None


def do_quit():
    global quit_flag
    quit_flag = True
    running.clear()
    try:  # stop the public tunnel first
        import tunnel as _tun_q
        _tun_q.stop()
    except Exception:
        pass
    try:  # don't let a hung AI call block quit
        import solver_opencode as _oo
        _oo.cancel_current()
    except Exception:
        pass
    try:
        import keyboard
        keyboard.unhook_all_hotkeys()
    except Exception:
        pass
    if ui:
        try:
            ui.log("Emergency quit (F10).", "ERROR")
            ui.after(300, ui.destroy)
        except Exception:
            pass
    # hard exit after Tk closes; os._exit as last resort in timer
    def _force():
        time.sleep(1.0)
        os._exit(0)
    threading.Thread(target=_force, daemon=True).start()


def do_change_monitor():
    do_pause()
    target, mon = prompt_target_selection()
    with _lock:
        config["target_window"] = target
        config["monitor_index"] = mon
    save_config()
    if ui:
        ui.log(f"Target changed -> window='{target}' monitor={mon}", "INFO")
        ui.set_status("PAUSED")
        try:  # move the on-monitor overlay to the newly chosen monitor
            mons = capture.list_monitors()
            rect = mons[mon] if mons and 0 <= mon < len(mons) else (mons[0] if mons else None)
            if rect and ui.attach_overlay(rect):
                ui.log(f"Monitor overlay moved to Display {mon}.", "INFO")
        except Exception:
            pass


def do_save_shot():
    global last_shot
    # Always grab the live Acellus window (not last preview), so Pause
    # doesn't save a stale image and we never save our own Debug UI.
    img = None
    try:
        win = capture.find_window(config.get("target_window", "Acellus"))
        if win is not None:
            img = capture.grab_window(win)
            last_shot = img
            if ui:
                ui.update_preview(img)
    except Exception as e:
        if ui:
            ui.log(f"Live capture failed, using last preview: {e}", "WARN")
    if img is None:
        img = last_shot
    if img is None:
        if ui:
            ui.log("No capture yet to save.", "WARN")
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(HERE, f"debug_{ts}.png")
    try:
        img.save(path)
        if ui:
            ui.log(f"Debug screenshot saved: {path}", "SUCCESS")
    except Exception as e:
        if ui:
            ui.log(f"Save failed: {e}", "ERROR")


def do_show_displays():
    """Flash Display 0/1/2... numbers on top-right of each monitor."""
    try:
        parent = ui if ui is not None else None
        n = capture.show_display_numbers(parent)
        if ui:
            ui.log(f"Showing {n} display number(s) top-right for 3s.", "INFO")
    except Exception as e:
        if ui:
            ui.log(f"Show Displays failed: {e}", "ERROR")


def play_finish_sound():
    """Alert: week/unit finished. Windows beep seq + console bell. Non-blocking."""
    def _beep():
        try:
            import winsound
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            for freq, ms in ((880, 250), (660, 250), (990, 400)):
                try:
                    winsound.Beep(freq, ms)
                except Exception:
                    break
                time.sleep(0.06)
        except Exception:
            try:
                print("\a")
            except Exception:
                pass
    threading.Thread(target=_beep, daemon=True).start()


def sleep_cancellable(seconds):
    """Sleep in 0.2s slices so Pause/Quit reacts fast."""
    end = time.time() + seconds
    while time.time() < end:
        if quit_flag or not running.is_set():
            return False
        time.sleep(0.2)
    return True


# ---------------- automation loop ----------------

BREAKER_LIMIT = 5  # consecutive unclickable answers before auto-pause


def _note_unclickable(reason):
    """Count a no-click answer. At limit: pause with diagnosis. Returns True if paused."""
    global unclickable_streak, last_unclickable_reason
    unclickable_streak += 1
    last_unclickable_reason = str(reason)[:160]
    _act(f"No click ({unclickable_streak}/{BREAKER_LIMIT}): {last_unclickable_reason}")
    if unclickable_streak >= BREAKER_LIMIT:
        if ui:
            ui.log(f"AUTO-PAUSE after {BREAKER_LIMIT} consecutive unclickable answers. "
                   f"Last reason: {last_unclickable_reason} "
                   "Check acellus_last_shot.png (what the AI saw), then F8.", "ERROR")
        do_pause()
        if ui:
            ui.set_activity(f"Auto-paused. Last: {last_unclickable_reason}")
        return True
    return False


def _note_clicked():
    global unclickable_streak
    unclickable_streak = 0


# Repeat / wrong-answer tracking. Same question reappearing after a submit
# means the answer was wrong (or the click missed). Escalation happens in
# the question branch: correction-hint re-solve, then Skip.
last_qa = {"q": None, "a": None, "fails": 0, "t": 0.0}


def _parse_answer(result):
    """Extract (letter, text, text2, qtype, conf, header, q) from a result."""
    letter = result.get("answer_letter")
    answer_text = (result.get("answer_text") or "")
    answer_text = answer_text.strip() if isinstance(answer_text, str) else (
        str(answer_text).strip() if answer_text else "")
    answer_text2 = (result.get("answer_text2") or "")
    answer_text2 = answer_text2.strip() if isinstance(answer_text2, str) else (
        str(answer_text2).strip() if answer_text2 else "")
    qtype = (result.get("question_type") or "").strip().lower() \
        if isinstance(result.get("question_type"), str) else ""
    if not qtype:
        # infer for older models that lack question_type
        qtype = "text" if (answer_text and not letter) else "choice"
    try:
        conf = float(result.get("confidence", 0.0) or 0.0)
    except Exception:
        conf = 0.0
    return (letter, answer_text, answer_text2, qtype, conf,
            result.get("header"), (result.get("question") or "")[:120])


def accuracy_setting():
    """Target correctness 0.0-1.0 from config (default 1.0 = always best)."""
    try:
        return max(0.0, min(1.0, float(config.get("accuracy", 1.0))))
    except Exception:
        return 1.0


def apply_accuracy(qtype, letter, answer_text, answer_text2, choices):
    """Maybe replace the correct answer with a plausible wrong one.

    Returns (letter, answer_text, answer_text2, forced_miss: bool).
    Keeps full confidence (the bot believes its wrong answer).
    """
    if random.random() < accuracy_setting():
        return letter, answer_text, answer_text2, False
    if qtype == "choice" and letter:
        others = [L for L in ("A", "B", "C", "D") if L != (letter or "").upper()]
        if others:
            wrong = random.choice(others)
            if ui:
                ui.log(f"Realism roll: answering {wrong} instead of {letter} "
                       f"(accuracy {accuracy_setting():.2f}).", "WARN")
            return wrong, None, None, True
        return letter, answer_text, answer_text2, False
    # text: perturb numerics, else swap with another visible option value
    import re as _rx
    vals = [str(v) for v in (choices or {}).values() if v]
    if answer_text in vals and len(set(vals)) > 1:
        pool = [v for v in vals if v != answer_text]
        wrong = random.choice(pool)
        if ui:
            ui.log(f"Realism roll: typing '{wrong}' instead of "
                   f"'{answer_text}' (accuracy {accuracy_setting():.2f}).",
                   "WARN")
        return None, wrong, None, True

    def _bump(s):
        m = _rx.search(r"-?\d+(?:\.\d+)?", s or "")
        if not m:
            return None
        try:
            num = float(m.group(0))
            step = random.choice([-2, -1, 1, 2])
            new = num + step if float(num).is_integer() else round(num + step, 1)
            news = str(int(new)) if float(new).is_integer() else str(new)
            return s[:m.start()] + news + s[m.end():]
        except Exception:
            return None

    wrong1 = _bump(answer_text)
    wrong2 = _bump(answer_text2) if answer_text2 else answer_text2
    if wrong1 is None and (not answer_text2 or wrong2 is None):
        return letter, answer_text, answer_text2, False
    if ui:
        ui.log(f"Realism roll: typing '{wrong1 or answer_text}"
               f"{(' / ' + wrong2) if answer_text2 else ''}' instead "
               f"(accuracy {accuracy_setting():.2f}).", "WARN")
    return None, wrong1 or answer_text, wrong2, True


def pre_action_delay():
    """Optional human pause before acting (config action_delay_sec)."""
    try:
        d = float(config.get("action_delay_sec", 0.0) or 0.0)
    except Exception:
        d = 0.0
    if d > 0:
        _act(f"Human delay {d:.1f}s...")
        sleep_cancellable(d)


# ---------------- class rotation ----------------
# Optional autonomous schedule: spend N minutes per class, then go home
# (back arrow) and start the next class card. Driven by dashboard sightings
# (AI returns classes[] there) + budget checks mid-lesson.
rotation = {"classes": [], "idx": -1, "active": None, "started": 0.0,
            "entering_until": 0.0}


def _rotation_cfg():
    rc = config.get("class_rotation") or {}
    return rc if isinstance(rc, dict) else {}


def rotation_enabled():
    try:
        return bool(_rotation_cfg().get("enabled", False))
    except Exception:
        return False


def _parse_minutes(v, default=30.0):
    """Accept 30, '45', '90m', '1h', '1h30m', '1:30'. Returns minutes float."""
    try:
        if isinstance(v, bool):
            return default
        if isinstance(v, (int, float)):
            f = float(v)
            return f if f > 0 else default
        import re as _rx
        s = str(v or "").strip().lower()
        if not s:
            return default
        m = _rx.fullmatch(r"(\d+(?:\.\d+)?)\s*m(in)?", s)
        if m:
            return float(m.group(1)) or default
        m = _rx.fullmatch(r"(\d+(?:\.\d+)?)\s*h(?:\s*(\d+(?:\.\d+)?)\s*m?)?", s)
        if m:
            tot = float(m.group(1)) * 60 + (float(m.group(2)) if m.group(2) else 0)
            return tot or default
        m = _rx.fullmatch(r"(\d+)\s*:\s*(\d{1,2})", s)
        if m:
            return float(int(m.group(1)) * 60 + int(m.group(2))) or default
        f = float(s)
        return f if f > 0 else default
    except Exception:
        return default


def _rotation_budget_sec(class_name):
    """Seconds allowed for a class (per-class override or default)."""
    rc = _rotation_cfg()
    default_min = _parse_minutes(rc.get("minutes_per_class", 30))
    mins = default_min
    try:
        per = rc.get("per_class") or {}
        if isinstance(per, dict) and class_name:
            low = str(class_name).lower()
            for k, v in per.items():
                if k and str(k).lower() in low:
                    mins = _parse_minutes(v, default_min)
                    break
    except Exception:
        pass
    return max(60.0, mins * 60.0)


def _rotation_expired():
    """True if the active class used up its budget."""
    if not rotation_enabled() or not rotation.get("active"):
        return False
    try:
        return (time.time() - float(rotation.get("started", 0))) >= \
            _rotation_budget_sec(rotation.get("active"))
    except Exception:
        return False


def _rotation_skipped(name):
    """True if a class name matches the skip_classes list (substring)."""
    try:
        skips = _rotation_cfg().get("skip_classes") or []
        if not isinstance(skips, list) or not name:
            return False
        low = str(name).lower()
        return any(s and str(s).lower() in low for s in skips)
    except Exception:
        return False


def _handle_dashboard(win, classes):
    """Rotation driver on the home screen: enter/advance class cards."""
    now = time.time()
    if classes:
        rotation["classes"] = list(classes)
    try:
        entering_until = float(rotation.get("entering_until", 0) or 0)
    except Exception:
        entering_until = 0.0
    if now < entering_until:
        _act("Lesson page loading, waiting...")
        sleep_cancellable(random.uniform(8, 12))
        return
    cached = [c for c in rotation.get("classes", [])
              if isinstance(c, dict) and c.get("name")]
    if not cached:
        _act("Dashboard (no class list yet). Re-check soon...")
        if ui:
            ui.log("Dashboard seen but no classes parsed; waiting for next sighting.",
                   "INFO")
        sleep_cancellable(random.uniform(10, 15))
        return
    names = [str(c.get("name")) for c in cached]
    active = rotation.get("active")
    try:
        elapsed = now - float(rotation.get("started", 0) or 0)
    except Exception:
        elapsed = 0.0
    if active and _rotation_skipped(active):
        # Class was added to skip_classes mid-run: drop it, advance below.
        if ui:
            ui.log(f"'{active}' is in skip_classes, moving on.", "WARN")
        active = None
        rotation["active"] = None
    if active and active in names and \
            elapsed < _rotation_budget_sec(active):
        # Still within budget (e.g. lesson Continue landed home): go back in.
        tgt = cached[names.index(active)]
        try:
            left_min = int((_rotation_budget_sec(active) - elapsed) // 60)
            _act(f"Returning to {active} ({left_min}m left)...")
            x, y = clicker.click_vision_point(win, tgt["x"], tgt["y"])
            if ui:
                ui.log(f"Entering '{active}' at ({x},{y}).", "SUCCESS")
        except Exception as e:
            if ui:
                ui.log(f"Re-enter failed: {e}", "ERROR")
        rotation["entering_until"] = time.time() + 60
        _note_clicked()
        sleep_cancellable(random.uniform(6, 9))
        return
    # Budget over (or first run): advance to the next non-skipped class.
    try:
        idx = int(rotation.get("idx", -1))
    except Exception:
        idx = -1
    order = [c for c in cached if not _rotation_skipped(c.get("name"))]
    if not order:
        _act("All classes are in skip_classes, waiting...")
        if ui:
            ui.log("Rotation: every class is skipped, idling (no strike).", "WARN")
        sleep_cancellable(random.uniform(18, 23))
        return
    try:
        cur_name = cached[idx].get("name") if 0 <= idx < len(cached) else None
    except Exception:
        cur_name = None
    start_at = 0
    for i, c in enumerate(order):
        if cur_name is not None and c.get("name") == cur_name:
            start_at = (i + 1) % len(order)
            break
    tgt = order[start_at]
    try:
        mins = _rotation_budget_sec(tgt.get("name")) / 60.0
        _act(f"Starting {tgt.get('name')} ({mins:g}m)...")
        x, y = clicker.click_vision_point(win, tgt["x"], tgt["y"])
        if ui:
            ui.log(f"Entering '{tgt.get('name')}' at ({x},{y}) "
                   f"{mins:g}min budget.", "SUCCESS")
        rotation.update(idx=cached.index(tgt), active=tgt.get("name"),
                        started=time.time(), entering_until=time.time() + 60)
    except Exception as e:
        if ui:
            ui.log(f"Class enter failed: {e}", "ERROR")
    _note_clicked()
    sleep_cancellable(random.uniform(6, 9))

def _act(msg):
    try:
        if ui:
            ui.set_activity(msg)
    except Exception:
        pass


def automation_loop():
    global last_shot
    if ui:
        try:
            _nk = 0
            if str(config.get("llm_provider", "")).lower() in ("gemini", "openai", ""):
                import solver as _sv
                _nk = _sv._gemini_key_count()
            _ks = f" keys={_nk}" if _nk else ""
        except Exception:
            _ks = ""
        ui.log(f"Worker started. Target='{config.get('target_window')}' "
               f"model={config.get('model')}{_ks} "
               f"conf>={config.get('confidence_threshold')}", "INFO")
        _act("Running. Capturing screen...")
    while not quit_flag:
        try:
            if not running.is_set():
                time.sleep(0.2)
                continue

            lo, hi = config.get("check_interval_sec", [1.5, 2.5])
            interval = random.uniform(float(lo), float(hi))

            # --- capture ---
            _act("Capturing screen...")
            target = config.get("target_window", "Acellus")
            win = capture.find_window(target)
            if win is None:
                if ui:
                    ui.log(f"Acellus window '{target}' not found. Retrying in 2s...",
                           "ERROR")
                    ui.set_status("ERROR")
                time.sleep(2)
                continue

            try:
                img = capture.grab_window(win)
            except Exception as e:
                if ui:
                    ui.log(f"Capture failed: {e}", "ERROR")
                    # list all Acellus-titled windows to explain tiny-match cases
                    try:
                        cands = capture.list_candidates(target)
                        ui.log(f"Candidates: {cands[:6]}", "WARN")
                    except Exception:
                        pass
                time.sleep(2)
                continue

            # Diagnose tiny/black frames (e.g. 160x28) directly in the log
            try:
                w, h = img.size
                if w < 400 or h < 300:
                    if ui:
                        ui.log(f"Capture suspiciously small: {w}x{h} "
                               f"win='{win.title}' rect=({win.left},{win.top},"
                               f"{win.width}x{win.height}). Unminimize / focus Acellus.",
                               "WARN")
                elif capture.is_mostly_black(img):
                    if ui:
                        ui.log(f"Capture mostly black: {w}x{h} win='{win.title}'. "
                               "If Acellus is HW-accelerated, disable it or move window to Display 0.",
                               "WARN")
            except Exception:
                pass

            last_shot = img
            if ui:
                ui.update_preview(img)
                if ui.status_var.get() == "ERROR":
                    ui.set_status("RUNNING")

            # --- solve (blocking: local CLI takes 30-120s; heartbeat
            # keeps the activity bar alive so it never looks frozen) ---
            _act("Asking AI...")
            _hb_stop = threading.Event()
            _hb_t0 = time.time()
            _hb_deadline = 120  # hard watchdog: never one call past this
            def _hb():
                killed = False
                while not _hb_stop.wait(5):
                    el = int(time.time() - _hb_t0)
                    try:
                        if el > _hb_deadline and not killed:
                            killed = True
                            try:
                                import solver_opencode as _oo
                                _oo.cancel_current()
                            except Exception:
                                pass
                            _act(f"Asking AI... {el}s OVERDUE - killed, retrying next check")
                        else:
                            _act(f"Asking AI... {el}s (F9 cancels the call)")
                    except Exception:
                        break
            threading.Thread(target=_hb, daemon=True).start()
            try:
                result = solver.solve(img, model=config.get("model", "gpt-4o"))
            finally:
                _hb_stop.set()
            try:
                if ui:
                    ui.log(f"AI answered in {time.time() - _hb_t0:.0f}s.", "INFO")
            except Exception:
                pass
            state = result.get("state", "other")

            # Rotation: arm the timer when working a lesson we didn't enter
            # via a dashboard card (F8 pressed mid-lesson, or Continue
            # auto-advanced to the next lesson). Otherwise the budget would
            # never start and rotation would never trigger.
            if state in ("question", "video") and rotation_enabled() \
                    and not rotation.get("active") and time.time() >= \
                    float(rotation.get("entering_until", 0) or 0):
                rotation["active"] = "lesson"
                rotation["started"] = time.time()
                if ui:
                    ui.log("Rotation timer armed mid-lesson "
                           f"({_rotation_budget_sec('lesson') / 60:g}min budget).",
                           "INFO")

            # Class budget expired mid-lesson: go home, dashboard handler
            # picks the next class on the following sighting.
            if state in ("question", "video") and _rotation_expired():
                try:
                    _act(f"Class budget over ({rotation.get('active')}), going home...")
                    _bx, _by = result.get("back_x"), result.get("back_y")
                    _hx, _hy, _hm = clicker.click_back(win, _bx, _by)
                    if ui:
                        ui.log(f"Budget over for '{rotation.get('active')}'. "
                               f"Back at ({_hx},{_hy}) [{_hm}].", "WARN")
                except Exception as _be:
                    if ui:
                        ui.log(f"Back nav failed: {_be}", "ERROR")
                rotation["active"] = None
                rotation["entering_until"] = time.time() + 45
                last_qa.update(q=None, a=None, fails=0, t=0.0)
                _note_clicked()
                _clo, _chi = config.get("post_click_cooldown_sec", [2, 3])
                sleep_cancellable(random.uniform(float(_clo), float(_chi)))
                continue

            if state in ("finished", "review"):
                label = "session end" if state == "finished" else "review/exam screen"
                detail = (result.get("reasoning") or result.get("header") or "").strip()[:150]
                if not config.get("auto_continue", True):
                    _act(f"{label} detected (auto_continue off).")
                    if ui:
                        ui.log(f"{label.upper()} detected but auto_continue=false. {detail}",
                               "WARN")
                    _note_unclickable(f"{state}: {detail}")
                else:
                    _act(f"Clicking Continue ({label})...")
                    try:
                        cx, cy = result.get("continue_x"), result.get("continue_y")
                        method = "vision"
                        if cx is None or cy is None:
                            cx, cy = 500, 875  # bottom-center fallback
                            method = "fallback"
                        x, y = clicker.click_vision_point(win, cx, cy)
                        if ui:
                            ui.log(f"Clicked Continue at ({x},{y}) [{method}] "
                                   f"({label}). {detail}", "SUCCESS")
                        if state == "finished" and config.get("alert_on_finish", True):
                            play_finish_sound()
                        _note_clicked()
                        # Lesson over: drop the rotation timer so the dashboard
                        # advances to the next class instead of re-entering.
                        rotation["active"] = None
                        _act("Resuming...")
                        sleep_cancellable(random.uniform(2, 3))
                    except Exception as e:
                        if ui:
                            ui.log(f"Continue click failed: {e}", "ERROR")
                        _note_unclickable(f"{state} click failed: {e}")
            elif state == "video":
                _act("Video playing. Waiting 20s before re-check (saves quota)...")
                if ui:
                    ui.log("Video playing, re-check in ~20s (quota saver).", "INFO")
                bump_stat("videos")
                _vs = time.time()
                sleep_cancellable(random.uniform(18, 23))
                if rotation_enabled() and rotation.get("active") and \
                        not _rotation_cfg().get("count_videos", True):
                    # Videos don't consume the class budget: shift the timer
                    # forward by the time spent waiting on the video.
                    try:
                        rotation["started"] = float(rotation.get("started", 0)) + \
                            (time.time() - _vs)
                    except Exception:
                        pass
                continue
            elif state == "question":
                _act("Getting info...")
                letter, answer_text, answer_text2, qtype, conf, header, q = \
                    _parse_answer(result)
                thresh = float(config.get("confidence_threshold", 0.75))

                # --- repeat / wrong-answer detection ---
                # Same question showing again after we answered = previous
                # answer was wrong (or the click missed). Escalate instead of
                # looping forever: 1st repeats -> re-solve with a correction
                # hint; >=N repeats -> Skip the question entirely.
                import re as _re2
                _qkey = _re2.sub(r"\s+", " ", (result.get("question") or "")
                                 ).strip().lower()[:160]
                _akey = ((letter or "").upper()
                         or (answer_text + "|" + answer_text2).lower())
                _now = time.time()
                try:
                    _last_t = float(last_qa.get("t", 0) or 0)
                except Exception:
                    _last_t = 0.0
                _same_q = bool(_qkey) and _qkey == last_qa.get("q") and \
                    (_now - _last_t) < 600
                if not _same_q:
                    last_qa.update(q=_qkey, a=None, fails=0, t=_now)
                else:
                    last_qa["fails"] = int(last_qa.get("fails", 0)) + 1
                    last_qa["t"] = _now
                _max_rep = int(config.get("max_repeats_before_skip", 3))
                if _same_q and last_qa["fails"] >= _max_rep:
                    try:
                        _act("Stuck on question, clicking Skip...")
                        _skx, _sky = result.get("skip_x"), result.get("skip_y")
                        _sx, _sy, _sm = clicker.click_skip(win, _skx, _sky)
                        if ui:
                            ui.log(f"Skipped stuck question at ({_sx},{_sy}) [{_sm}] "
                                   f"(seen {last_qa['fails'] + 1}x). [{header}] {q}",
                                   "WARN")
                        bump_stat("skips")
                    except Exception as _se:
                        if ui:
                            ui.log(f"Skip failed: {_se}", "ERROR")
                    last_qa.update(q=None, a=None, fails=0, t=0.0)
                    _note_clicked()
                    _clo, _chi = config.get("post_click_cooldown_sec", [2, 3])
                    sleep_cancellable(random.uniform(float(_clo), float(_chi)))
                    continue
                if _same_q and last_qa.get("a") and _akey and \
                        _akey == last_qa.get("a"):
                    _did_correct = False
                    try:
                        _act("Same answer repeating, asking AI to reconsider...")
                        if ui:
                            ui.log(f"Repeat {last_qa['fails']}x: previous '{_akey}' "
                                   f"didn't stick. Re-solving with correction hint.",
                                   "WARN")
                        _hint = (f"Your previous answer '{_akey}' for this exact "
                                 f"question was marked WRONG (the same question is "
                                 f"showing again). Re-solve from scratch and give a "
                                 f"DIFFERENT answer.")
                        result = solver.solve(img, model=config.get("model", "gpt-4o"),
                                              hint=_hint)
                        letter, answer_text, answer_text2, qtype, conf, header, q = \
                            _parse_answer(result)
                        _akey = ((letter or "").upper()
                                 or (answer_text + "|" + answer_text2).lower())
                        _did_correct = True
                    except Exception as _he:
                        if ui:
                            ui.log(f"Correction re-solve failed: {_he}", "WARN")
                else:
                    _did_correct = False
                # Realism roll (skipped while correcting a previous miss).
                if not _did_correct:
                    letter, answer_text, answer_text2, _miss = apply_accuracy(
                        qtype, letter, answer_text, answer_text2,
                        result.get("choices"))
                    if _miss:
                        _akey = ((letter or "").upper()
                                 or (answer_text + "|" + answer_text2).lower())
                pre_action_delay()
                if qtype == "text":
                    if conf < thresh or not answer_text:
                        if ui:
                            ui.log(f"Low confidence ({conf:.2f}<{thresh}) or no answer_text. "
                                   f"[{header}] {q} Skipping.", "WARN")
                        _note_unclickable(f"low-conf {conf:.2f}<{thresh} [{header}] {q}")
                    else:
                        try:
                            _act(f"Typing answer {answer_text}...")
                            tx, ty = result.get("text_x"), result.get("text_y")
                            # Guard: vision sometimes aims at the green [?] display
                            # box mid-screen (not typeable). Real input is the dark
                            # bottom bar (y ~900-950). Override mid-screen hits.
                            try:
                                _ty = float(ty) if ty is not None else None
                            except Exception:
                                _ty = None
                            if _ty is not None and _ty < 750:
                                if ui:
                                    ui.log(f"text aim ({tx},{ty}) looks like green display box, "
                                           f"redirecting to bottom answer bar.", "WARN")
                                tx, ty = None, None
                            x, y = clicker.click_textbox_and_type(win, answer_text, tx, ty)
                            if ui:
                                ui.log(f"Typed '{answer_text}' at ({x},{y}) "
                                       f"conf={conf:.2f} [{header}] {q} | "
                                       f"{result.get('reasoning','')}", "SUCCESS")
                            stats["last_answer"] = answer_text
                            bump_stat("answered")
                            # Two-box questions (ordered pairs): fill 2nd box
                            # separately - never combine as "a, b" in one field.
                            _a2 = answer_text2
                            if _a2:
                                try:
                                    _t2x, _t2y = result.get("text2_x"), result.get("text2_y")
                                    if _t2x is not None and _t2y is not None:
                                        _act(f"Typing 2nd answer {_a2}...")
                                        _x2, _y2 = clicker.click_textbox_and_type(
                                            win, _a2, _t2x, _t2y)
                                    else:
                                        _act(f"Tab + typing 2nd answer {_a2}...")
                                        clicker.press_tab(win, tx, ty)
                                        _x2, _y2 = clicker.type_focused(
                                            win, _a2, tx, ty)
                                    if ui:
                                        ui.log(f"Typed 2nd '{_a2}' at ({_x2},{_y2}).",
                                               "SUCCESS")
                                        stats["last_answer"] = f"{answer_text} / {_a2}"
                                except Exception as _e2:
                                    if ui:
                                        ui.log(f"2nd box type failed: {_e2}", "ERROR")
                            # Textbox answers need Submit - otherwise Acellus
                            # never grades them. Default ON via auto_submit_text.
                            if config.get("auto_submit_text", True):
                                _act("Clicking Submit...")
                                try:
                                    sx, sy = result.get("submit_x"), result.get("submit_y")
                                    scx, scy, sm = clicker.click_submit(win, sx, sy)
                                    if ui:
                                        ui.log(f"Clicked Submit at ({scx},{scy}) [{sm}].",
                                               "SUCCESS")
                                    bump_stat("submits")
                                except Exception as se:
                                    # fallback: Enter key submits most Acellus forms
                                    try:
                                        if clicker.use_background():
                                            import bginput as _bg
                                            sx2 = result.get("submit_x")
                                            sy2 = result.get("submit_y")
                                            _fx = float(sx2) / 1000.0 if sx2 is not None else None
                                            _fy = float(sy2) / 1000.0 if sy2 is not None else None
                                            _bg.bg_press_enter(win, _fx, _fy)
                                        else:
                                            import pyautogui as _pg
                                            _pg.press("enter")
                                        if ui:
                                            ui.log(f"Submit click failed ({se}), pressed Enter.",
                                                   "WARN")
                                    except Exception as se2:
                                        if ui:
                                            ui.log(f"Submit failed: {se} / Enter failed: {se2}",
                                                   "ERROR")
                            last_qa["a"] = _akey
                            _note_clicked()
                            clo, chi = config.get("post_click_cooldown_sec", [2, 3])
                            _act(f"Cooldown {clo}-{chi}s...")
                            sleep_cancellable(random.uniform(float(clo), float(chi)))
                        except Exception as e:
                            if ui:
                                ui.log(f"Type failed: {e}", "ERROR")
                            _act("Type failed.")
                            _note_unclickable(f"type failed: {e}")
                elif conf < thresh or not letter:
                    if ui:
                        ui.log(f"Low confidence ({conf:.2f}<{thresh}) or no letter. "
                               f"[{header}] {q} Skipping.", "WARN")
                    _note_unclickable(f"low-conf {conf:.2f}<{thresh} [{header}] {q}")
                else:
                    try:
                        _act(f"Clicking answer {letter}...")
                        vx, vy = result.get("click_x"), result.get("click_y")
                        x, y, method = clicker.click_choice(win, letter, vx, vy)
                        if ui:
                            ui.log(f"Clicked {letter} at ({x},{y}) [{method}] "
                                   f"conf={conf:.2f} [{header}] {q} | "
                                   f"{result.get('reasoning','')}", "SUCCESS")
                        stats["last_answer"] = f"{letter}" + (f" ({q[:40]})" if q else "")
                        bump_stat("answered")
                        last_qa["a"] = _akey
                        _note_clicked()
                        if config.get("auto_submit", False):
                            try:
                                sx, sy = result.get("submit_x"), result.get("submit_y")
                                if sx is not None and sy is not None:
                                    scx, scy, sm = clicker.click_submit(win, sx, sy)
                                    if ui:
                                        ui.log(f"Clicked Submit at ({scx},{scy}) [{sm}] (auto_submit).",
                                               "SUCCESS")
                                else:
                                    if clicker.use_background():
                                        import bginput as _bg2
                                        _bg2.bg_press_enter(win)
                                    else:
                                        import pyautogui as _pg2
                                        _pg2.press("enter")
                            except Exception as se3:
                                if ui:
                                    ui.log(f"auto_submit failed: {se3}", "WARN")
                        # short cooldown so it doesn't double-click
                        clo, chi = config.get("post_click_cooldown_sec", [2, 3])
                        _act(f"Cooldown {clo}-{chi}s...")
                        sleep_cancellable(random.uniform(float(clo), float(chi)))
                    except Exception as e:
                        if ui:
                            ui.log(f"Click failed: {e}", "ERROR")
                        _act("Click failed.")
                        _note_unclickable(f"click failed: {e}")
            else:
                _act("Checking screen...")
                _reason = str(result.get('reasoning', '') or '')
                if ui:
                    ui.log(f"State=other. {_reason[:150]}", "INFO")
                # Quota/rate-limit is transient - wait it out, don't burn the
                # 5-strike auto-pause on it.
                _rl = _reason.lower()
                if "429" in _rl or "quota" in _rl or "ratelimit" in _rl:
                    _act("API quota hit. Waiting 45s for reset...")
                    if ui:
                        ui.log("Quota exhausted (free Gemini). Pausing 45s, not counting strike.",
                               "WARN")
                    sleep_cancellable(45)
                elif any(k in _rl for k in ("dashboard", "course card", "home screen",
                                            "gpa", "work completed")) or \
                        bool([c for c in (result.get("classes") or [])
                              if isinstance(c, dict) and c.get("name")]):
                    # Home screen between lessons.
                    if rotation_enabled():
                        _dash_classes = [c for c in (result.get("classes") or [])
                                         if isinstance(c, dict) and c.get("name")]
                        _handle_dashboard(win, _dash_classes)
                        continue
                    _act("Dashboard idle. Re-check in ~20s...")
                    if ui:
                        ui.log("Dashboard idle, waiting (no strike).", "INFO")
                    sleep_cancellable(random.uniform(18, 23))
                else:
                    _note_unclickable(f"state=other: {_reason[:120]}")

            sleep_cancellable(interval)

        except Exception as e:
            # Never crash the loop
            try:
                if ui:
                    ui.log(f"Loop exception: {type(e).__name__}: {e}", "ERROR")
            except Exception:
                print(f"Loop exception: {e}")
            time.sleep(1)


# ---------------- hotkeys ----------------
def register_hotkeys():
    try:
        import keyboard
        hk = config.get("hotkeys", {})
        keyboard.add_hotkey(hk.get("start", "f8"), do_start)
        keyboard.add_hotkey(hk.get("pause", "f9"), do_pause)
        keyboard.add_hotkey(hk.get("quit", "f10"), do_quit)
        if ui:
            ui.log(f"Hotkeys: {hk.get('start','f8').upper()}=Start "
                   f"{hk.get('pause','f9').upper()}=Pause "
                   f"{hk.get('quit','f10').upper()}=Quit", "INFO")
    except Exception as e:
        if ui:
            ui.log(f"Hotkey registration failed (try admin): {e}", "WARN")
        else:
            print(f"Hotkey registration failed: {e}")


# ---------------- entry ----------------
def main():
    global ui
    # DPI awareness: makes pygetwindow rects match pyautogui/mss pixels
    # on 125%/150% scaled displays. No-op on failure.
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass
    load_config()

    # 1b) input + capture modes (background = no real mouse, window may be
    # covered; it must NOT be minimized - minimizing the VirtualBox host
    # window is fine, the guest desktop keeps rendering).
    try:
        imode = clicker.set_input_mode(config.get("input_mode", "foreground"))
        clicker.set_speed(config.get("typing_speed", 1.0))
        cmode = str(config.get("capture_mode", "auto")).lower()
        if cmode == "auto":
            cmode = "pw_first" if imode == "background" else "mss_first"
        capture.set_capture_mode(cmode)
    except Exception as e:
        print(f"[modes] falling back to foreground/mss: {e}")
        imode, cmode = "foreground", "mss_first"

    # Auto-update check (non-blocking: manifest fetch + download in thread).
    try:
        import updater as _upd
        _u_cfg = config.get("update") or {}
        _u_info = _upd.check_for_update(_u_cfg)
        if _u_info.get("update"):
            import tkinter.messagebox as _mb
            _mb.showinfo("AcellusBot update",
                         f"Update available: V{_u_info['latest_version']} "
                         f"(current V{VERSION}).\n{_u_info.get('notes','')}\n\n"
                         "It will stage the new exe next to this app; "
                         "restart to apply.")
            try:
                import sys as _sys
                _exe = _sys.executable if getattr(_sys, "frozen", False) else os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "AcellusBot.exe")
                _staged = _upd.prepare_swap(_u_cfg, _exe,
                                            lambda m: ui.log(f"Update: {m}", "INFO"))
                if _staged:
                    ui.log(f"Update staged: {_staged}. Restart to apply.", "SUCCESS")
            except Exception as _ue:
                ui.log(f"Update stage failed: {_ue}", "WARN")
        elif _u_info.get("error"):
            try:
                ui.log(f"Update check: {_u_info['error']}", "INFO")
            except Exception:
                pass
    except Exception as _ueo:
        try:
            ui.log(f"Update check error: {_ueo}", "WARN")
        except Exception:
            pass

    # 1) startup picker (Tk)
    target, mon = prompt_target_selection()
    config["target_window"] = target
    config["monitor_index"] = mon
    save_config()

    # 2) debug UI
    ui = DebugWindow(on_start=do_start, on_pause=do_pause,
                     on_change_monitor=do_change_monitor,
                     on_save_shot=do_save_shot,
                     on_show_displays=do_show_displays)
    ui.set_close_handler(do_quit)
    ui.set_status("IDLE")
    ui.log(f"AcellusBot V{VERSION} ready. Target='{target}' monitor={mon}. "
           f"input={imode} capture={cmode}. Press F8 to start. "
           f"[build {BUILD_TAG}]", "INFO")
    # On-monitor overlay: always-on-top status bar on the chosen monitor.
    try:
        mons = capture.list_monitors()
        rect = mons[mon] if mons and 0 <= mon < len(mons) else (mons[0] if mons else None)
        if rect and ui.attach_overlay(rect):
            ui.log(f"Monitor overlay pinned to Display {mon} top-right.", "INFO")
        else:
            ui.log("Monitor overlay unavailable (no monitor rect).", "WARN")
    except Exception as e:
        ui.log(f"Monitor overlay failed: {e}", "WARN")
    # Fit the Acellus window above the taskbar so captures never include
    # it and bottom answer bars stay visible/clickable. Silent no-op if ok.
    try:
        if config.get("auto_fit_window", True):
            _fw = capture.find_window(config.get("target_window", "Acellus"))
            if _fw is not None:
                _changed, _note = capture.fit_window_to_workarea(_fw)
                ui.log(f"Window fit: {_note}.", "INFO")
            else:
                ui.log("Window fit: target not open yet, skipping.", "WARN")
    except Exception as e:
        ui.log(f"Window fit check failed: {e}", "WARN")
    # Web dashboard (phone/other PC on the LAN).
    start_dashboard()
    # Public tunnel (your domain) - self-managed cloudflared.
    try:
        _dp = int((config.get("dashboard") or {}).get("port", 8080))
    except Exception:
        _dp = 8080
    start_tunnel(_dp)
    # Serve reachability: attach only helps if this is UP.
    try:
        import solver_opencode as _oo0
        _srv = _oo0._server_url()
        if _srv:
            ui.log(f"Opencode serve {_srv}: "
                   f"{'UP (warm calls)' if _oo0.serve_up() else 'DOWN (direct runs; start `opencode serve --port 4096` in a 2nd terminal for speed)'}",
                   "INFO")
    except Exception:
        pass

    # Preflight: backend-specific checks so the loop doesn't spam.
    try:
        prov = str(config.get("llm_provider", "")).lower()
        if prov == "ollama":
            import ollama as _ol

            def _olog(m):
                try:
                    ui.log(f"Ollama: {m}", "INFO")
                except Exception:
                    pass

            _st = _ol.setup_status(config.get("model") or _ol.DEFAULT_MODEL)
            if not _st.get("installed"):
                ui.log("PREFLIGHT: Ollama not installed. Run setup_ollama.bat "
                       "(or install from ollama.com/download/windows).", "ERROR")
                ui.set_activity("Ollama missing - run setup_ollama.bat.")
            else:
                _ol.ensure_server(timeout_s=15)
                _st2 = _ol.setup_status(config.get("model"))
                if not _st2.get("ready"):
                    ui.log(f"Ollama model '{_st2.get('want')}' not pulled yet; "
                           "downloading in background.", "WARN")
                    _ol.ensure_model(_st2.get("want"), _olog, background=True)
                else:
                    ui.log(f"Ollama ready: {_st2.get('want')}", "SUCCESS")
        elif prov == "opencode":
            import shutil
            if shutil.which("opencode") is None:
                ui.log("PREFLIGHT FAIL: `opencode` not on PATH. Install it, then "
                       "CLOSE + REOPEN this terminal before `python main.py`. "
                       "Options: `npm install -g opencode-ai` | `choco install opencode` | "
                       "`scoop install opencode` | binary from github.com/anomalyco/opencode/releases. "
                       "Then: `opencode auth login`.",
                       "ERROR")
                ui.set_activity("opencode missing on PATH - see log for install steps.")
        elif prov == "zen":
            import os as _os
            from dotenv import load_dotenv as _ld
            _ld()
            if not (_os.getenv("OPENCODE_ZEN_API_KEY") or _os.getenv("OPENCODE_API_KEY")):
                ui.log("PREFLIGHT FAIL: llm_provider=zen but no key. Copy it from "
                       "opencode.ai/auth and add to .env as OPENCODE_ZEN_API_KEY=... "
                       "then restart. (Free vision model, no CLI needed.)",
                       "ERROR")
                ui.set_activity("Zen key missing - see log.")
        elif prov == "github":
            import os as _os
            from dotenv import load_dotenv as _ld
            _ld()
            if not (_os.getenv("GITHUB_TOKEN") or _os.getenv("GH_TOKEN")):
                ui.log("PREFLIGHT FAIL: llm_provider=github but no token. Create at "
                       "github.com/settings/tokens (classic, no scopes) and add to "
                       ".env as GITHUB_TOKEN=... then restart. (Free GPT-4o vision.)",
                       "ERROR")
                ui.set_activity("GitHub token missing - see log.")
        elif prov == "groq":
            import os as _os
            from dotenv import load_dotenv as _ld
            _ld()
            if not _os.getenv("GROQ_API_KEY"):
                ui.log("PREFLIGHT FAIL: llm_provider=groq but no key. Free, no card: "
                       "console.groq.com -> API Keys -> add to .env as "
                       "GROQ_API_KEY=... then restart.",
                       "ERROR")
                ui.set_activity("Groq key missing - see log.")
        elif prov == "gemini":
            import os as _os
            from dotenv import load_dotenv as _ld
            _ld()
            if not _os.getenv("GEMINI_API_KEY"):
                ui.log("PREFLIGHT FAIL: llm_provider=gemini but no key. Free, no card: "
                       "aistudio.google.com -> Get API key -> add to .env as "
                       "GEMINI_API_KEY=... then restart.",
                       "ERROR")
                ui.set_activity("Gemini key missing - see log.")
    except Exception:
        pass

    register_hotkeys()

    # 3) worker thread
    t = threading.Thread(target=automation_loop, daemon=True)
    t.start()

    # 4) Tk mainloop (blocks)
    try:
        ui.mainloop()
    except KeyboardInterrupt:
        do_quit()


if __name__ == "__main__":
    main()
