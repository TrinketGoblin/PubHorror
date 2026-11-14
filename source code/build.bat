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
if errorlevel 1 (
    echo [!] PyInstaller failed.
    pause
    exit /b 1
)

:: Sharing / Watch Party component: official Node.js (downloaded once, checksum- and signature-verified)
:: plus the sidecar code and its npm packages, copied into dist\PubHorror\sidecar.
:: Needs Node.js + npm installed on THIS (build) PC; players don't need anything.
echo [*] Preparing the sharing component...
powershell -NoProfile -ExecutionPolicy Bypass -File "build_tools\prepare_sidecar.ps1" -Dist "dist\PubHorror"
if errorlevel 1 (
    echo [!] Preparing the sharing component failed - see the message above. The installer was NOT built.
    pause
    exit /b 1
)

echo [*] Checking the build with Microsoft Defender...
powershell -NoProfile -ExecutionPolicy Bypass -File "build_tools\defender_check.ps1" -Path "dist\PubHorror"
if errorlevel 2 (
    echo [!] Defender flagged this build ^(usually a false positive^). Run build.bat again before releasing it.
    echo     The installer was NOT built.
    pause
    exit /b 2
)

echo.
set /p "ans=Build finished. Run Inno Setup? (y/n): "
if /i "%ans%"=="y" (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "PubHorror.iss"
)
pause
