@echo off
rem SecureAIFlow agent-hooks - Windows one-line installer.
rem
rem     install.bat cursor        (or antigravity / copilot / codex / --login)
rem
rem Thin shim: finds a working Python 3 and hands off to install.py.
rem No Git Bash required. ASCII only: cmd parses .bat in the OEM codepage,
rem so any non-ASCII character corrupts line parsing.

setlocal
set "HERE=%~dp0"

rem Find a Python that actually runs (the MS Store stub exits non-zero).
set "PY="
py -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py"
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  python3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python3"
)
if not defined PY (
  echo error: Python 3 is required on PATH. Install from python.org and retry.
  exit /b 1
)

"%PY%" "%HERE%install.py" %*
exit /b %errorlevel%
