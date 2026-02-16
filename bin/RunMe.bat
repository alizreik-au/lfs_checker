@echo off
cd /d "%~dp0.."
python src\lfs_checker.py
if errorlevel 1 pause
