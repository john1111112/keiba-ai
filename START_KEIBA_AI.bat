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

:: 2. 常駐バックグラウンド監視デーモンの起動（アプリ画面を閉じても監視・通知が継続）
echo [2/3] 常駐バックグラウンド監視サービスを開始中...
:: 既に稼働中の監視プロセスがないか確認し、無ければ起動
tasklist /FI "WINDOWTITLE eq KEIBA_AI_BACKGROUND_MONITOR*" 2>NUL | find /I /N "python" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "KEIBA_AI_BACKGROUND_MONITOR" /min %PY_CMD% scripts\run_background_monitor.py --manifest data\authorized_feed\latest_jvlink_feed.json --db data\alert_state.db --interval 60 --use-jvlink
    echo       -> 監視プロセスを開始しました（60秒間隔で自動分析＆通知）
) else (
    echo       -> 監視プロセスは既に稼働中です
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
