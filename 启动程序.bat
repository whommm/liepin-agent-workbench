@echo off
cd /d "%~dp0"

REM Use venv python first
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python -m liepin_agent.main
) else (
    echo [WARN] Virtual env not found, trying system python...
    python -m liepin_agent.main
)

if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with code: %errorlevel%
)
pause
