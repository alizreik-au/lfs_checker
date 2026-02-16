@echo off
cd /d "%~dp0.."
set "REPO=%~1"
if "%REPO%"=="" (
    python src\lfs_checker.py
) else (
    python src\lfs_checker.py "%REPO%"
)
if errorlevel 1 pause
