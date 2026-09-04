@echo off
REM ===========================================================================
REM  Pipeline sin el paso de Trade Republic (pasos 2 a 4).
REM
REM  Limpia el CSV que ya esta en disco, lo carga en la hoja y recalcula las
REM  posiciones. NO se conecta a Trade Republic, asi que no pide OTP ni login.
REM
REM  Es exactamente lo que ejecuta la tarea programada.
REM
REM  Ojo: reaplica las reglas solo a los movimientos del CSV. Para reaplicarlas
REM  a TODO lo que ya esta en la hoja, usa apply_rules.bat.
REM ===========================================================================
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
.venv\Scripts\python.exe pipeline\run_pipeline.py --unattended %*
pause
