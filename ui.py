"""ui.py - AcellusBot V5 dashboard window (purple theme). Keeps the old
DebugWindow API surface so main.py keeps working."""

import datetime
import os
import tkinter as tk
from tkinter import scrolledtext, ttk

from PIL import ImageTk

try:
    import paths as _paths_ui
    LOG_FILE = os.path.join(_paths_ui.app_dir(), "acellus_auto.log")
except Exception:
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "acellus_auto.log")

PURPLE_DARK = "#4c1d95"
PURPLE_SOFT = "#ede9fe"
BG = "#14111c"
SURFACE = "#1e1a2e"
SURFACE_2 = "#2a2340"
TEXT = "#f3f0ff"
MUTED = "#b8b0d9"
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"

STATUS_COLORS = {
    "IDLE": (SURFACE_2, MUTED),
    "RUNNING": ("#065f46", "#34d399"),
    "PAUSED": ("#7c2d12", "#fbbf24"),
    "ERROR": ("#7f1d1d", "#f87171"),
}
STATUS_DOT = {"IDLE": "o", "RUNNING": "*", "PAUSED": "||", "ERROR": "!"}


class MonitorOverlay:
    WIDTH, HEIGHT, MARGIN = 300, 64, 12

    def __init__(self, root, status_var, activity_var, monitor_rect):
        self.top = tk.Toplevel(root)
        t = self.top
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        try:
            t.attributes("-alpha", 0.92)
        except Exception:
            pass
        try:
            mx = int(monitor_rect.get("left", 0)) + int(monitor_rect.get("width", 1920))
            my = int(monitor_rect.get("top", 0))
            t.geometry(f"{self.WIDTH}x{self.HEIGHT}+{mx - self.WIDTH - self.MARGIN}+{my + self.MARGIN}")
        except Exception:
            pass
        t.configure(bg=SURFACE)
        self.status_lbl = tk.Label(t, textvariable=status_var, font=("Segoe UI", 11, "bold"),
                                   bg=SURFACE, fg=TEXT, anchor="w", padx=8)
        self.status_lbl.pack(fill="x")
        self.act_lbl = tk.Label(t, textvariable=activity_var, font=("Segoe UI", 8),
                                bg=SURFACE, fg=MUTED, anchor="w", padx=8,
                                wraplength=self.WIDTH - 16, justify="left")
        self.act_lbl.pack(fill="x")
        t.update_idletasks()
        self._click_through()

    def _click_through(self):
        try:
            import ctypes
            hwnd = self.top.winfo_id()
            get = ctypes.windll.user32.GetWindowLongW
            set_ = ctypes.windll.user32.SetWindowLongW
            style = get(hwnd, -20)
            set_(hwnd, -20, style | 0x20 | 0x80000)
        except Exception:
            pass

    def set_colors(self, bg, fg):
        try:
            self.status_lbl.configure(bg=bg, fg=fg)
        except Exception:
            pass


class DashboardWindow(tk.Tk):
    def __init__(self, on_start=None, on_pause=None, on_change_monitor=None,
                 on_save_shot=None, on_show_displays=None):
        super().__init__()
        try:
            from version import APP_NAME, VERSION
            _name, _ver = APP_NAME, VERSION
        except Exception:
            _name, _ver = "AcellusBot", "5.x"
        self._ver = _ver
        self.title(f"{_name} V{_ver} - Dashboard")
        self.geometry("720x860")
        self.configure(bg=BG)
        self.minsize(600, 720)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._on_close_cb = None

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=SURFACE_2, foreground=MUTED,
                        padding=(14, 8), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", PURPLE_DARK)],
                  foreground=[("selected", TEXT)])
        style.configure("Purple.TFrame", background=BG)

        hdr = tk.Frame(self, bg=PURPLE_DARK, padx=16, pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"{_name} V{_ver}", font=("Segoe UI", 18, "bold"),
                 bg=PURPLE_DARK, fg="white").pack(anchor="w")
        tk.Label(hdr, text="Local AI homework automation - background mode",
                 font=("Segoe UI", 9), bg=PURPLE_DARK, fg=PURPLE_SOFT).pack(anchor="w")

        self.status_var = tk.StringVar(value="IDLE")
        self.status_label = tk.Label(self, textvariable=self.status_var,
                                     font=("Segoe UI", 13, "bold"), padx=12, pady=6)
        self.status_label.pack(fill="x", padx=12, pady=(10, 0))
        self.set_status("IDLE")
        self.activity_var = tk.StringVar(value="Idle. Press F8 to start.")
        tk.Label(self, textvariable=self.activity_var, font=("Segoe UI", 10),
                 bg=BG, fg=MUTED, anchor="w", padx=12).pack(fill="x")

        self.overlay = None
        self._ov_status = tk.StringVar(value="o IDLE")
        self._ov_activity = tk.StringVar(value="Idle. Press F8 to start.")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        live = ttk.Frame(nb, style="Purple.TFrame")
        nb.add(live, text="Live")
        tk.Label(live, text="Capture preview:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=(8, 0))
        self.preview_label = tk.Label(live, text="(no capture yet)", bg="#0f0d16",
                                      fg=MUTED, height=12)
        self.preview_label.pack(fill="both", expand=True, padx=8, pady=4)
        self._photo = None
        btn = ttk.Frame(live)
        btn.pack(fill="x", padx=8, pady=8)
        ttk.Button(btn, text="Start (F8)", command=on_start).pack(side="left", padx=3)
        ttk.Button(btn, text="Pause (F9)", command=on_pause).pack(side="left", padx=3)
        ttk.Button(btn, text="Change Monitor", command=on_change_monitor).pack(side="left", padx=3)
        ttk.Button(btn, text="Save Screenshot", command=on_save_shot).pack(side="left", padx=3)
        ttk.Button(btn, text="Show Displays", command=on_show_displays).pack(side="left", padx=3)

        logtab = ttk.Frame(nb, style="Purple.TFrame")
        nb.add(logtab, text="Log")
        self.log_text = scrolledtext.ScrolledText(logtab, height=20, bg="#0f0d16",
                                                  fg=TEXT, state="disabled",
                                                  wrap="word", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text.tag_config("INFO", foreground=MUTED)
        self.log_text.tag_config("SUCCESS", foreground=GOOD)
        self.log_text.tag_config("WARN", foreground=WARN)
        self.log_text.tag_config("ERROR", foreground=BAD)

        sett = ttk.Frame(nb, style="Purple.TFrame")
        nb.add(sett, text="Settings")
        self._settings_frame = ttk.Frame(sett, style="Purple.TFrame")
        self._settings_frame.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(self._settings_frame, text="(Edit config.json directly; a full settings editor is on the roadmap.)",
                 bg=BG, fg=MUTED).pack(anchor="w")

        about = ttk.Frame(nb, style="Purple.TFrame")
        nb.add(about, text="About")
        tk.Label(about, text=f"{_name} V{_ver}", font=("Segoe UI", 16, "bold"),
                 bg=BG, fg=PURPLE_SOFT).pack(anchor="w", padx=16, pady=16)
        tk.Label(about, text="Local AI via Ollama. Background mode, rotation, dashboard, tunnel, auto-update.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10), wraplength=600,
                 justify="left").pack(anchor="w", padx=16)

        try:
            open(LOG_FILE, "a", encoding="utf-8").close()
        except Exception:
            pass

    def set_close_handler(self, cb):
        self._on_close_cb = cb

    def _on_close(self):
        if self._on_close_cb:
            try:
                self._on_close_cb()
            except Exception:
                pass
        else:
            self.destroy()

    def set_status(self, status):
        status = str(status).upper()
        def _apply():
            self.status_var.set(status)
            bg, fg = STATUS_COLORS.get(status, (SURFACE_2, TEXT))
            self.status_label.configure(bg=bg, fg=fg)
            try:
                self._ov_status.set(f"{STATUS_DOT.get(status, 'o')} {status}")
            except Exception:
                pass
            if self.overlay is not None:
                self.overlay.set_colors(bg, fg)
        try:
            self.after(0, _apply)
        except Exception:
            _apply()

    def attach_overlay(self, monitor_rect):
        try:
            if self.overlay is not None:
                try:
                    self.overlay.top.destroy()
                except Exception:
                    pass
            self.overlay = MonitorOverlay(self, self._ov_status, self._ov_activity,
                                          monitor_rect or {})
            cur = self.status_var.get().upper()
            bg, fg = STATUS_COLORS.get(cur, (SURFACE_2, TEXT))
            self.overlay.set_colors(bg, fg)
            return True
        except Exception:
            return False

    def set_activity(self, text):
        text = str(text)[:220]
        def _apply():
            try:
                self.activity_var.set(text)
            except Exception:
                pass
            try:
                self._ov_activity.set(text[:80])
            except Exception:
                pass
        try:
            self.after(0, _apply)
        except Exception:
            _apply()

    def log(self, msg, level="INFO"):
        level = str(level).upper()
        if level not in ("INFO", "SUCCESS", "WARN", "ERROR"):
            level = "INFO"
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}\n"
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        def _apply():
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line, level)
                self.log_text.see("end")
                lc = int(self.log_text.index("end-1c").split(".")[0])
                if lc > 2000:
                    self.log_text.delete("1.0", f"{lc-2000}.0")
                self.log_text.configure(state="disabled")
            except Exception:
                pass
        try:
            self.after(0, _apply)
        except Exception:
            pass

    def update_preview(self, pil_image):
        def _apply():
            try:
                img = pil_image.copy()
                img.thumbnail((680, 360))
                self._photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=self._photo, text="")
            except Exception as e:
                self.preview_label.configure(text=f"preview error: {e}")
        try:
            self.after(0, _apply)
        except Exception:
            pass


DebugWindow = DashboardWindow
