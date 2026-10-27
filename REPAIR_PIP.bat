@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title KEIBA AI - Repair pip and resume installation

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] No existing .venv. Running normal installer.
    call "%~dp0INSTALL_AUTO_MONITOR.bat"
    exit /b %ERRORLEVEL%
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [REPAIR] Installing missing pip into the existing .venv...
    "%VENV_PY%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo [WARN] ensurepip unavailable. Trying installed system pip...
        where py >nul 2>&1
        if not errorlevel 1 (
            py -3 -m pip --python "%VENV_PY%" install pip
        )
    )
)
"%VENV_PY%" -m pip --version
if errorlevel 1 (
    echo [ERROR] Could not repair pip automatically.
    echo [NEXT] Install official Python 3 with pip support, then rerun.
    pause
    exit /b 1
)
echo [OK] pip is available. Continuing KEIBA AI setup...
call "%~dp0INSTALL_AUTO_MONITOR.bat"
exit /b %ERRORLEVEL%
