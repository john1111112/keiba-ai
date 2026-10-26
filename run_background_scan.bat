@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title KEIBA AI - Background Monitor Scanner

echo =========================================================
echo    🏇 KEIBA AI -- バックグラウンド自動監視スキャナー
echo =========================================================
echo.

cd /d "%~dp0"

:: Python 実行環境の自動検出
set "PY_CMD="
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set "PY_CMD=python"
    ) else (
        if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
            set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
        )
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python 3 が見つかりません。
    pause
    exit /b 1
)

echo [INFO] Python環境: %PY_CMD%
echo [INFO] 認可フィードのスキャン＆AI予測を実行中...

%PY_CMD% scripts\run_background_scan.py --manifest data\authorized_feed\latest.json --db data\alert_state.db %*

echo.
echo [INFO] スキャン処理が完了しました。
pause

endlocal
