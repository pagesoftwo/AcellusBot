"""updater.py - Auto-update checker/applier for AcellusBot V5."""

import json
import os
import shutil
import subprocess
import urllib.request

from version import VERSION, is_newer


def fetch_manifest(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_for_update(update_cfg):
    out = {"update": False, "latest_version": VERSION, "url": "",
           "notes": "", "error": None}
    try:
        if not update_cfg or not update_cfg.get("enabled", True):
            out["error"] = "updates disabled"
            return out
        url = (update_cfg.get("manifest_url") or "").strip()
        if not url or "<user>" in url:
            out["error"] = "manifest_url not set"
            return out
        data = fetch_manifest(url)
        latest = str(data.get("latest_version", "")).strip()
        out["latest_version"] = latest or VERSION
        out["url"] = str(data.get("url", "")).strip()
        out["notes"] = str(data.get("notes", ""))[:500]
        if latest and is_newer(latest, VERSION):
            out["update"] = True
        return out
    except Exception as e:
        out["error"] = str(e)[:200]
        return out


def download(url, dest_path, timeout=600):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r, \
                open(dest_path, "wb") as f:
            shutil.copyfileobj(r, f, length=1024 * 256)
        return os.path.getsize(dest_path) > 1024 * 1024
    except Exception:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        return False


def prepare_swap(update_cfg, current_exe, log=print):
    info = check_for_update(update_cfg)
    if not info.get("update") or not info.get("url"):
        return None
    log(f"Update available: V{info['latest_version']} "
        f"(current V{VERSION}). Downloading...")
    staged = current_exe + ".new"
    if download(info["url"], staged):
        log(f"Staged new build at {staged}. Restart to apply.")
        return staged
    log("Download failed.")
    return None


def apply_swap_on_next_start(current_exe, staged_exe, log=print):
    try:
        if not os.path.exists(staged_exe):
            return False
        bat = os.path.join(os.path.dirname(current_exe), "_apply_update.bat")
        lines = [
            "@echo off",
            "timeout /t 2 /nobreak >nul",
            f'if exist "{staged_exe}" (',
            f'  move /y "{staged_exe}" "{current_exe}" >nul',
            ")",
            f'start "" "{current_exe}"',
            f'del "%~f0"',
        ]
        with open(bat, "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines) + "\r\n")
        subprocess.Popen(["cmd", "/c", "start", "", bat], shell=False)
        return True
    except Exception as e:
        try:
            log(f"apply_swap failed: {e}")
        except Exception:
            pass
        return False
