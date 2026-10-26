@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title KEIBA AI - Automated Windows Launcher

echo =========================================================
echo    🏇 KEIBA AI -- 競馬AI予想＆リアルタイム期待値分析システム
echo =========================================================
echo.

cd /d "%~dp0"

:: 1. Python環境検出
set "PY_CMD="
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set "PY_CMD=python"
    ) else (
        if exist "C:\Users\user\AppData\Local\Programs\Python\Python310\python.exe" (
            set "PY_CMD=C:\Users\user\AppData\Local\Programs\Python\Python310\python.exe"
        )
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python 3 が見つかりません。
    echo Python 3.10以上をインストールしてPATHを通してください。
    pause
    exit /b 1
)

echo [1/3] Python環境の確認完了: %PY_CMD%

:: 2. Background monitoring is independently owned by Windows Task Scheduler.
:: Do not start a second monitoring daemon from the dashboard launcher.
:: Double-acquiring JV-Link records may collide with the scheduled worker.
echo [2/3] 自動監視はWindowsタスクスケジューラで独立稼働します。
schtasks.exe /Query /TN "KeibaAI_BackgroundMonitor" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo       [INFO] 初回だけ INSTALL_AUTO_MONITOR.bat を実行すると自動監視を登録できます。
) else (
    echo       [OK] 自動監視タスクは登録されています。
)

:: 3. Streamlit Webダッシュボード起動
echo [3/3] AI競馬予想 Webアプリケーションを起動しています...
start "" "http://localhost:8501"
%PY_CMD% -m streamlit run app.py --server.headless=true --server.port=8501

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] アプリケーションの起動中にエラーが発生しました。
    pause
)

endlocal
