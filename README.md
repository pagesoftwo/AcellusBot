# AcellusBot V5

Autonomous Acellus homework assistant. Watches the Acellus window, solves
questions with AI vision (cloud or **fully local via Ollama**), and answers
multiple-choice, textbox, two-box, and labeled-option questions — without
touching your real mouse (background Win32 input + PrintWindow capture).

## Quick start (source)

1. Install Python 3.10+ (tick *Add to PATH*).
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`, add a key (`GEMINI_API_KEY`, free at
   [aistudio.google.com](https://aistudio.google.com)) — or go fully local:
   `setup_ollama.bat`, then set `llm_provider` to `ollama`.
4. `python main.py` → pick the Acellus window → Save → F8 to start.
   - F8 Start/Resume · F9 Pause · F10 Quit (run as admin if hotkeys fail).

## Quick start (exe)

Unzip `dist\AcellusBot\`, add your `.env`, run `AcellusBot.exe`. No Python
needed. Rebuild anytime with `build_exe.bat`.

## Highlights

- **Local AI**: Ollama backend (`llama3.2-vision`, `qwen2.5vl`, `llava`…)
  — zero API limits. See `setup_ollama.bat`.
- **Background operation**: real cursor never moves; window may stay covered
  (minimize the VM viewer all you want — keep the guest window restored).
- **Class rotation**: per-class time budgets, auto-switching, skip lists.
- **Smarts**: wrong-answer retry with correction hints, stuck-question Skip,
  dashboard idle handling, multi-key quota rotation.
- **Remote dashboard**: phone-friendly page on LAN (`:8080`) + optional
  Cloudflare Tunnel to your own domain.
- **Auto-update**: exe checks `update.json` on launch.

## Config

All settings live in `config.json` (created on first run) and are editable
in the app under the Settings tab: accuracy, speeds, cooldowns, rotation,
dashboard, tunnel, update channel.

## Layout

- `main.py` — automation loop, scheduler, wiring
- `solver.py` — vision backends (gemini/openai/ollama/…)
- `ollama.py` — local model management
- `clicker.py` / `bginput.py` — foreground/background input
- `capture.py` — mss/PrintWindow capture, work-area fit
- `ui.py` — purple AcellusBot V5 interface
- `updater.py` — auto-update checker/applier
- `web/` — remote dashboard site
- `version.py` — single source of truth for releases
