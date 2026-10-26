@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title KEIBA AI - Automated Windows Launcher

echo =========================================================
echo    🏇 KEIBA AI -- 競馬AI予想＆期待値分析システム
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

:: 2. バックグラウンド自動監視スキャン（未接続・欠損時は安全待機、架空補完ゼロ）
echo [2/3] バックグラウンド監視サービスを開始中...
start /min "KEIBA_AI_BACKGROUND_MONITOR" %PY_CMD% scripts\run_background_scan.py --manifest data\upcoming_races.json --db data\odds_history.db

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
