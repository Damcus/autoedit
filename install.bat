@echo off
cd /d "%~dp0"
echo ============================================
echo   AutoEdit - one-time setup
echo ============================================
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [WARNING] FFmpeg was not found on PATH.
  echo           Install it from https://ffmpeg.org and reopen this window.
  echo.
)

python -m venv .venv
if errorlevel 1 (
  echo [ERROR] Could not create the virtual environment. Is Python installed?
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Dependency install failed. See messages above.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Setup complete. Double-click run.bat
echo ============================================
pause
