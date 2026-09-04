@echo off
REM ===========================================================================
REM  Anade reglas nuevas desde un CSV a la pestana `rules`, y las reordena
REM  junto a las que ya habia por `priority`. No borra ni pisa ninguna regla
REM  existente -- un rule_id repetido se salta, se avisa y no se toca nada.
REM
REM  Primero simula y ensena que anadiria. Luego pregunta si aplicarlo. Si
REM  dices que no, no se escribe nada.
REM
REM  Uso:  scripts\importar_reglas.bat ruta\al\csv.csv
REM
REM  Despues de aplicar, ejecuta apply_rules.bat para recategorizar el
REM  historico con las reglas nuevas -- este script solo las anade a `rules`,
REM  no toca `transactions`.
REM ===========================================================================
REM  %~dp0 es scripts\, asi que hay que subir a la raiz del proyecto.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat

if "%~1"=="" (
    echo Uso: scripts\importar_reglas.bat ruta\al\csv.csv
    goto :fin
)

.venv\Scripts\python.exe pipeline\importar_reglas_csv.py "%~1" --dry-run

REM  Codigos que devuelve la simulacion:
REM    10  hay reglas para anadir  -> preguntar
REM     1  algo fallo              -> no preguntar, no hay nada fiable
REM     0  nada que anadir         -> no preguntar
if errorlevel 10 goto :preguntar
if errorlevel 1 (
    echo.
    echo La simulacion ha fallado. No se aplica nada.
    goto :fin
)
echo.
echo No hay reglas nuevas que anadir.
goto :fin

:preguntar
echo.
echo ===============================================================
set /p RESPUESTA="Anadir estas reglas a la hoja? (s/N): "
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
.venv\Scripts\python.exe pipeline\importar_reglas_csv.py "%~1"

:fin
echo.
pause
