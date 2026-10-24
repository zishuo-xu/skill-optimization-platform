@echo off
rem ============================================================================
rem  Skill Optimization Platform - one-click start for Windows
rem
rem    start.bat                  first run: create venv, install deps, seed demo,
rem                               start server and open the browser
rem    start.bat --reset          wipe local demo data, re-seed, then start
rem    start.bat --no-open        start without opening the browser
rem    start.bat --seed-only      initialize/reset demo data only, do not start
rem    start.bat --no-kill        do not stop an already running server
rem    start.bat --port 8766      use another port
rem    start.bat --help
rem ============================================================================

setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

cd /d "%~dp0"
set "ROOT=%CD%"

set "OPEN_BROWSER=1"
set "DO_RESET=0"
set "SEED_ONLY=0"
set "NO_KILL=0"
if defined SKILLOPT_HOST (set "HOST=%SKILLOPT_HOST%") else (set "HOST=127.0.0.1")
if defined SKILLOPT_PORT (set "PORT=%SKILLOPT_PORT%") else (set "PORT=8765")

:parse_args
if "%~1"=="" goto after_args
if /i "%~1"=="--reset" (
  set "DO_RESET=1"
  shift
  goto parse_args
)
if /i "%~1"=="--no-open" (
  set "OPEN_BROWSER=0"
  shift
  goto parse_args
)
if /i "%~1"=="--seed-only" (
  set "SEED_ONLY=1"
  shift
  goto parse_args
)
if /i "%~1"=="--no-kill" (
  set "NO_KILL=1"
  shift
  goto parse_args
)
if /i "%~1"=="--port" (
  if "%~2"=="" (
    echo [start] missing value after --port
    exit /b 2
  )
  set "PORT=%~2"
  shift
  shift
  goto parse_args
)
if /i "%~1"=="--host" (
  if "%~2"=="" (
    echo [start] missing value after --host
    exit /b 2
  )
  set "HOST=%~2"
  shift
  shift
  goto parse_args
)
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
echo [start] unknown argument: %~1
echo [start] run start.bat --help to see usage.
exit /b 2

:after_args
set "SKILLOPT_HOST=%HOST%"
set "SKILLOPT_PORT=%PORT%"
if "%HOST%"=="0.0.0.0" (set "BROWSER_HOST=127.0.0.1") else (set "BROWSER_HOST=%HOST%")
set "BASE_URL=http://%BROWSER_HOST%:%PORT%"

call :find_python
if errorlevel 1 goto fail
echo [start] Python: %PY_EXE%

if not exist ".venv\Scripts\python.exe" (
  echo [start] Creating virtual environment .venv ...
  "%PY_EXE%" -m venv .venv
  if errorlevel 1 (
    echo [start] failed to create .venv
    goto fail
  )
)
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

"%VENV_PY%" -c "import starlette, uvicorn, httpx, websockets, multipart" >nul 2>nul
if errorlevel 1 (
  echo [start] Installing dependencies ^(first run needs internet, 10-30s^) ...
  "%VENV_PY%" -m pip install --upgrade pip >nul 2>nul
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [start] dependency installation failed.
    echo [start] check your network, or run manually:
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
    goto fail
  )
) else (
  echo [start] Dependencies ready
)

call :prepare_runtime
if errorlevel 2 exit /b 0
if errorlevel 1 goto fail

set "NEEDS_SEED=0"
if "%DO_RESET%"=="1" set "NEEDS_SEED=1"
if "%NEEDS_SEED%"=="0" (
  if not exist "data\app.db" set "NEEDS_SEED=1"
)
if "%NEEDS_SEED%"=="0" (
  set "PROJECT_COUNT="
  for /f "usebackq delims=" %%C in (`"%VENV_PY%" -c "from app import db, persistence; db.init_db(); print(len(persistence.list_projects()))" 2^>nul`) do set "PROJECT_COUNT=%%C"
  if not defined PROJECT_COUNT set "NEEDS_SEED=1"
  if "!PROJECT_COUNT!"=="0" set "NEEDS_SEED=1"
)

if "%NEEDS_SEED%"=="1" (
  if "%DO_RESET%"=="1" (
    echo [start] resetting demo data and running baseline + one optimize round ...
    "%VENV_PY%" -m app.seed --reset --with-optimize
  ) else (
    echo [start] initializing demo project ^(baseline + one optimize round^) ...
    "%VENV_PY%" -m app.seed --with-optimize
  )
  if errorlevel 1 goto fail
) else (
  echo [start] existing local data found, skipping seed
)

if "%SEED_ONLY%"=="1" (
  echo [start] demo data is ready ^(--seed-only: server not started^)
  exit /b 0
)

if "%OPEN_BROWSER%"=="1" (
  start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 40;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 '%BASE_URL%/api/v3/healthz' | Out-Null; Start-Process '%BASE_URL%'; break}catch{Start-Sleep -Milliseconds 500}}"
)

echo.
echo [start] Starting server: %BASE_URL%
echo [start] Press Ctrl+C to stop the server.
echo.
"%VENV_PY%" -m app.server
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo [start] server exited with code %EXITCODE%.
  echo [start] if the port is in use, retry with: start.bat --port 8766
  goto fail
)
exit /b 0

rem ---------------------------------------------------------------------------
:prepare_runtime
if "%NO_KILL%"=="1" goto prepare_no_kill
call :kill_existing
call :is_up
if not errorlevel 1 (
  echo [start] port %PORT% is still in use after stopping the previous server.
  echo [start] another process may own the port; retry with --port 8766 or stop it manually.
  exit /b 1
)
exit /b 0

:prepare_no_kill
call :is_up
if errorlevel 1 exit /b 0
if "%DO_RESET%"=="1" (
  echo [start] service is already running at %BASE_URL%.
  echo [start] stop it first, or omit --no-kill to restart automatically.
  exit /b 1
)
if "%SEED_ONLY%"=="1" (
  echo [start] service is already running; --seed-only requires it to be stopped.
  exit /b 1
)
echo [start] service already running: %BASE_URL%
if "%OPEN_BROWSER%"=="1" start "" "%BASE_URL%"
exit /b 2

:kill_existing
echo [start] stopping previous server ^(if any^) ...
set "SKILLOPT_KILL_ROOT=%ROOT%"
powershell -NoProfile -Command "$root=$env:SKILLOPT_KILL_ROOT; $targets = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*app.server*' -and (($_.ExecutablePath -and $_.ExecutablePath.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine.Contains($root))) }; if ($targets) { $targets | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Output ('[start] stopped PID ' + $_.ProcessId) } catch {} } } else { Write-Output '[start] no previous server process found' }; Start-Sleep -Milliseconds 600"
exit /b 0

rem ---------------------------------------------------------------------------
:find_python
set "PY_EXE="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)"`) do set "PY_EXE=%%P"
  )
)
if not defined PY_EXE (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 (
      for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)"`) do set "PY_EXE=%%P"
    )
  )
)
if not defined PY_EXE (
  echo [start] Python 3.11+ was not found.
  echo [start] install Python from https://www.python.org/downloads/windows/
  echo [start] and enable "Add python.exe to PATH" during installation.
  exit /b 1
)
exit /b 0

rem ---------------------------------------------------------------------------
:is_up
if exist "%SystemRoot%\System32\curl.exe" (
  "%SystemRoot%\System32\curl.exe" -fsS --max-time 2 "%BASE_URL%/api/v3/healthz" >nul 2>nul
  if not errorlevel 1 exit /b 0
  exit /b 1
)
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%BASE_URL%/api/v3/healthz' | Out-Null; exit 0 } catch { exit 1 }"
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------------------
:usage
echo Skill Optimization Platform - one-click start ^(Windows^)
echo.
echo Usage:
echo   start.bat                  first run: setup + seed + start + open browser
echo   start.bat --reset          wipe local demo data, re-seed, then start
echo   start.bat --no-open        start without opening the browser
echo   start.bat --seed-only      initialize/reset demo data only
echo   start.bat --no-kill        do not stop an already running server
echo   start.bat --port 8766      use another port ^(same as SKILLOPT_PORT^)
echo   start.bat --host 0.0.0.0   listen on another address
echo   start.bat --help           show this help
echo.
echo Requirements: Windows 10/11, Python 3.11+ ^(python.org, add to PATH^).
echo The platform will be available at http://127.0.0.1:8765 after startup.
exit /b 0

rem ---------------------------------------------------------------------------
:fail
echo.
echo [start] startup failed. Read the messages above for details.
if not defined CI pause >nul
exit /b 1
