@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "src\LfsChecker.ps1" %*
if errorlevel 1 pause
