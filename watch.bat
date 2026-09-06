@echo off
chcp 65001 >nul
cd /d "%~dp0"
title uchetskup-bot
if exist "%~dp0runtime\python.exe" (
  set "PY=%~dp0runtime\python.exe"
) else (
  set "PY=python"
)
:loop
"%PY%" bot.py
echo bot crashed, restart in 3s
timeout /t 3 /nobreak >nul
goto loop
