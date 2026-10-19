@echo off
rem ReefHue launcher for Windows.
rem First run: creates .venv and installs requirements. Later runs: just start
rem (re-installs only when requirements.txt changed).
setlocal EnableExtensions
cd /d "%~dp0"
set "APP_DIR=%CD%"
set "VENV=%APP_DIR%\.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "PY_URL=https://www.python.org/downloads/"

if exist "%VPY%" (
    "%VPY%" -m reefhue.bootstrap probe >nul 2>&1 && goto :deps
)

set "PY="
for %%V in (3.12 3.11 3.13) do (
    if not defined PY (
        py -%%V -m reefhue.bootstrap probe >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -m reefhue.bootstrap probe >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo ReefHue needs Python 3.11 or newer ^(3.12 recommended^).
    echo Please install it from %PY_URL% and start ReefHue again.
    echo Tip: tick "Add python.exe to PATH" in the installer.
    start "" "%PY_URL%"
    pause
    exit /b 1
)

echo Setting up ReefHue (first start) with %PY% ...
if exist "%VENV%" rmdir /s /q "%VENV%"
%PY% -m venv "%VENV%"
if errorlevel 1 (
    echo Could not create the Python environment in %VENV%.
    pause
    exit /b 1
)

:deps
"%VPY%" -m reefhue.bootstrap check
if errorlevel 2 (
    pause
    exit /b 1
)
if errorlevel 1 (
    echo Installing / updating ReefHue dependencies ^(this can take a few minutes^) ...
    "%VPY%" -m pip install --disable-pip-version-check --upgrade pip
    "%VPY%" -m pip install --disable-pip-version-check -r "%APP_DIR%\requirements.txt"
    if errorlevel 1 (
        echo.
        echo Installing the dependencies failed. Check your internet connection.
        echo If you use a very new Python version, install Python 3.12 from %PY_URL%,
        echo delete the .venv folder inside ReefHue and start again.
        pause
        exit /b 1
    )
    "%VPY%" -m reefhue.bootstrap stamp
)

"%VPY%" -m reefhue %*
if errorlevel 1 (
    echo ReefHue exited with an error. See the log folder ^(Help ^> Open log folder^).
    pause
    exit /b 1
)
endlocal
exit /b 0
