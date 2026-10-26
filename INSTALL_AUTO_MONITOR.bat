@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title KEIBA AI - Install Always-On Race Watch

echo ================================================================
echo KEIBA AI: Install automatic race monitoring without opening UI
echo ================================================================
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON=py"
    set "PY_ARGS=-3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python not found. Install Python and retry.
        pause
        exit /b 1
    )
    set "PYTHON=python"
    set "PY_ARGS="
)

rem Explicit installation enables Discord candidate alerts while keeping
rem JRA-VAN source verification, odds freshness, and zero-bet protection.
"%PYTHON%" %PY_ARGS% scripts\enable_personal_monitor.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Notification configuration failed.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows_task.ps1" -Action register -IntervalMinutes 1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Windows Task Scheduler registration failed.
    pause
    exit /b 1
)

echo [OK] Race monitoring starts automatically in Windows each minute.
echo [INFO] The Streamlit UI is not required for monitoring.
echo [INFO] For phone alerts, configure Discord webhook once in KEIBA AI.
echo [INFO] Your Windows PC must remain on; SDK and JRA-VAN access are required.
pause
exit /b 0
