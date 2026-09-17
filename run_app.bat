@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found on PATH.
  echo Install Python 3.11 or newer and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

python -c "import PySide6" >nul 2>&1
if errorlevel 1 (
  echo Installing PySide6, this happens once...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Install failed. Run: python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

where pythonw >nul 2>&1
if errorlevel 1 (
  python "%~dp0app.py"
) else (
  start "" pythonw "%~dp0app.py"
)
