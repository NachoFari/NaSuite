@echo off
cd /d "%~dp0"
if exist "dist\NaSuite\NaSuite.exe" (
    start "" "dist\NaSuite\NaSuite.exe" %*
    exit /b 0
)
if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" (
    start "" "%LocalAppData%\Programs\Python\Python312\pythonw.exe" "%~dp0app.py" %*
    exit /b 0
)
start "" pythonw "%~dp0app.py" %*
