@echo off
setlocal enabledelayedexpansion

REM ================================================
REM  UniteChan Bot Auto-Restart Launcher (portable)
REM  - この bat を置いたフォルダが基準になる
REM ================================================

chcp 65001 >nul 2>&1
title UniteChan Bot Auto-Restart

REM この bat が置いてあるフォルダに移動
cd /d "%~dp0"

echo ==============================
echo  🚀 UniteChan Bot Boot
echo  Folder: %CD%
echo ==============================
echo.

:loop
echo [START] python -m unitechan.app.bot
python -m unitechan.app.bot

echo.
echo [WARN] Bot stopped or crashed.
echo Restarting in 5 sec...
timeout /t 5 >nul

goto loop
