# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AcellusBot V5 - single-file windowed exe.

Build with:  build_exe.bat   (or: pyinstaller AcellusBot.spec)
Output:      dist/AcellusBot.exe  (no Python needed on target PC)
First run:   exe creates config.json next to itself; user adds .env
             with GEMINI_API_KEY or runs setup_ollama.bat.
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('vision_prompt.txt', '.'), ('web', 'web'),
           ('config.json', '.'), ('setup_ollama.bat', '.'),
           ('.env.example', '.')],
    hiddenimports=[
        'mss',
        'pygetwindow',
        'pyautogui',
        'pyscreeze',
        'pytweening',
        'pymsgbox',
        'mouseinfo',
        'keyboard',
        'PIL',
        'openai',
        'dotenv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AcellusBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
