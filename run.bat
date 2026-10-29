@echo off
rem Apprentice on Windows: sets up a virtual environment once, then starts the app on port 8000.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

rem Find Python 3.10+: the py launcher, then Anaconda/Miniconda, then python on PATH.
set "PYEXE="
set "PYARGS="
where py >nul 2>nul && (set "PYEXE=py" & set "PYARGS=-3")
if not defined PYEXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYEXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYEXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo Python was not found. Install Python 3.11 from python.org, tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)
echo Creating the Python environment with %PYEXE% %PYARGS% ...
"%PYEXE%" %PYARGS% -m venv .venv
if errorlevel 1 (
  echo Could not create the environment. See the message above.
  pause
  exit /b 1
)

:run
call ".venv\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo Installing packages failed. See the message above.
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env. Add your MODEL_API_KEY to it, then run this again. Starting in mock mode for now.
)

echo.
echo Starting Apprentice. Your browser opens by itself; close this window to stop the app.
python backend\scripts\serve.py
pause
