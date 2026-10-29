@echo off
rem Double click this file to start FF Analyst. On the first run it installs uv, which sets up everything else.
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>nul
if errorlevel 1 (
  echo First time setup: installing uv, a free helper that installs the rest...
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

where uv >nul 2>nul
if errorlevel 1 (
  echo Could not install uv. Check your internet connection and try again.
  pause
  exit /b 1
)

echo Starting FF Analyst. The first run takes a few minutes. Close this window to quit.
uv run python -m app run
if errorlevel 1 (
  echo FF Analyst stopped with an error.
  pause
)
