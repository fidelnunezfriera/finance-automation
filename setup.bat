@echo off
REM ===========================================================================
REM  Finance Automation - instalacion automatica
REM
REM  Uso:  doble clic, o desde una consola:
REM          setup.bat          instala con requirements.txt (recomendado)
REM          setup.bat lock     instala versiones exactas (requirements.lock)
REM
REM  Texto sin acentos a proposito: los .bat se leen con la codepage de la
REM  consola (cp850) y los acentos saldrian como simbolos raros.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "ROOT=%CD%"
set "AVISOS=0"

echo.
echo ===============================================================
echo    Finance Automation  -  instalacion
echo ===============================================================
echo.

REM --------------------------------------------------------------------------
REM  [1/6] Ubicacion del repositorio
REM --------------------------------------------------------------------------
echo [1/6] Comprobando la ubicacion del repositorio...
echo       %ROOT%
echo.

REM -- Ruta de red: los entornos virtuales no funcionan ahi --
if "%ROOT:~0,2%"=="\\" (
    echo   ERROR: el repositorio esta en una ruta de red ^(UNC^).
    echo          Los entornos virtuales de Python no funcionan en rutas \\servidor\...
    echo          Copia el repositorio a una carpeta local, por ejemplo:
    echo              C:\dev\finance-automation
    goto :die
)

REM -- Ficheros que tienen que estar si el clonado es correcto --
set "FALTAN="
for %%F in (requirements.txt config.example.yaml app\main.py app\data.py pipeline\run_pipeline.py sheets\push_to_sheets.py) do (
    if not exist "%%F" set "FALTAN=!FALTAN! %%F"
)
if defined FALTAN (
    echo   ERROR: en esta carpeta faltan ficheros del proyecto:
    echo         !FALTAN!
    echo.
    echo          Estas ejecutando setup.bat fuera de la raiz del repositorio,
    echo          o el clonado quedo incompleto. La carpeta correcta es la que
    echo          contiene README.md, requirements.txt y las carpetas app\ y pipeline\
    echo.
    echo          Clona asi:
    echo              git clone https://github.com/fidelnunezfriera/finance-automation
    echo              cd finance-automation
    echo              setup.bat
    goto :die
)
echo   OK  estructura del repositorio correcta

REM -- Clonado con git, o descarga de un ZIP? --
if not exist ".git" (
    echo   AVISO: no hay carpeta .git aqui.
    echo          Parece que descargaste un ZIP en vez de clonar con git.
    echo          Funciona, pero no podras actualizar con 'git pull'.
    set /a AVISOS+=1
)

REM -- Caracteres no ASCII en la ruta ^(acentos, enyes^) --
echo %ROOT%| findstr /R "[^ -~]" >nul
if not errorlevel 1 (
    echo   AVISO: la ruta contiene acentos o caracteres especiales.
    echo          Suele funcionar, pero es una fuente conocida de errores raros
    echo          de codificacion en Windows. Si algo falla, mueve el repositorio
    echo          a una ruta simple como C:\dev\finance-automation
    set /a AVISOS+=1
)

REM -- Espacios en la ruta --
if not "%ROOT%"=="%ROOT: =%" (
    echo   AVISO: la ruta contiene espacios. Deberia funcionar, pero si algun
    echo          script falla prueba con una ruta sin espacios.
    set /a AVISOS+=1
)

REM -- Carpeta sincronizada con OneDrive --
echo %ROOT%| findstr /I /C:"OneDrive" >nul
if not errorlevel 1 (
    echo   AVISO: el repositorio esta dentro de OneDrive.
    echo          Dos problemas: OneDrive sincroniza el entorno virtual .venv
    echo          ^(miles de ficheros^) y ademas subiria a la nube tus credenciales
    echo          de Google en credentials\. Recomendado moverlo fuera de OneDrive.
    set /a AVISOS+=1
)

echo.

REM --------------------------------------------------------------------------
REM  [2/6] Interprete de Python
REM --------------------------------------------------------------------------
echo [2/6] Buscando Python 3.11 o superior...

set "PYEXE="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python") do (
    if not defined PYEXE (
        %%~C -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYEXE=%%~C"
    )
)

if not defined PYEXE (
    echo   ERROR: no se ha encontrado Python 3.11 o superior.
    echo.
    echo          El proyecto necesita Python 3.11+ ^(pandas y scikit-learn ya no
    echo          soportan versiones anteriores^). Si tienes Python 3.10 o menor,
    echo          instalar sobre el no vale: hay que instalar una version nueva.
    echo.
    echo          Descarga: https://www.python.org/downloads/
    echo          IMPORTANTE: marca "Add python.exe to PATH" en el instalador.
    goto :die
)

for /f "tokens=*" %%v in ('%PYEXE% -c "import sys;print(sys.version.split()[0])"') do set "PYFULL=%%v"
for /f "tokens=*" %%v in ('%PYEXE% -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])"') do set "PYNUM=%%v"
echo   OK  Python %PYFULL%   ^(via: %PYEXE%^)
echo.

REM --------------------------------------------------------------------------
REM  [3/6] Fichero de dependencias
REM --------------------------------------------------------------------------
echo [3/6] Seleccionando el fichero de dependencias...

set "REQ=requirements.txt"
if /I "%~1"=="lock" set "REQ=requirements.lock"

if /I "%REQ%"=="requirements.lock" (
    if not exist "requirements.lock" (
        echo   ERROR: no existe requirements.lock
        goto :die
    )
    if !PYNUM! LSS 312 (
        echo   ERROR: requirements.lock fija versiones que necesitan Python 3.12+
        echo          ^(tienes %PYFULL%^). Ejecuta setup.bat sin el argumento "lock".
        goto :die
    )
)
echo   OK  se usara %REQ%
echo.

REM --------------------------------------------------------------------------
REM  [4/6] Entorno virtual
REM --------------------------------------------------------------------------
echo [4/6] Preparando el entorno virtual .venv ...

set "VENVPY=%ROOT%\.venv\Scripts\python.exe"

if exist "%VENVPY%" (
    REM  Version del .venv existente. Se vuelca a un fichero temporal: meter
    REM  una ruta entre comillas dentro de un for /f rompe el parseo de cmd.
    set "VENVNUM="
    set "VERFILE=%TEMP%\fa_venv_ver.txt"
    "%VENVPY%" -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" > "!VERFILE!" 2>nul
    if exist "!VERFILE!" (
        for /f "usebackq tokens=*" %%v in ("!VERFILE!") do set "VENVNUM=%%v"
        del "!VERFILE!" >nul 2>&1
    )

    if not defined VENVNUM (
        echo   AVISO: hay un .venv pero no se ha podido comprobar su version
        echo          ^(puede estar corrupto^).
        set /p "RESP=  Borrarlo y crearlo de cero? [s/N]: "
        if /I "!RESP!"=="s" (
            echo   Borrando .venv ...
            rmdir /s /q ".venv"
        ) else (
            echo   Se reutiliza el entorno existente.
        )
    ) else if !VENVNUM! LSS 311 (
        echo   El .venv existente usa Python anterior a 3.11 y hay que recrearlo.
        set /p "RESP=  Borrar .venv y crearlo de nuevo? [S/n]: "
        if /I "!RESP!"=="n" (
            echo   ERROR: no se puede continuar con un .venv de Python antiguo.
            goto :die
        )
        echo   Borrando .venv ...
        rmdir /s /q ".venv"
    ) else (
        echo   Ya existe un entorno virtual valido en .venv
        set /p "RESP=  Borrarlo y crearlo de cero? [s/N]: "
        if /I "!RESP!"=="s" (
            echo   Borrando .venv ...
            rmdir /s /q ".venv"
        ) else (
            echo   Se reutiliza el entorno existente.
        )
    )
)

if not exist "%VENVPY%" (
    echo   Creando .venv ...
    %PYEXE% -m venv ".venv"
    if errorlevel 1 (
        echo   ERROR: no se ha podido crear el entorno virtual.
        goto :die
    )
)
echo   OK  entorno virtual listo
echo.

REM --------------------------------------------------------------------------
REM  [5/6] Dependencias
REM --------------------------------------------------------------------------
echo [5/6] Instalando dependencias desde %REQ% ...
echo       ^(la primera vez tarda varios minutos^)
echo.

"%VENVPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
if errorlevel 1 (
    echo   ERROR: no se ha podido actualizar pip. Revisa tu conexion a internet.
    goto :die
)

"%VENVPY%" -m pip install -r "%REQ%" --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo   ERROR: la instalacion de dependencias ha fallado.
    echo          Revisa el mensaje de pip de arriba. Causas habituales:
    echo            - sin conexion a internet o proxy corporativo
    echo            - antivirus bloqueando la escritura en .venv
    goto :die
)
echo.

echo       Instalando el navegador de Playwright ^(Chromium, hace falta
echo       para el login web de Trade Republic^) ...
"%VENVPY%" -m playwright install chromium
if errorlevel 1 (
    echo   AVISO: no se ha podido instalar Chromium para Playwright.
    echo          La exportacion desde Trade Republic fallara hasta que lo
    echo          instales a mano con:
    echo            .venv\Scripts\python -m playwright install chromium
    set /a AVISOS+=1
) else (
    echo   OK  navegador de Playwright instalado
)
echo.

REM --------------------------------------------------------------------------
REM  [6/6] Verificacion y configuracion
REM --------------------------------------------------------------------------
echo [6/6] Verificando la instalacion...

"%VENVPY%" -c "import streamlit, plotly, pandas, numpy, sklearn, gspread, yaml, requests, yfinance; import google.oauth2.service_account" 2>nul
if errorlevel 1 (
    echo   ERROR: alguna libreria no se ha instalado correctamente.
    goto :die
)
echo   OK  todas las librerias importan correctamente

if not exist "%ROOT%\.venv\Scripts\pytr.exe" (
    echo   AVISO: no se encuentra pytr.exe en el entorno virtual.
    echo          La exportacion desde Trade Republic no funcionara.
    set /a AVISOS+=1
) else (
    echo   OK  pytr disponible
)

REM -- config.yaml: NUNCA se sobreescribe uno existente --
if exist "config.yaml" (
    echo   OK  config.yaml ya existe ^(no se toca^)
) else (
    copy /y "config.example.yaml" "config.yaml" >nul
    echo   OK  creado config.yaml a partir de la plantilla
    set "CONFIG_NUEVO=1"
)

REM  La carpeta viene en el repositorio, pero se recrea por si acaso: sin ella
REM  el mensaje de abajo diria donde dejar el JSON en un sitio que no existe.
if not exist "credentials" mkdir "credentials"

if not exist "credentials\gdrive-sa.json" (
    echo   AVISO: falta credentials\gdrive-sa.json ^(credenciales de Google^).
    echo          Ver credentials\README.md y SETUP.md, apartado 5.
    set /a AVISOS+=1
)

echo.
echo ===============================================================
echo    Instalacion terminada
if %AVISOS% GTR 0 echo    Avisos: %AVISOS%  ^(revisa los mensajes de arriba^)
echo ===============================================================
echo.
echo  Siguientes pasos:
echo.
REM  Los dos primeros pasos son condicionales, asi que el numero se lleva en
REM  un contador: si no falta nada, la lista empieza igualmente en 1.
set /a PASO=0
if defined CONFIG_NUEVO (
    set /a PASO+=1
    echo   !PASO!. Edita config.yaml y pon el ID de tu Google Sheet
    echo      en google_sheets.spreadsheet_id
    echo      IMPORTANTE: guardalo en UTF-8 ^(el Bloc de notas ya lo hace^)
)
if not exist "credentials\gdrive-sa.json" (
    set /a PASO+=1
    echo   !PASO!. Copia el JSON de tu cuenta de servicio de Google en:
    echo         credentials\gdrive-sa.json
    echo      Ver SETUP.md, apartado 5, para crearla paso a paso.
)
REM  El orden importa: sin pestanas no hay donde escribir, y sin datos el
REM  dashboard sale vacio. init -^> pipeline -^> dashboard.
set /a PASO+=1
echo   !PASO!. Preparar las pestanas de la hoja:  scripts\init_sheet.bat
set /a PASO+=1
echo   !PASO!. Ejecutar el pipeline:              scripts\run_full_pipeline.bat
set /a PASO+=1
echo   !PASO!. Lanzar el dashboard:               scripts\launch_dashboard.bat
echo.
echo  Los lanzadores estan todos en la carpeta scripts\ y se pueden ejecutar
echo  con doble clic desde ahi.
echo.
echo  Opcional, para que el pipeline se ejecute solo con la cadencia
echo  configurada en config.yaml:  scripts\schedule_pipeline.bat --install
echo.
pause
exit /b 0

REM --------------------------------------------------------------------------
:die
echo.
echo ---------------------------------------------------------------
echo   INSTALACION INTERRUMPIDA
echo ---------------------------------------------------------------
echo.
pause
exit /b 1
