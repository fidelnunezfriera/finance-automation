@echo off
REM ===========================================================================
REM  Reaplica las reglas a TODAS las filas que ya estan en la hoja.
REM
REM  Primero simula y ensena que cambiaria. Luego pregunta si aplicarlo. Si
REM  dices que no, no se escribe nada.
REM
REM  Solo actualiza category, subcategory, rule_id y rule_confidence. El resto
REM  de columnas no se tocan. No se conecta a Trade Republic.
REM ===========================================================================
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat

.venv\Scripts\python.exe pipeline\apply_rules_to_sheet.py --dry-run

REM  Codigos que devuelve la simulacion:
REM    10  hay cambios pendientes  -> preguntar
REM     1  algo fallo              -> no preguntar, no hay nada fiable
REM     0  no hay nada que hacer   -> no preguntar, no hay nada que aplicar
REM
REM  Se comprueban de mayor a menor porque `if errorlevel N` es "N o mas".
if errorlevel 10 goto :preguntar
if errorlevel 1 (
    echo.
    echo La simulacion ha fallado. No se aplica nada.
    goto :fin
)
echo.
echo La hoja ya esta al dia. No hay nada que aplicar.
goto :fin

:preguntar
echo.
echo ===============================================================
set /p RESPUESTA="Aplicar estos cambios a la hoja? (s/N): "
echo ===============================================================
echo.

if /I "%RESPUESTA%"=="s"  goto :aplicar
if /I "%RESPUESTA%"=="si" goto :aplicar
if /I "%RESPUESTA%"=="y"  goto :aplicar
if /I "%RESPUESTA%"=="yes" goto :aplicar

echo Cancelado. No se ha escrito nada en la hoja.
goto :fin

:aplicar
echo Aplicando...
echo.
.venv\Scripts\python.exe pipeline\apply_rules_to_sheet.py

:fin
echo.
pause
