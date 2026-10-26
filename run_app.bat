@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title KEIBA AI - Application Launcher

echo =========================================================
echo    🏇 KEIBA AI -- 競馬AI予想＆リアルタイム期待値分析システム
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
    echo Python 3.10以上をインストールしてPATHを通してください。
    pause
    exit /b 1
)

echo [INFO] Python環境: %PY_CMD%
echo [INFO] ブラウザで http://localhost:8501 を開いています...
start "" "http://localhost:8501"

%PY_CMD% -m streamlit run app.py --server.headless=true --server.port=8501

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] アプリケーションの終了時に異常が発生しました。
    pause
)

endlocal
