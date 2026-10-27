@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title KEIBA AI - Install Auto Race Monitor

echo ================================================================
echo KEIBA AI - one-time setup for unattended all-race monitoring
echo ================================================================

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Install Python 3 first.
            pause
            exit /b 1
        )
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [ERROR] Python virtual environment creation failed.
        pause
        exit /b 1
    )
)

set "KEIBA_PYTHON=%CD%\.venv\Scripts\python.exe"
echo [1/5] Installing Python dependencies in project-only environment...
"%KEIBA_PYTHON%" -m pip install -r requirements.txt pywin32
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. No background monitor registered.
    pause
    exit /b 1
)

echo [2/5] Checking compatible 32-bit or 64-bit JV-Link COM (read-only)...
"%KEIBA_PYTHON%" -c "import sys; from src.jvlink_client import find_jvlink_python; sys.exit(0 if find_jvlink_python() else 1)"
if errorlevel 1 (
    echo [ERROR] JRA-VAN JV-Link COM is not installed for this Python.
    echo [NEXT] Install matching JV-Link 32/64-bit SDK and pywin32 in that Python.
    echo [INFO] A Data Lab trial was NOT started.
    pause
    exit /b 1
)

echo [3/5] Enabling personal Discord buy-candidate notifications...
"%KEIBA_PYTHON%" scripts\enable_personal_monitor.py
if errorlevel 1 (
    echo [ERROR] Notification configuration failed.
    pause
    exit /b 1
)

echo [4/5] Checking activation consent...
echo [IMPORTANT] Automatic monitoring starts JRA-VAN access immediately.
echo [IMPORTANT] If this is your first access, it may start a Data Lab trial.
choice /C YN /M "Authorize starting JRA-VAN automatic monitoring now?"
if errorlevel 2 (
    echo [PREPARED] Installation stopped before live JV-Link access.
    pause
    exit /b 0
)

echo [5/5] Registering one-minute Windows Task Scheduler monitor...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows_task.ps1" -Action register -IntervalMinutes 1
if errorlevel 1 (
    echo [ERROR] Scheduled task registration failed.
    pause
    exit /b 1
)

echo [OK] Background monitoring is registered. You do not need to open the app.
echo [INFO] For phone push configure Discord webhook and optionally your Discord user ID.
echo [INFO] JRA-VAN Data Lab SDK, registered access and a powered-on Windows PC are required.
echo [INFO] If using classic 32-bit JV-Link, install pywin32 in 32-bit Python separately.
pause
exit /b 0
