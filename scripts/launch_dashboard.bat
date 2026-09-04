@echo off
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
streamlit run app\main.py
pause
