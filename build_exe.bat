@echo off
REM build_exe.bat - One-click build of AcellusBot.exe (run INSIDE the VM).
REM Installs Python deps + PyInstaller, then freezes main.py to a single
REM windowed exe in dist\. No Python needed on PCs that run the exe.
setlocal EnableExtensions

echo ============================================================
echo  AcellusBot - exe builder
echo ============================================================

REM ---- 1) find Python ----
set "PY="
where py >nul 2>nul
if %errorlevel%==0 ( set "PY=py -3" ) else (
  where python >nul 2>nul
  if %errorlevel%==0 ( set "PY=python" ) else (
    echo [FAIL] No Python found.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" during install^) or run:
    echo   winget install Python.Python.3.12
    pause
    exit /b 1
  )
)
echo [ok] Using: %PY%
%PY% --version

REM ---- 2) deps + PyInstaller (this is the "install dependencies" step) ----
echo.
echo [1/3] Installing dependencies from requirements.txt ...
%PY% -m pip install --upgrade pip
if %errorlevel% neq 0 ( echo [FAIL] pip upgrade failed. & pause & exit /b 1 )
%PY% -m pip install -r requirements.txt
if %errorlevel% neq 0 ( echo [FAIL] pip install failed. & pause & exit /b 1 )

echo.
echo [2/3] Installing PyInstaller ...
%PY% -m pip install pyinstaller
if %errorlevel% neq 0 ( echo [FAIL] PyInstaller install failed. & pause & exit /b 1 )

REM ---- 3) freeze ----
echo.
echo [3/3] Building AcellusBot.exe (takes a few minutes) ...
%PY% -m PyInstaller AcellusBot.spec --noconfirm --clean
if %errorlevel% neq 0 ( echo [FAIL] Build failed. & pause & exit /b 1 )

REM ---- 4) stage distributable (never touches .env - keys stay put) ----
if not exist "dist\AcellusBot" mkdir "dist\AcellusBot"
tasklist /FI "IMAGENAME eq AcellusBot.exe" 2>nul | find /I "AcellusBot.exe" >nul
if %errorlevel%==0 (
  echo.
  echo [WARN] AcellusBot.exe is RUNNING - cannot overwrite it.
  echo   Press F10 in the bot to quit, then re-run this script
  echo   ^(or manually copy dist\AcellusBot.exe over
  echo   dist\AcellusBot\AcellusBot.exe while it is stopped^).
  echo   Fresh binary kept at: dist\AcellusBot.exe
  pause
  exit /b 2
)
copy /y "dist\AcellusBot.exe" "dist\AcellusBot\" >nul
if exist ".env.example" copy /y ".env.example" "dist\AcellusBot\" >nul
if exist ".env" copy /y ".env" "dist\AcellusBot\" >nul
if exist "setup_ollama.bat" copy /y "setup_ollama.bat" "dist\AcellusBot\" >nul
if exist "web" xcopy /E /I /Y "web" "dist\AcellusBot\web" >nul
if exist "cloudflared.exe" copy /y "cloudflared.exe" "dist\AcellusBot\" >nul
if exist "config.json" copy /y "config.json" "dist\AcellusBot\" >nul

echo.
echo ============================================================
echo  BUILD OK: dist\AcellusBot\AcellusBot.exe
echo.
echo  To distribute: zip the dist\AcellusBot folder and share it.
echo  On the target PC:
echo    1. Unzip anywhere, double-click AcellusBot.exe
echo    2. Copy .env.example to .env and add GEMINI_API_KEY=...
echo       ^(free key: aistudio.google.com -^> Get API key^)
echo    3. Pick the Acellus window, press F8 to start
echo  Notes: no Python needed on target. If F8/F9/F10 hotkeys do
echo  nothing, right-click the exe -^> Run as administrator.
echo  Windows SmartScreen may warn on first run (unsigned exe).
echo ============================================================
pause

