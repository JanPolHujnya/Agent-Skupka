@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Учёт железа — Telegram-бот
echo Бот запускается. Не закрывай это окно.
echo В Telegram открой бота и нажми Старт.
echo.
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" bot.py
) else (
  python bot.py
)
echo.
echo Бот остановился.
pause
