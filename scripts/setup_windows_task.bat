@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title KEIBA AI - Windows Task Scheduler Setup

cd /d "%~dp0.."

echo =========================================================
echo    🏇 KEIBA AI - Windowsタスクスケジューラ登録マネージャー
echo =========================================================
echo.
echo 1: タスク登録 (1分ごとにアプリ不要で全レース監視)
echo 2: タスク解除 (定期実行を停止)
echo 3: タスク状態確認
echo 4: 今すぐ実行テスト
echo.

set /p CHOICE="番号を入力してください (1-4): "

if "%CHOICE%"=="1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows_task.ps1" -Action register -IntervalMinutes 1
) else if "%CHOICE%"=="2" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows_task.ps1" -Action unregister
) else if "%CHOICE%"=="3" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows_task.ps1" -Action status
) else if "%CHOICE%"=="4" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows_task.ps1" -Action run-now
) else (
    echo 無効な選択です。
)

pause
endlocal
