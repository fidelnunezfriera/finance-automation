@echo off
REM ===========================================================================
REM  Ejecucion programada del pipeline (pasos 2-4, sin OTP).
REM
REM  Uso:  schedule_pipeline.bat --install    registra la tarea
REM        schedule_pipeline.bat --status     muestra lo registrado
REM        schedule_pipeline.bat --remove     la elimina
REM        schedule_pipeline.bat --install --dry-run
REM
REM  Sin argumentos hace --status: doble clic para ver como esta la cosa
REM  sin tocar nada.
REM ===========================================================================
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
if "%~1"=="" (
    .venv\Scripts\python.exe pipeline\schedule_pipeline.py --status
) else (
    .venv\Scripts\python.exe pipeline\schedule_pipeline.py %*
)
pause
