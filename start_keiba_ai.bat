@echo off
chcp 65001 > nul
cd /d "C:\Users\user\.gemini\antigravity\scratch\keiba-ai"
set PYTHON_EXE=C:\Users\user\AppData\Local\Programs\Python\Python310\python.exe

echo ===================================================
echo 🏇 AI競馬予想システム 自動起動マネージャー
echo ===================================================
echo.

:: 1. 最新のSランク・SSランク自動検知＆Discord送信
echo [1/2] 今週末の勝負レースをスキャン中...
"%PYTHON_EXE%" "scripts\auto_notify_s_rank.py"
echo.

:: 2. Streamlit Webアプリケーション起動（バックグラウンド）
echo [2/2] AI競馬予想 Webアプリを起動します...
start /min "" "%PYTHON_EXE%" -m streamlit run app.py --server.headless=true

echo 完了しました。このウィンドウは自動で閉じます。
timeout /t 3 > nul
