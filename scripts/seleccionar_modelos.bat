@echo off
REM ===========================================================================
REM  Decide que modelos de prediccion usar, con tus datos.
REM
REM  Corre el banco de evaluacion sobre cada serie y guarda la decision en
REM  logs\modelos_elegidos.json, que es lo que lee el dashboard.
REM
REM  Tarda unos segundos por serie, asi que NO se ejecuta al abrir el
REM  dashboard. Si no han llegado meses nuevos desde la ultima vez, sale sin
REM  recalcular nada.
REM
REM  Uso:  seleccionar_modelos.bat            decide si hay datos nuevos
REM        seleccionar_modelos.bat --forzar   recalcula igualmente
REM
REM  No se conecta a Trade Republic.
REM ===========================================================================
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
.venv\Scripts\python.exe pipeline\seleccionar_modelos.py %*
pause
