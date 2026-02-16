@echo off
cd /d "%~dp0.."
set "REGFILE=%LOCALAPPDATA%\LFS-Checker\Add_LFS_IdLookup_Context_Menu.reg"
if not exist "%REGFILE%" (
    echo Add_LFS_IdLookup_Context_Menu.reg not found at %REGFILE%
    echo Run Install-LFS-Checker first to install and create the reg file.
    pause
    exit /b 1
)
echo Adding LFS context menus (Find LFS object by ID + Run LFS Checker)...
echo You may be prompted for Administrator approval.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'reg.exe' -ArgumentList 'import', \"\"\"%REGFILE%\"\"\" -Verb RunAs -Wait"
if errorlevel 1 (
    echo Failed. Right-click Add_LFS_IdLookup_Context_Menu.reg and choose Merge, then accept UAC.
    pause
    exit /b 1
)
echo Done.
echo.
echo On Windows 11: right-click a folder (or empty area), then "Show more options" to see:
echo   - Find LFS object by ID
echo   - Run LFS Checker
pause
