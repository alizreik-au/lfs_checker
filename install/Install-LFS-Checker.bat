@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-LFS-Checker.ps1"
if errorlevel 1 pause
