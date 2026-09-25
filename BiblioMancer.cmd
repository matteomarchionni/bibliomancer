@echo off
REM ==========================================================================
REM  BiblioMancer - double-click to open the control panel (source checkout).
REM
REM  Self-locating: runs BiblioMancer.pyw next to this file, so the folder can
REM  be moved without edits. Any arguments go to the command line instead:
REM      BiblioMancer.cmd check
REM
REM  Python: set PY / PYW in local.cmd next to this file (not tracked by git),
REM  e.g.   set "PY=C:\path\to\python.exe"
REM         set "PYW=C:\path\to\pythonw.exe"
REM  otherwise the Python launcher (py / pyw) or python on PATH is used.
REM ==========================================================================
setlocal
if exist "%~dp0local.cmd" call "%~dp0local.cmd"
if not defined PY  for %%P in (py.exe python.exe) do if not defined PY  if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if not defined PYW for %%P in (pyw.exe pythonw.exe) do if not defined PYW if not "%%~$PATH:P"=="" set "PYW=%%~$PATH:P"

if not defined PY (
    echo Python was not found. Install Python 3.10+ from python.org, or set PY and PYW
    echo in local.cmd next to this file.
    pause
    exit /b 1
)
if not defined PYW set "PYW=%PY%"

if "%~1"=="" (
    start "" /D "%~dp0" "%PYW%" "%~dp0BiblioMancer.pyw"
) else (
    "%PY%" "%~dp0BiblioMancer.pyw" %*
)
