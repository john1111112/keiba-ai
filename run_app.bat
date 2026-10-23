@echo off
cd /d "%~dp0"
echo ========================================
echo   Starting Keiba AI Prediction System
echo ========================================
echo Opening browser at http://localhost:8501 ...
python -m streamlit run app.py
pause
