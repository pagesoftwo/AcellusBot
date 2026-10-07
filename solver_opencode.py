"""solver_opencode.py - Local Opencode backend for AcellusAuto.

Uses the `opencode` CLI already on your machine (no Gemini/OpenAI quota):
  opencode run -m opencode/muse-spark-1.3-contributor-free -f shot.png "<prompt>"

Returns raw model text; caller (solver.py) parses STRICT JSON via _safe_parse.
Only stdlib + Pillow. Never raises (returns None text on failure, caller retries).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile


def is_available():
    """True if `opencode` binary is on PATH."""
    return shutil.which("opencode") is not None


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")


def _clean(s, limit=2000):
    """Strip ANSI/color codes + control chars, collapse whitespace."""
    s = _ANSI.sub("", s or "")
    s = s.replace("\x00", "").strip()
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s[:limit]


def _error_line(s):
    """Extract the `Error: ...` line if present (the actionable part)."""
    m = re.search(r"Error\s*:\s*(.+)", s or "", re.DOTALL)
    return m.group(1).strip()[:500] if m else ""


def _collect_text_from_json_events(stdout):
    """Parse `opencode run --format json` event stream into assistant text.

    Event shape varies by version (step_start, message.part.delta with
    delta/snapshot/text keys, etc.). Walk every JSON line, collect model
    output strings; streaming deltas join with "", full parts with newline.
    Returns (text, saw_events).
    """
    deltas, parts = [], []
    saw = False
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
            saw = True
        except Exception:
            continue
        stack = [ev]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                for k, v in cur.items():
                    if isinstance(v, str) and v.strip() and len(v.strip()) > 1:
                        kl = str(k).lower()
                        if kl in ("delta",):
                            deltas.append(v)
                        elif kl in ("text", "content", "snapshot", "value",
                                    "output", "result", "answer", "data", "message"):
                            # skip obvious metadata (ids, types, timestamps)
                            vl = v.strip()
                            if kl == "message" and len(vl) < 40 and "{" not in vl:
                                continue
                            parts.append(v)
                        # else: ignore keys like type/sessionID/timestamp
                    elif isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)
    text = ("".join(deltas).strip() + "\n" + "\n".join(parts)).strip()
    # Deduplicate: if deltas already contain the full text, parts repeat it.
    # Prefer the longest coherent blob containing '{'.
    if deltas and parts:
        dj, pj = "".join(deltas).strip(), "\n".join(parts).strip()
        if dj and ("{-state-" in dj.replace(" ", "").replace('"', "") or '"state"' in dj):
            text = dj
        elif pj and '"state"' in pj:
            text = pj
    return (text, saw)


def _server_url():
    """Optional persistent server to skip per-call cold boot (see config)."""
    try:
        import paths as _paths
        with open(_paths.config_path(), "r", encoding="utf-8") as f:
            u = (json.load(f).get("opencode_server") or "").strip()
        return u or None
    except Exception:
        return None


def serve_up(timeout_s=0.6):
    """Quick TCP ping of the configured serve URL. No-op False if unset."""
    import socket as _s
    import urllib.parse as _u
    try:
        u = _server_url()
        if not u:
            return False
        p = _u.urlparse(u)
        host, port = (p.hostname or "127.0.0.1"), (p.port or 4096)
        with _s.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False
    return False


# Tracked so Pause/Quit (F9/F10) can kill a hung call instead of waiting out
# the full timeout. Guarded by GIL (assignment is atomic enough here).
_current_proc = None


def cancel_current():
    """Terminate the in-flight `opencode run`, if any. Never raises."""
    global _current_proc
    p = _current_proc
    if p is None:
        return
    try:
        p.kill()
    except Exception:
        pass


def _run_opencode(tmp, model, full_prompt, use_json_format, timeout, use_attach=True):
    """Single CLI invocation. Returns (stdout, stderr_tail, exit_code, launch_err)."""
    global _current_proc
    _exe = shutil.which("opencode") or "opencode"
    # NOTE: `-f/--file` is yargs `[array]` = greedy: anything positioned after
    # it (incl. the prompt) gets swallowed as another file ->
    # "Error: File not found: <prompt>". So message FIRST, -f LAST.
    base = [_exe, "run", full_prompt, "-m", model, "-f", tmp]
    if use_json_format:
        base += ["--format", "json"]
    srv = _server_url() if use_attach else None
    if srv:
        base += ["--attach", srv]  # reuse warm server, skip MCP cold boot
    # Scrub PATs: subprocess inherits os.environ (dotenv loaded GITHUB_TOKEN
    # into it). The CLI/SDK then offers the classic PAT as a "third-party
    # user token" where the endpoint demands OAuth ->
    # "Personal Access Tokens are not supported for this endpoint".
    # (Hand tests passed before GITHUB_TOKEN existed - same cause.)
    _scrub = {"GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT", "GH_PAT",
              "GITHUB_OAUTH_TOKEN", "GH_OAUTH_TOKEN"}
    _env = {k: v for k, v in os.environ.items() if k.upper() not in _scrub}
    # Windows npm shim is opencode.cmd: CreateProcess can't run .cmd directly,
    # must go through cmd.exe (was FileNotFoundError [WinError 2]).
    if os.name == "nt" and str(_exe).lower().endswith((".cmd", ".bat", ".ps1")):
        cmd = ["cmd", "/d", "/c", _exe] + base[1:]
    else:
        cmd = base
    try:
        import paths as _paths_cwd
        _cwd = _paths_cwd.app_dir()
    except Exception:
        _cwd = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_env,
            cwd=_cwd,
        )
        _current_proc = proc
        try:
            out, err = proc.communicate(timeout=timeout)
        finally:
            _current_proc = None
        return out or "", _clean(err), proc.returncode, None
    except subprocess.TimeoutExpired:
        cancel_current()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return out or "", _clean(err), -1, f"opencode timed out after {timeout}s (killed)"
    except Exception as e:
        _current_proc = None
        return "", "", -1, f"opencode launch failed: {type(e).__name__}: {e}"


def _has_state(text):
    return bool(text) and '"state"' in text


_BLIND_MARKERS = (
    "can't view image", "cannot view image", "can't see the image",
    "cannot see the image", "unable to analyze", "can't analyze",
    "doesn't support image", "not support image", "no image attached",
    "don't see an attached image", "can't view images", "cannot view images",
    "don't see a screenshot", "no screenshot attached", "screenshot attached",
    "attached to your message", "upload the image", "provide the screenshot",
    "send the image", "let me check if there's",
    "ocr",
)


def looks_blind(text):
    """True if the model admits blindness (free-tier vision flake).

    Retrying the call usually routes to a working backend. Only counts when
    it failed to produce an answer (no answer_letter present).
    """
    if not text or '"answer_letter"' in text:
        return False
    low = text.lower()
    return any(m in low for m in _BLIND_MARKERS)


def _is_attach_error(err):
    e = (err or "").lower()
    return any(k in e for k in ("econnrefused", "refused", "failed to connect",
                                "attach", "econnreset", "socket"))


def solve_opencode(pil_image, prompt, model="opencode/muse-spark-1.3-contributor-free",
                   timeout=180):
    """Run local opencode. Returns (text or None, error or None).

    Strategy: default (formatted) output first - it is the final message
    text our {..} extractor handles best. Fall back to --format json events.
    Saves screenshot to a temp png (cleaned up afterwards).
    """
    if not is_available():
        return None, "opencode binary not found on PATH. Install opencode and re-open terminal."
    # Save INSIDE the app dir (not %TEMP%) with cwd=app dir:
    # opencode scopes attached files to the project; %TEMP% attachments
    # arrive missing -> model says "I don't see an attached image."
    # Must be VISIBLE (no leading dot): hidden dotfiles are skipped by the
    # file pipeline, model then hunts the workspace instead of seeing the shot.
    # Small JPEG (not full-res PNG): big uploads intermittently never attach
    # ("I don't see a screenshot attached") on the free tier.
    try:
        import paths as _paths_shot
        HERE = _paths_shot.app_dir()
    except Exception:
        HERE = os.path.dirname(os.path.abspath(__file__))
    tmp = os.path.join(HERE, "acellus_last_shot.jpg")
    try:
        _shot = pil_image.convert("RGB")
        _shot.thumbnail((1280, 1280))
        _shot.save(tmp, format="JPEG", quality=70)
    except Exception as e:
        return None, f"screenshot save failed: {e}"

    # The screenshot is already attached; forbid the build agent's
    # explore-the-workspace reflex (it was reading .py files + running
    # `where opencode` instead of looking at the image).
    full_prompt = ((prompt or "").strip()
                   + "\nThe screenshot is already attached to this message. "
                     "Answer directly from it. Do NOT use tools, read/search "
                     "files, or run commands. Reply with STRICT JSON ONLY.")
    import time as _t
    t0 = _t.time()
    # Attach only when the warm server actually answers; a dead URL makes
    # the CLI hang on reconnect instead of failing fast.
    with_server = bool(_server_url()) and serve_up()

    def _attempt(use_json, attach):
        return _run_opencode(tmp, model, full_prompt, use_json, timeout,
                             use_attach=attach)

    try:
        # Attempt 1: default formatted output (final message text).
        out1, err1, code1, launch1 = _attempt(False, with_server)
        if launch1:
            return None, launch1
        # Server died mid-call? (attach refused) -> retry once WITHOUT attach.
        if _is_attach_error(err1) and _server_url():
            out1, err1, code1, launch1 = _attempt(False, False)
            if launch1:
                return None, launch1
        if _has_state(out1):
            return out1.strip(), None
        # Attempt 2 (json events) ONLY if attempt 1 failed fast - otherwise
        # we'd stack minutes of "Asking AI...". Slow = queued model, not format.
        if _t.time() - t0 < 60:
            out2, err2, code2, launch2 = _run_opencode(tmp, model, full_prompt, True, timeout,
                                                       use_attach=with_server)
            if not launch2:
                text2, _ = _collect_text_from_json_events(out2)
                if _has_state(text2):
                    return text2, None
            else:
                out2, err2, code2 = "", "", -1
        else:
            out2, err2, code2 = "", "", -1
        # Neither has state: surface the actionable error, not raw envelopes.
        err_line = _error_line(err2) or _error_line(err1)
        # Log which model actually ran (header like "> build · <model>").
        used = ""
        m = re.search(r">\s*\S+\s+·\s*([A-Za-z0-9._\-/]+)", (err1 or "") + "\n" + (err2 or ""))
        if m:
            used = f"ran-as={m.group(1)} "
        detail = err_line or f"default exit={code1} | json exit={code2}"
        return None, (f"{used}{detail} "
                      f"[default out[:80]={out1[:80]!r} json out[:80]={out2[:80]!r}]")
    finally:
        # acellus_last_shot.png intentionally KEPT (overwritten each call)
        # so you can open it and see exactly what the model saw.
        pass
