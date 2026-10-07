"""ollama.py - Local LLM management for AcellusBot V5 (stdlib only).

Ollama runs models on your own PC: no API keys, no quotas, no cooldowns.
Vision-capable models (any is fine - solver sends OpenAI-style vision
requests to http://127.0.0.1:11434/v1):

  qwen2.5vl:7b        (default, best accuracy/size balance)
  llava:7b            (light fallback)
  llama3.2-vision:11b (heavier, sharper)

Setup is one step: run setup_ollama.bat (installs Ollama, pulls the model).
The bot also self-heals at startup: starts `ollama serve` if needed and
pulls the configured model in the background while it keeps working.
"""

import json
import shutil
import subprocess
import threading
import urllib.request

HOST = "http://127.0.0.1:11434"
BASE_URL = HOST + "/v1"
DEFAULT_MODEL = "qwen2.5vl:7b"
FALLBACK_MODEL = "llava:7b"

# substrings identifying vision-capable local models (for auto-detect)
VISION_HINTS = ("vl", "vision", "llava", "moondream", "minicpm",
                "gemma3", "bakllava", "qwen2-vl", "qwen2.5vl")


def looks_like_ollama_model(name):
    """True if a model name looks like a local Ollama model."""
    low = (name or "").strip().lower()
    if not low or "/" in low or low.startswith(("gpt-", "gemini-",
                                                "Muse-", "deepseek-")):
        return False
    return (":" in low or
            any(h in low for h in VISION_HINTS + ("llama", "mistral",
                                                 "phi", "gemma", "qwen")))


def is_installed():
    return shutil.which("ollama") is not None


def server_up(timeout_s=3):
    try:
        with urllib.request.urlopen(HOST + "/api/tags",
                                    timeout=timeout_s) as r:
            return r.status == 200
    except Exception:
        return False


def _popen_hidden(cmd):
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    except Exception:
        si = None
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, startupinfo=si)


def ensure_server(timeout_s=20):
    """Start `ollama serve` if needed. Returns True if API answers."""
    import time as _t
    if server_up():
        return True
    if not is_installed():
        return False
    try:
        _popen_hidden(["ollama", "serve"])
    except Exception:
        return False
    end = _t.time() + timeout_s
    while _t.time() < end:
        if server_up(timeout_s=2):
            return True
        _t.sleep(1)
    return server_up(timeout_s=2)


def list_models():
    """Return [names] installed locally, [] on any failure."""
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode())
        return [m.get("name", "") for m in data.get("models", [])
                if m.get("name")]
    except Exception:
        return []


def has_model(name):
    want = (name or "").strip().lower()
    try:
        return any(want == m.lower() or m.lower().startswith(want + ":")
                   for m in list_models())
    except Exception:
        return False


def pull_model(name, log=None, timeout_s=1800):
    """Blocking `ollama pull`. Streams last status line to log callback."""
    def _log(m):
        try:
            if log:
                log(m)
        except Exception:
            pass

    _log(f"Pulling Ollama model '{name}' (one-time, several GB)...")
    try:
        proc = subprocess.Popen(
            ["ollama", "pull", name], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
    except Exception as e:
        _log(f"Pull failed to start: {e}")
        return False
    last = ""
    try:
        for line in proc.stdout:
            line = (line or "").strip()
            if line and line != last and ("%" in line or "pulling" in line.lower()
                                          or "success" in line.lower()):
                last = line
                _log(f"Ollama: {line[:120]}")
        proc.wait(timeout=timeout_s)
    except Exception as e:
        _log(f"Pull error: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return False
    ok = proc.returncode == 0 and has_model(name)
    _log(f"Model '{name}' {'ready.' if ok else 'NOT ready (see above).'}")
    return ok


def ensure_model(name, log=None, background=True):
    """Make sure a model exists; optionally pull in background. Returns bool
    (True if already present OR background pull started)."""
    if has_model(name):
        return True

    def _log(m):
        try:
            if log:
                log(m)
        except Exception:
            pass

    if not background:
        return pull_model(name, _log)
    _log(f"Model '{name}' not installed yet - downloading in background. "
         f"Bot keeps running meanwhile.")
    threading.Thread(target=pull_model, args=(name, _log),
                     daemon=True).start()
    return True


def setup_status(model):
    """One-shot diagnosis dict for preflight/logs. Never raises."""
    try:
        installed = is_installed()
        up = server_up() if installed else False
        models = list_models() if up else []
        want = (model or DEFAULT_MODEL).strip()
        ready = any(want.lower() == m.lower() for m in models)
        return {"installed": installed, "server_up": up, "models": models,
                "want": want, "ready": bool(ready)}
    except Exception as e:
        return {"installed": False, "server_up": False, "models": [],
                "want": model or DEFAULT_MODEL, "ready": False,
                "error": str(e)[:200]}
