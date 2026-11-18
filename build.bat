@echo off
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "UniversalClicker" --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets;assets" --distpath "." --workpath ".build" --specpath ".build" "main.py"
if errorlevel 1 goto fail
rmdir /s /q ".build"
echo Built: UniversalClicker.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
