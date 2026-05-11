@echo off
cd /d "%~dp0.."
set "REPO=%~1"
if "%REPO%"=="" (
    python src\investigate_lfs_push.py
) else (
    python src\investigate_lfs_push.py "%REPO%"
)
if errorlevel 1 pause

