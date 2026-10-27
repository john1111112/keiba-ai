@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title KEIBA AI - Production Acceptance Check
echo KEIBA AI - Unattended monitor go-live checklist
echo This check does not activate JRA-VAN, send Discord, or place bets.
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\check_release_readiness.py
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 scripts\check_release_readiness.py
    ) else (
        python scripts\check_release_readiness.py
    )
)
set "CHECK_RESULT=%ERRORLEVEL%"
echo.
if "%CHECK_RESULT%"=="0" (
    echo [PASS] Machine-verifiable requirements passed.
    echo [NEXT] Confirm phone push and reboot persistence manually.
) else (
    echo [PENDING] Read the NEXT items above for remaining prerequisites.
)
pause
exit /b %CHECK_RESULT%
