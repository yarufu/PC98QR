@echo off
setlocal

cd /d "%~dp0"

if "%~1"=="" (
    echo Drag and drop all QR photos onto restore-v2.bat.
    pause
    exit /b 1
)

python "%~dp0windows\reassemble_photos.py" %*

if errorlevel 1 (
    echo.
    echo Restore failed.
)

pause
