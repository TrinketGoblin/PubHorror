@echo off
setlocal EnableDelayedExpansion
title PubHorror — Fresh Build

echo [*] Deleting old build artifacts...
if exist "build" rd /s /q "build"
if exist "dist" rd /s /q "dist"

:: Recreate venv if it was deleted
if not exist "venv\" (
    echo [*] Creating fresh Virtual Environment...
    python -m venv venv
)

echo [*] Activating environment...
call venv\Scripts\activate.bat

echo [*] Installing/Repairing dependencies...
python -m pip install --upgrade pip
:: pywebview[http] is required — without the 'http' extra the local file
:: server (used to serve index.html and assets) is not available at runtime.
pip install "pywebview[http]" requests pyinstaller

echo [*] Running PyInstaller...
pyinstaller --clean PubHorror.spec

echo.
set /p "ans=Build finished. Run Inno Setup? (y/n): "
if /i "%ans%"=="y" (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "PubHorror.iss"
)
pause