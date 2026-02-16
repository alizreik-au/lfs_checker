@echo off
cd /d "%~dp0.."
set "SCRIPT=%~d0%~p0..\scripts\FindLfsObject.ps1"
set "REPO=%~1"
if "%REPO%"=="" (
    start "LFS Object Lookup" cmd /k "cd /d ""%~d0%~p0.."" && powershell -NoProfile -ExecutionPolicy Bypass -File ""%SCRIPT%"""
    exit /b 0
)
if "%~2"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -RepoPath "%REPO%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -RepoPath "%REPO%" -OID "%~2"
)
if errorlevel 1 pause
