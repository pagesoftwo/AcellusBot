@echo off
REM setup_ollama.bat - One-click local AI setup for AcellusBot V5.
REM Installs Ollama, starts it, pulls the default vision model.
REM No API keys, no quotas, no cooldowns after this.
setlocal EnableExtensions

echo ============================================================
echo  AcellusBot V5 - local AI setup (Ollama)
echo ============================================================

where ollama >nul 2>nul
if %errorlevel% neq 0 (
  echo [1/3] Installing Ollama via winget ...
  winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
  if %errorlevel% neq 0 (
    echo [FAIL] winget install failed. Install manually from:
    echo   https://ollama.com/download/windows
    pause
    exit /b 1
  )
  echo NOTE: if 'ollama' is still unknown, CLOSE and REOPEN this terminal,
  echo then re-run setup_ollama.bat.
  where ollama >nul 2>nul
  if %errorlevel% neq 0 ( echo [WARN] reopen terminal and re-run. & pause & exit /b 2 )
) else (
  echo [1/3] Ollama already installed.
)

echo.
echo [2/3] Starting Ollama server ...
start "" /min ollama serve >nul 2>nul
timeout /t 4 /nobreak >nul

echo.
echo [3/3] Pulling vision model qwen2.5vl:7b (one-time, ~6 GB) ...
ollama pull qwen2.5vl:7b
if %errorlevel% neq 0 (
  echo [FAIL] pull failed. Check disk space and network, then retry:
  echo   ollama pull qwen2.5vl:7b
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  DONE. In config.json (or Settings tab) set:
echo    "llm_provider": "ollama",
echo    "model": "qwen2.5vl:7b"
echo  then restart AcellusBot. Lighter fallback: "llava:7b"
echo  (ollama pull llava:7b).
echo ============================================================
pause
