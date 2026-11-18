@echo off
setlocal
cd /d "%~dp0"
if exist "UniversalClicker.exe" (
    start "" "UniversalClicker.exe"
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "main.py"
    exit /b
)
python "main.py"
