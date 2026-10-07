"""tunnel.py - Self-managed Cloudflare Tunnel for AcellusAuto (stdlib only).

Modes (config "tunnel"):
  quick: no account needed. Runs `cloudflared tunnel --url ...`, parses the
         random https://xxx.trycloudflare.com URL from its output.
  named: uses YOUR domain (e.g. acellus.pagesoftwo.xyz). One-time manual
         step: Zero Trust dashboard -> Networks -> Tunnels -> Create ->
         copy the token -> paste as tunnel.token. Ingress for the hostname
         must point at http://localhost:<dashboard port> (set in the
         dashboard when creating the tunnel).

The bot downloads cloudflared.exe itself (if missing), launches it,
restarts it if it dies, and publishes the public URL to the dashboard.
"""

import os
import re
import shutil
import subprocess
import threading
import time

QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
DOWNLOAD_URL = ("https://github.com/cloudflare/cloudflared/releases/latest"
                "/download/cloudflared-windows-amd64.exe")

_state = {"proc": None, "url": None, "mode": None, "running": False,
          "stop": threading.Event(), "thread": None}


def _log_default(msg):
    print(f"[tunnel] {msg}")


def find_binary(explicit=""):
    """Locate cloudflared.exe: explicit path > app dir > PATH."""
    cands = []
    if explicit:
        cands.append(explicit)
    try:
        import paths as _paths
        cands.append(os.path.join(_paths.app_dir(), "cloudflared.exe"))
    except Exception:
        pass
    for c in cands:
        try:
            if c and os.path.isfile(c) and os.path.getsize(c) > 1024 * 1024:
                return c
        except Exception:
            continue
    try:
        w = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
        if w:
            return w
    except Exception:
        pass
    return None


def ensure_binary(explicit="", auto_download=True, log=_log_default):
    """Return cloudflared path, downloading it if allowed. None on failure."""
    found = find_binary(explicit)
    if found:
        return found
    if not auto_download:
        log("cloudflared.exe not found. Set tunnel.cloudflared_path or allow "
            "auto_download (or `winget install Cloudflare.cloudflared`).")
        return None
    try:
        import paths as _paths
        dest = os.path.join(_paths.app_dir(), "cloudflared.exe")
    except Exception:
        return None
    try:
        log(f"downloading cloudflared (~35MB) to {dest} ...")
        import urllib.request as _u
        _u.urlretrieve(DOWNLOAD_URL, dest)
        if os.path.getsize(dest) < 10 * 1024 * 1024:
            raise RuntimeError("download too small, likely an error page")
        log("cloudflared downloaded.")
        return dest
    except Exception as e:
        log(f"cloudflared download failed: {e}. Install manually: "
            "`winget install Cloudflare.cloudflared`.")
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except Exception:
            pass
        return None


def _build_cmd(binpath, mode, token, local_url):
    if mode == "quick":
        return [binpath, "tunnel", "--no-autoupdate", "--url", local_url]
    return [binpath, "tunnel", "--no-autoupdate", "run", "--token", token]


def _parse_url(line):
    try:
        m = QUICK_URL_RE.search(line or "")
        return m.group(0) if m else None
    except Exception:
        return None


def _popen_hidden(cmd):
    """Launch without a console popup (matters for windowed exe)."""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    except Exception:
        si = None
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True,
                            startupinfo=si)


def _supervise(binpath, mode, token, local_url, log):
    _state["running"] = True
    while not _state["stop"].is_set():
        _state["url"] = None
        _state["proc"] = None
        try:
            proc = _popen_hidden(_build_cmd(binpath, mode, token, local_url))
            _state["proc"] = proc
            log(f"tunnel process started (mode={mode}).")
            try:
                for line in proc.stderr:
                    if _state["stop"].is_set():
                        break
                    if mode == "quick" and _state["url"] is None:
                        u = _parse_url(line)
                        if u:
                            _state["url"] = u
                            log(f"public URL: {u}")
                    low = (line or "").lower()
                    if "failed" in low and ("cert" in low or "login" in low or
                                            "token" in low or "auth" in low):
                        log(f"tunnel auth problem: {line.strip()[:200]}")
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
        except Exception as e:
            log(f"tunnel launch failed: {e}")
        _state["proc"] = None
        if _state["stop"].is_set():
            break
        log("tunnel exited, retrying in 5s ...")
        _state["stop"].wait(5)
    _state["running"] = False


def start(cfg, local_port, log=_log_default):
    """Start supervised tunnel from config dict. Returns True if starting."""
    stop()
    cfg = cfg if isinstance(cfg, dict) else {}
    if not cfg.get("enabled", False):
        return False
    mode = str(cfg.get("mode", "named") or "named").lower()
    if mode not in ("quick", "named"):
        mode = "named"
    token = str(cfg.get("token") or os.getenv("TUNNEL_TOKEN") or
                os.getenv("CLOUDFLARED_TOKEN") or
                os.getenv("CLOUDFLARE_TOKEN") or "").strip()
    if mode == "named" and not token:
        log("tunnel.mode=named but tunnel.token is empty. Paste the token "
            "from Zero Trust -> Networks -> Tunnels -> your tunnel "
            "(or use mode=quick for a random URL, no account needed).")
        return False
    binpath = ensure_binary(str(cfg.get("cloudflared_path") or ""),
                            bool(cfg.get("auto_download", True)), log)
    if not binpath:
        return False
    local_url = f"http://127.0.0.1:{int(local_port)}"
    _state["stop"].clear()
    _state["mode"] = mode
    t = threading.Thread(target=_supervise,
                         args=(binpath, mode, token, local_url, log),
                         daemon=True)
    _state["thread"] = t
    t.start()
    return True


def public_url():
    return _state.get("url")


def is_running():
    return bool(_state.get("running"))


def stop():
    _state["stop"].set()
    p = _state.get("proc")
    if p is not None:
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    _state["proc"] = None
    _state["url"] = None
    _state["running"] = False
