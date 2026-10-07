"""solver.py - Gemini vision solver for AcellusAuto (via OpenAI-compatible endpoint).

solve(pil_image) -> dict:
  {"state","header","question","choices","answer_letter","confidence","reasoning"}
Only uses: openai, python-dotenv, Pillow (+ stdlib).
Uses GEMINI_API_KEY from .env via:
  base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
Falls back to OPENAI_API_KEY if GEMINI key missing.
Retries 3x with exponential backoff. Never raises (returns low-confidence dict).
"""

import base64
import io
import json
import os
import time

from dotenv import load_dotenv

load_dotenv()  # loads GEMINI_API_KEY / OPENAI_API_KEY from .env

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
# 2.x models 404 for new keys (Google limits 2.5 to past users).
# Auto-migrate them to 3.5-flash.
DEPRECATED_TO_CURRENT = {
    "gemini-2.0-flash": "gemini-3.5-flash",
    "gemini-2.5-flash": "gemini-3.5-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-flash-latest": "gemini-3.5-flash",
}

_client = None
_client_key = None  # (api_key, base_url) to detect provider switch
_prompt_cache = None


def _load_prompt():
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    import paths as _paths
    path = _paths.resource("vision_prompt.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _prompt_cache = f.read().strip()
            return _prompt_cache
    except Exception:
        pass
    _prompt_cache = (
        'Analyze this Acellus screenshot. Classify state as "video" '
        '(player controls visible, no radio buttons), "question" (A/B/C/D bubbles '
        'or input field visible), or "other".\n'
        'If question: read the HEADER text at the top of the content area, use it '
        'as topic context. Transcribe the full question and all answer choices. '
        'Solve it using the header context. Pick the correct letter. '
        'Estimate confidence 0-1.\n'
        'Return STRICT JSON ONLY:\n'
        '{"state":"video|question|other","header":"str or null",'
        '"question":"str or null","choices":{"A":"","B":"","C":"","D":""},'
        '"answer_letter":"A|B|C|D|null","confidence":0.0,'
        '"reasoning":"1-2 sentences"}'
    )
    return _prompt_cache


ZEN_BASE_URL = "https://opencode.ai/zen/v1"
# NOTE: base is the API ROOT only - the openai client appends
# /chat/completions itself. (Full path here doubled it -> HTML 404.)
GITHUB_BASE_URL = "https://models.github.ai/inference"
# GitHub Models: RETIRED (410 github_models_retirement_brownout as of
# 2026-09). Kept as stub only - do not select llm_provider=github.
# Token: github.com/settings/tokens (classic, no scopes) -> .env GITHUB_TOKEN=...
DEFAULT_GITHUB_MODEL = "openai/gpt-4o"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Groq: free tier, NO card, OpenAI-compatible vision.
# Key: console.groq.com -> API keys -> .env GROQ_API_KEY=...
DEFAULT_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
# Ollama: fully local, OpenAI-compatible at 127.0.0.1:11434/v1.
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_DUMMY_KEY = "ollama"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
# Free vision-capable model on Zen (OpenAI-compatible chat/completions).
# muse-spark-1.3-contributor-free is Free but lives on the /responses
# endpoint (not chat/completions), so it can't drop into this solver.
# This one can and is also Free.
DEFAULT_ZEN_MODEL = "deepseek-v4-flash-vision-exp"


# Models that do NOT speak OpenAI chat/completions (they live on /responses).
# Silently remap to the free vision chat model so config typos don't 404.
CHAT_INCOMPATIBLE = (
    "muse-spark-1.3-contributor-free",
    "muse-spark-1.3",
    "muse-spark-1.2",
)


def _chat_model(req):
    r = (req or "").strip()
    low = r.lower()
    if any(k in low for k in CHAT_INCOMPATIBLE):
        return DEFAULT_ZEN_MODEL
    return r or DEFAULT_ZEN_MODEL


def _zen_model_from_config():
    try:
        import paths as _paths
        with open(_paths.config_path(), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if str(cfg.get("llm_provider", "")).lower() == "zen":
            m = (cfg.get("model") or "").strip()
            if m:
                return m
    except Exception:
        pass
    return None


_key_rr = 0  # round-robin cursor across Gemini keys (spreads quota load)


def _gemini_keys():
    """All configured Gemini keys: GEMINI_API_KEY, GEMINI_API_KEY2..9.

    Extra keys = extra free quota (one per Google account, all free from
    aistudio.google.com). Deduped, order-stable. Never log the values.
    """
    out = []
    names = ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY{i}" for i in range(2, 10)]
    for name in names:
        try:
            v = (os.getenv(name) or "").strip().strip('"').strip("'")
        except Exception:
            v = ""
        if v and v not in out:
            out.append(v)
    return out


def _gemini_key_count():
    try:
        return len(_gemini_keys())
    except Exception:
        return 0


def _resolve_auth(requested_model=None):
    """Pick (api_key, base_url, model, provider).

    Priority: explicit zen config > Gemini > OpenAI.
    Zen (OpenCode) free vision avoids Gemini 5 RPM / 20 RPD caps.
    """
    gem_key = os.getenv("GEMINI_API_KEY")
    oai_key = os.getenv("OPENAI_API_KEY")
    gh_key = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    groq_key = os.getenv("GROQ_API_KEY")
    zen_key = os.getenv("OPENCODE_ZEN_API_KEY") or os.getenv("OPENCODE_API_KEY")
    req = (requested_model or "").strip()

    # 0) GitHub Models selected -> free GPT-4o vision, no card, no CLI.
    try:
        import paths as _paths0
        with open(_paths0.config_path(), "r", encoding="utf-8") as _f:
            _prov0 = str(json.load(_f).get("llm_provider", "")).lower()
    except Exception:
        _prov0 = ""
    if _prov0 == "github" or req.lower() in ("openai/gpt-4o", "openai/gpt-4o-mini",
                                             "github/gpt-4o"):
        if not gh_key:
            raise RuntimeError(
                "llm_provider=github but GITHUB_TOKEN missing. Create one at "
                "github.com/settings/tokens (classic, no scopes) and add to .env as "
                "GITHUB_TOKEN=... "
            )
        _gm = req if req.lower().startswith("openai/") else DEFAULT_GITHUB_MODEL
        return gh_key, GITHUB_BASE_URL, _gm, "github"

    # 1) Groq selected in config.json -> free, no card, vision over HTTPS.
    # (CLI opencode/muse-spark is text-only: "can't analyze images".)
    try:
        import paths as _paths1
        _prov1 = str(json.load(open(_paths1.config_path(),
                                    encoding="utf-8")).get("llm_provider", "")).lower()
    except Exception:
        _prov1 = ""
    if _prov1 == "groq" or req.lower() in ("meta-llama/llama-4-scout-17b-16e-instruct",
                                           "meta-llama/llama-4-maverick-17b-128e-instruct"):
        if not groq_key:
            raise RuntimeError(
                "llm_provider=groq but GROQ_API_KEY missing. Free, no card: "
                "console.groq.com -> API Keys -> add to .env as GROQ_API_KEY=... "
            )
        return groq_key, GROQ_BASE_URL, req or DEFAULT_GROQ_MODEL, "groq"

    # 1b) Ollama selected -> fully local, no key, no quota, no cooldowns.
    # Model auto-detect also routes bare local names (llava, qwen2.5vl...).
    try:
        _hereO = os.path.dirname(os.path.abspath(__file__))
        _provO = str(json.load(open(os.path.join(_hereO, "config.json"),
                                    encoding="utf-8")).get("llm_provider", "")).lower()
    except Exception:
        _provO = ""
    try:
        import ollama as _ol
        _looks_local = _ol.looks_like_ollama_model(req)
    except Exception:
        _looks_local = False
    if _provO == "ollama" or _looks_local or req == DEFAULT_OLLAMA_MODEL:
        return OLLAMA_DUMMY_KEY, OLLAMA_BASE_URL, \
            req or DEFAULT_OLLAMA_MODEL, "ollama"

    # 2) Zen selected in config.json -> use it even if Gemini key exists.
    # Direct HTTPS (no CLI agent, no tool-loops, no cold boot).
    zen_cfg_model = _zen_model_from_config()
    if zen_cfg_model or (req and req in (DEFAULT_ZEN_MODEL, "mimo-v2.5-free",
                                         "big-pickle", "nemotron-3-ultra-free")):
        if not zen_key:
            raise RuntimeError(
                "llm_provider=zen but OPENCODE_ZEN_API_KEY missing. "
                "Copy it from opencode.ai/auth, add to .env as "
                "OPENCODE_ZEN_API_KEY=... "
            )
        return zen_key, ZEN_BASE_URL, _chat_model(zen_cfg_model or req), "zen"

    if not req:
        req = DEFAULT_GEMINI_MODEL

    # 2) Gemini (OpenAI-compat endpoint).
    # Auto-correct gpt-* and deprecated gemini-2.x names to current model.
    # Returns the round-robin key; solve() tries the rest on 429.
    _gkeys = _gemini_keys()
    if _gkeys:
        model = DEPRECATED_TO_CURRENT.get(req, req)
        if model.startswith("gpt-") or not model:
            model = DEFAULT_GEMINI_MODEL
        # allow gemini-3.5-flash, gemini-3.6-flash, gemini-3.1-flash-lite, etc.
        return _gkeys[0], GEMINI_BASE_URL, model, "gemini"

    # 3) Zen via env even without config (free vision, no Gemini RPM).
    if zen_key:
        return zen_key, ZEN_BASE_URL, _chat_model(req), "zen"

    if oai_key:
        return oai_key, None, req, "openai"

    raise RuntimeError(
        "No API key found. Create .env with GEMINI_API_KEY=... "
        "(or OPENCODE_ZEN_API_KEY=... for free Zen vision, or OPENAI_API_KEY=sk-...)"
    )


def _get_client(api_key, base_url):
    global _client, _client_key
    cache_id = (api_key, base_url)
    if _client is not None and _client_key == cache_id:
        return _client
    from openai import OpenAI
    if base_url:
        _client = OpenAI(api_key=api_key, base_url=base_url)
    else:
        _client = OpenAI(api_key=api_key)
    _client_key = cache_id
    return _client


def _image_to_data_url(image, max_size=1280, quality=80):
    """Resize + JPEG base64 data URL to keep API payload small."""
    img = image.convert("RGB")
    w, h = img.size
    scale = min(1.0, max_size / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _safe_parse(text):
    """Robust JSON extraction. Tries strict parse, then repairs/truncation fallback."""
    import re
    t = (text or "").strip()
    # 1) strip markdown fences ```json ... ```
    if "```" in t:
        # keep content inside first fence pair if present
        m = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL | re.IGNORECASE)
        if m:
            t = m.group(1).strip()
        elif t.startswith("```"):
            lines = t.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines).strip()
    # 2) extract outermost {...} (drop preamble like "Here is JSON:")
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1 and e > s:
        t = t[s:e + 1]
    # 3) strict try
    try:
        return json.loads(t)
    except Exception:
        pass
    # 4) sanitize: remove literal control chars inside strings, fix trailing commas
    try:
        cleaned = re.sub(r",\s*([}\]])", r"\1", t)
        return json.loads(cleaned)
    except Exception:
        pass
    # 5) truncated-JSON fallback: regex-extract the fields we need.
    # Lets a cut-off response still yield state/letter/confidence.
    def _grab_str(key):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)', t)
        if m:
            return m.group(1)[:500]
        m = re.search(rf'"{key}"\s*:\s*null', t)
        return None
    def _grab_letter():
        m = re.search(r'"answer_letter"\s*:\s*"([A-Da-d])', t)
        return m.group(1).upper() if m else None
    def _grab_conf():
        m = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', t)
        try:
            return float(m.group(1)) if m else 0.0
        except Exception:
            return 0.0
    m_state = re.search(r'"state"\s*:\s*"(\w+)', t)
    state = m_state.group(1).lower() if m_state else "other"
    if state not in ("video", "question", "finished", "review", "other"):
        state = "other"
    choices = {}
    for k in ("A", "B", "C", "D"):
        # choices may be nested; search "A": "text (possibly unterminated)
        m = re.search(rf'"{k}"\s*:\s*"([^"]*)', t)
        choices[k] = m.group(1)[:200] if m else ""
    partial = {
        "state": state,
        "question_type": _grab_str("question_type"),
        "header": _grab_str("header"),
        "question": _grab_str("question"),
        "choices": choices,
        "answer_letter": _grab_letter(),
        "answer_text": _grab_str("answer_text"),
        "answer_text2": _grab_str("answer_text2"),
        "click_x": None,
        "click_y": None,
        "text_x": None,
        "text_y": None,
        "text2_x": None,
        "text2_y": None,
        "confidence": _grab_conf(),
        "reasoning": (_grab_str("reasoning") or "partial parse of truncated response")[:500],
    }
    # try to grab coords even in truncated output
    try:
        for _k in ("click_x", "click_y", "text_x", "text_y", "text2_x", "text2_y",
                   "gear_x", "gear_y",
                   "submit_x", "submit_y", "skip_x", "skip_y",
                   "back_x", "back_y",
                   "continue_x", "continue_y"):
            _m = re.search(rf'"{_k}"\s*:\s*([0-9]+)', t)
            if _m:
                partial[_k] = max(0, min(1000, int(_m.group(1))))
    except Exception:
        pass
    # try to grab dashboard classes even in truncated output
    try:
        _cl = []
        for _m in re.finditer(
                r'"name"\s*:\s*"([^"]{1,60})"\s*,\s*"x"\s*:\s*([0-9]+)\s*,\s*"y"\s*:\s*([0-9]+)', t):
            _cl.append({"name": _m.group(1).strip()[:60],
                        "x": max(0, min(1000, int(_m.group(2)))),
                        "y": max(0, min(1000, int(_m.group(3))))})
            if len(_cl) >= 10:
                break
        partial["classes"] = _cl
    except Exception:
        partial.setdefault("classes", [])
    # defaults for new keys
    for _k in ("gear_x", "gear_y", "click_x", "click_y", "text_x", "text_y",
               "text2_x", "text2_y",
               "submit_x", "submit_y", "skip_x", "skip_y", "back_x", "back_y",
               "continue_x", "continue_y"):
        partial.setdefault(_k, None)
    partial.setdefault("classes", [])
    # If we got at least a state, accept partial; else re-raise original error
    if state in ("video", "question", "finished", "review", "other") and ("answer_letter" in t or "state" in t):
        return partial
    raise json.JSONDecodeError("no JSON object found", t, 0)


def _normalize(data):
    """Force expected schema with safe defaults."""
    out = {
        "state": "other",
        "question_type": None,
        "header": None,
        "question": None,
        "choices": {"A": "", "B": "", "C": "", "D": ""},
        "answer_letter": None,
        "answer_text": None,
        "answer_text2": None,
        "classes": [],
        "click_x": None,
        "click_y": None,
        "text_x": None,
        "text_y": None,
        "text2_x": None,
        "text2_y": None,
        "submit_x": None,
        "submit_y": None,
        "skip_x": None,
        "skip_y": None,
        "back_x": None,
        "back_y": None,
        "gear_x": None,
        "gear_y": None,
        "continue_x": None,
        "continue_y": None,
        "confidence": 0.0,
        "reasoning": "",
    }
    if not isinstance(data, dict):
        return out
    state = str(data.get("state", "other")).lower().strip()
    if state not in ("video", "question", "finished", "review", "other"):
        state = "other"
    out["state"] = state
    out["header"] = data.get("header")
    out["question"] = data.get("question")
    ch = data.get("choices") or {}
    if isinstance(ch, dict):
        for k in ("A", "B", "C", "D"):
            if k in ch and ch[k] is not None:
                out["choices"][k] = str(ch[k])
    letter = data.get("answer_letter")
    if isinstance(letter, str):
        letter = letter.strip().upper()[:1]
        out["answer_letter"] = letter if letter in ("A", "B", "C", "D") else None
    # short text answer for green-box / input questions, e.g. "9", "-3"
    at = data.get("answer_text")
    if isinstance(at, (str, int, float)) and str(at).strip().lower() != "null":
        out["answer_text"] = str(at).strip()[:100]
    else:
        out["answer_text"] = None
    # second box (ordered pairs etc.), e.g. answer_text "-1" + answer_text2 "4"
    at2 = data.get("answer_text2")
    if isinstance(at2, (str, int, float)) and str(at2).strip().lower() != "null":
        out["answer_text2"] = str(at2).strip()[:100]
    else:
        out["answer_text2"] = None
    # Safety net: model combined both values ("-1, 4" / "(-1,4)") - split
    # into two boxes when the question looks like it has two blanks.
    if out["answer_text2"] is None and out["answer_text"]:
        import re as _re
        _m = _re.match(r"^\s*\(?\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*\)?\s*$",
                       out["answer_text"])
        if _m:
            _qq = str(out.get("question") or "").lower()
            if (data.get("text2_x") is not None or _qq.count("[?]") >= 2
                    or "ordered pair" in _qq or "pair" in _qq
                    or _qq.count("[]") >= 2):
                out["answer_text"], out["answer_text2"] = _m.group(1), _m.group(2)
    # question_type: choice | text | null (infer when missing)
    qt = data.get("question_type")
    if isinstance(qt, str) and qt.strip().lower() in ("choice", "text"):
        out["question_type"] = qt.strip().lower()
    else:
        if out["answer_text"]:
            out["question_type"] = "text"
        elif out["answer_letter"]:
            out["question_type"] = "choice"
        else:
            out["question_type"] = None
    # vision coords 0-1000 (answer radio / textbox / gear icon / Continue button)
    for key in ("click_x", "click_y", "text_x", "text_y", "text2_x", "text2_y",
                "gear_x", "gear_y",
                "submit_x", "submit_y", "skip_x", "skip_y", "back_x", "back_y",
                "continue_x", "continue_y"):
        try:
            v = data.get(key)
            if v is None:
                out[key] = None
            else:
                out[key] = max(0, min(1000, int(float(v))))
        except Exception:
            out[key] = None
    # dashboard course cards: [{name, x, y}] visual order, max 10
    try:
        _raw_cl = data.get("classes") or []
        _cl = []
        if isinstance(_raw_cl, list):
            for _c in _raw_cl[:10]:
                if not isinstance(_c, dict):
                    continue
                _nm = str(_c.get("name") or "").strip()[:60]
                try:
                    _cx = max(0, min(1000, int(float(_c.get("x")))))
                    _cy = max(0, min(1000, int(float(_c.get("y")))))
                except Exception:
                    continue
                if _nm:
                    _cl.append({"name": _nm, "x": _cx, "y": _cy})
        out["classes"] = _cl
    except Exception:
        out["classes"] = []
    # Labeled-text options (a) positive / b) negative) with a text entry
    # bar: models sometimes return these as choice letters with no click
    # target. Clicking is impossible then, so type the label instead.
    if (out["question_type"] == "choice" and out["answer_letter"]
            and out["click_x"] is None and out["click_y"] is None
            and not out["answer_text"]
            and (out["text_x"] is not None or out["text_y"] is not None)):
        out["answer_text"] = out["answer_letter"].lower()
        out["answer_letter"] = None
        out["question_type"] = "text"
    try:
        out["confidence"] = float(data.get("confidence", 0.0))
    except Exception:
        out["confidence"] = 0.0
    out["confidence"] = max(0.0, min(1.0, out["confidence"]))
    if data.get("reasoning"):
        out["reasoning"] = str(data["reasoning"])[:500]
    return out


def _is_opencode_model(model):
    m = (model or "").strip().lower()
    if m.startswith("opencode/"):
        return True
    try:
        import paths as _paths2
        with open(_paths2.config_path(), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return str(cfg.get("llm_provider", "")).lower() == "opencode"
    except Exception:
        return False


def solve(pil_image, model=None, timeout=60, hint=None):
    """Send screenshot to LLM, return normalized dict.

    Routes to local `opencode` CLI (Muse Spark free, no quota) when
    model startswith "opencode/" or config llm_provider=="opencode".
    Otherwise uses Gemini/Zen/OpenAI HTTP endpoints.
    Args:
        pil_image: PIL.Image screenshot
        model: e.g. "opencode/muse-spark-1.3-contributor-free". None = config.
        hint: optional extra instruction appended to the vision prompt,
            e.g. "your previous answer X was wrong, give a different one".
    Never raises: on total failure returns other/0-confidence dict.
    """
    # Auto-read model from config.json if caller passes None
    if not model:
        try:
            import paths as _paths3
            with open(_paths3.config_path(), "r", encoding="utf-8") as f:
                model = json.load(f).get("model") or DEFAULT_GEMINI_MODEL
        except Exception:
            model = DEFAULT_GEMINI_MODEL

    prompt = _load_prompt()
    if hint:
        prompt = prompt + "\nIMPORTANT CORRECTION: " + str(hint).strip()[:300]

    # ---- local opencode path: no API keys, no HTTP quota ----
    if _is_opencode_model(model):
        try:
            import solver_opencode as _oo
        except Exception as e:
            return {
                "state": "other", "question_type": None, "header": None, "question": None,
                "choices": {"A": "", "B": "", "C": "", "D": ""},
                "answer_letter": None, "answer_text": None, "answer_text2": None,
                "classes": [],
                "click_x": None, "click_y": None, "text_x": None, "text_y": None,
                "text2_x": None, "text2_y": None,
                "gear_x": None, "gear_y": None, "submit_x": None, "submit_y": None,
                "skip_x": None, "skip_y": None, "back_x": None, "back_y": None,
                "continue_x": None, "continue_y": None, "confidence": 0.0,
                "reasoning": f"opencode backend import failed: {e}",
            }
        eff = (model or "").strip() or "opencode/muse-spark-1.3-contributor-free"
        # Single attempt: one CLI run already takes 30-120s (cold boot +
        # free-tier queue). Retrying here piled up to 6+ min of "Asking AI...".
        # The main loop re-captures fresh screenshots on its own interval.
        # 100s cap: free models either answer by then or never do.
        # Blind-vision flake (free mimo sometimes answers blind): one instant
        # retry usually lands on a working backend.
        text, err = _oo.solve_opencode(pil_image, prompt, model=eff, timeout=100)
        if text and _oo.looks_blind(text):
            time.sleep(2)
            text2, err2 = _oo.solve_opencode(pil_image, prompt, model=eff, timeout=100)
            if text2 and not _oo.looks_blind(text2):
                text, err = text2, err2
        if text:
            try:
                return _normalize(_safe_parse(text))
            except Exception as e:
                import traceback as _tb
                last_err = (f"{type(e).__name__}: {e} | raw[:120]={text[:120]!r} | "
                            f"tb[:300]={_tb.format_exc(limit=2)[-300:]}")
        else:
            last_err = err or "empty response"
        return {
            "state": "other", "question_type": None, "header": None, "question": None,
            "choices": {"A": "", "B": "", "C": "", "D": ""},
            "answer_letter": None, "answer_text": None, "answer_text2": None,
            "classes": [],
            "click_x": None, "click_y": None, "text_x": None, "text_y": None,
            "text2_x": None, "text2_y": None,
            "submit_x": None, "submit_y": None, "skip_x": None, "skip_y": None,
            "back_x": None, "back_y": None,
            "gear_x": None, "gear_y": None,
            "continue_x": None, "continue_y": None, "confidence": 0.0,
            "reasoning": f"opencode failed: {(last_err or '')[:600]}",
        }

    data_url = _image_to_data_url(pil_image)
    last_err = "unknown"
    last_raw = ""

    def _single_request(api_key, base_url, eff_model):
        """One HTTPS attempt. Returns parsed dict, raises on any failure."""
        nonlocal last_raw
        client = _get_client(api_key, base_url)
        create_kwargs = dict(
            model=eff_model,
            timeout=timeout,
            temperature=0,
            max_tokens=1500,  # was 800: truncated long Q/A JSON mid-string
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        # Force JSON mode when supported (Gemini OpenAI-compat supports it).
        # Fall back silently if endpoint rejects the param - but NOT on
        # quota errors (retrying those just burns more quota).
        try:
            resp = client.chat.completions.create(
                **create_kwargs, response_format={"type": "json_object"}
            )
        except Exception as e_json:
            _el = str(e_json).lower()
            if "429" in _el or "quota" in _el or "ratelimit" in _el:
                raise
            resp = client.chat.completions.create(**create_kwargs)
        text = resp.choices[0].message.content or ""
        last_raw = text
        # Detect server-side truncation (finish_reason=length) and retry
        try:
            if getattr(resp.choices[0], "finish_reason", None) == "length":
                raise ValueError("truncated by max_tokens, retrying")
        except ValueError:
            raise
        except Exception:
            pass
        return _normalize(_safe_parse(text))

    for attempt in range(3):
        try:
            api_key, base_url, eff_model, provider = _resolve_auth(model)
            if provider == "gemini":
                keys = _gemini_keys() or [api_key]
                if len(keys) == 1:
                    return _single_request(keys[0], base_url, eff_model)
                # Multi-key: round-robin start, try each key once. A 429'd
                # key is skipped for the next key after a short pause; other
                # errors raise immediately (bad key/model, not quota).
                global _key_rr
                order = [keys[(_key_rr + i) % len(keys)]
                         for i in range(len(keys))]
                _key_rr += 1
                key_err = None
                for ki, k in enumerate(order):
                    try:
                        return _single_request(k, base_url, eff_model)
                    except Exception as e:
                        kn = keys.index(k) + 1
                        key_err = (f"[key{kn}/{len(keys)}] "
                                   f"{type(e).__name__}: {e}")
                        _el = str(e).lower()
                        if (("429" in _el or "quota" in _el or "ratelimit" in _el)
                                and ki < len(order) - 1):
                            time.sleep(3)  # brief pause, try next key
                            continue
                        raise
                raise RuntimeError(key_err or "all gemini keys failed")
            return _single_request(api_key, base_url, eff_model)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if last_raw:
                last_err += f" | raw[:120]={last_raw[:120]!r}"
            if attempt < 2:
                # 429 = free-tier quota exhausted: hammering retries makes it
                # worse. Back off hard so the next loop tick has quota again.
                _el = last_err.lower()
                if "429" in _el or "quota" in _el or "ratelimit" in _el:
                    time.sleep(30 + attempt * 15)  # 30s, 45s
                else:
                    time.sleep(2 ** (attempt + 1))  # 2, 4
            continue
    return {
        "state": "other",
        "question_type": None,
        "header": None,
        "question": None,
        "choices": {"A": "", "B": "", "C": "", "D": ""},
        "answer_letter": None,
        "answer_text": None,
        "answer_text2": None,
        "classes": [],
        "click_x": None,
        "click_y": None,
        "text_x": None,
        "text_y": None,
        "text2_x": None,
        "text2_y": None,
        "submit_x": None,
        "submit_y": None,
        "skip_x": None,
        "skip_y": None,
        "back_x": None,
        "back_y": None,
        "gear_x": None,
        "gear_y": None,
        "continue_x": None,
        "continue_y": None,
        "confidence": 0.0,
        "reasoning": f"API failed after 3 retries: {last_err[:300]}",
    }
