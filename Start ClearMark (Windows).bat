@echo off
setlocal
title ClearMark
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1"
exit /b %ERRORLEVEL%
