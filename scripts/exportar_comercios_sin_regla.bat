@echo off
REM ===========================================================================
REM  Exporta los comercios de tu historico que ninguna regla real categoriza
REM  todavia, para pedirle a un LLM que proponga reglas nuevas.
REM
REM  Solo lee la hoja, no escribe nada en ella. Deja dos CSV en out/.
REM  Flujo completo en docs/GENERAR_REGLAS.md.
REM ===========================================================================
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat

.venv\Scripts\python.exe pipeline\exportar_comercios_sin_regla.py

echo.
pause
