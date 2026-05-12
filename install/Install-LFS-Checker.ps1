#Requires -Version 5.1
<#
.SYNOPSIS
    Installs or updates LFS Checker to the user's AppData\Local\LFS-Checker.
.DESCRIPTION
    Mirrors the InitGit installer pattern: replaces the install folder on update, copies src (full tree),
    scripts, assets, docs, generates launch .bat files with correct paths, and optionally merges the
    folder context-menu .reg (UAC).
#>

$ErrorActionPreference = 'Stop'
$script:SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:SrcSource = Join-Path -Path $SourceRoot -ChildPath 'src'
$InstallDir = Join-Path -Path $env:LOCALAPPDATA -ChildPath 'LFS-Checker'

Write-Host "LFS Checker Installer" -ForegroundColor Cyan
Write-Host "=====================" -ForegroundColor Cyan
Write-Host "Install/update to: $InstallDir" -ForegroundColor White
Write-Host ""

if (-not (Test-Path -LiteralPath $SrcSource -PathType Container)) {
    Write-Error "Source directory not found: $SrcSource"
    exit 1
}

if (Test-Path -LiteralPath $InstallDir -PathType Container) {
    Write-Host "Updating existing installation: removing old files..." -ForegroundColor Gray
    Get-ChildItem -LiteralPath $InstallDir -Force | Remove-Item -Recurse -Force
    Write-Host "  Cleared: $InstallDir" -ForegroundColor Green
} else {
    New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
    Write-Host "Created: $InstallDir" -ForegroundColor Green
}

# --- Copy full src (Python modules + package layout for imports) ---
$destSrc = Join-Path -Path $InstallDir -ChildPath 'src'
New-Item -Path $destSrc -ItemType Directory -Force | Out-Null
# Use -Path (not -LiteralPath): a trailing * must be treated as a wildcard so children are copied.
Copy-Item -Path (Join-Path $SrcSource '*') -Destination $destSrc -Recurse -Force
Write-Host "  Copied: src\" -ForegroundColor Green

# --- Copy optional folders ---
foreach ($folder in @('scripts', 'assets', 'docs')) {
    $from = Join-Path -Path $SourceRoot -ChildPath $folder
    if (Test-Path -LiteralPath $from -PathType Container) {
        $to = Join-Path -Path $InstallDir -ChildPath $folder
        New-Item -Path $to -ItemType Directory -Force | Out-Null
        Copy-Item -Path (Join-Path $from '*') -Destination $to -Recurse -Force
        Write-Host "  Copied: $folder\" -ForegroundColor Green
    }
}

# --- Root docs at repo (optional) ---
foreach ($leaf in @('requirements.txt', 'README.md')) {
    $p = Join-Path -Path $SourceRoot -ChildPath $leaf
    if (Test-Path -LiteralPath $p -PathType Leaf) {
        Copy-Item -LiteralPath $p -Destination (Join-Path $InstallDir $leaf) -Force
        Write-Host "  Copied: $leaf" -ForegroundColor Green
    }
}

# --- Generated launchers (paths relative to install dir) ---
$batRunLfsChecker = @"
@echo off
cd /d "%~dp0"
set "REPO=%~1"
if "%REPO%"=="" (
    python "%~dp0src\lfs_checker.py"
) else (
    python "%~dp0src\lfs_checker.py" "%REPO%"
)
if errorlevel 1 pause
"@
Set-Content -LiteralPath (Join-Path $InstallDir 'RunLfsChecker.bat') -Value $batRunLfsChecker -Encoding ASCII
Write-Host "  Created: RunLfsChecker.bat" -ForegroundColor Green

$batRunMe = @"
@echo off
cd /d "%~dp0"
python "%~dp0src\lfs_checker.py"
if errorlevel 1 pause
"@
Set-Content -LiteralPath (Join-Path $InstallDir 'RunMe.bat') -Value $batRunMe -Encoding ASCII
Write-Host "  Created: RunMe.bat" -ForegroundColor Green

$batInvestigate = @"
@echo off
cd /d "%~dp0"
set "REPO=%~1"
if "%REPO%"=="" (
    python "%~dp0src\investigate_lfs_push.py"
) else (
    python "%~dp0src\investigate_lfs_push.py" "%REPO%"
)
if errorlevel 1 pause
"@
Set-Content -LiteralPath (Join-Path $InstallDir 'RunInvestigateLfsPush.bat') -Value $batInvestigate -Encoding ASCII
Write-Host "  Created: RunInvestigateLfsPush.bat" -ForegroundColor Green

$batIdLookup = @"
@echo off
cd /d "%~dp0"
set "SCRIPT=%~dp0scripts\FindLfsObject.ps1"
set "REPO=%~1"
if "%REPO%"=="" (
    start "LFS Object Lookup" cmd /k "cd /d ""%~dp0"" && powershell -NoProfile -ExecutionPolicy Bypass -File ""%SCRIPT%"""
    exit /b 0
)
if "%~2"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -RepoPath "%REPO%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -RepoPath "%REPO%" -OID "%~2"
)
if errorlevel 1 pause
"@
Set-Content -LiteralPath (Join-Path $InstallDir 'RunLfsIdLookup.bat') -Value $batIdLookup -Encoding ASCII
Write-Host "  Created: RunLfsIdLookup.bat" -ForegroundColor Green

# --- Context menu: merge helper (same idea as InitGit) ---
$batAddContext = @"
@echo off
set "REGFILE=%LOCALAPPDATA%\LFS-Checker\Add_LFS_IdLookup_Context_Menu.reg"
if not exist "%REGFILE%" (
    echo Add_LFS_IdLookup_Context_Menu.reg not found at %REGFILE%
    echo Run "Install LFS Checker.bat" from the repo root first to install and create the reg file.
    pause
    exit /b 1
)
echo Adding LFS context menus (Find LFS object by ID + Run LFS Checker^)...
echo You may be prompted for Administrator approval.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'reg.exe' -ArgumentList 'import', \"\"\"%REGFILE%\"\"\" -Verb RunAs -Wait"
if errorlevel 1 (
    echo Failed. Right-click Add_LFS_IdLookup_Context_Menu.reg and choose Merge, then accept UAC.
    pause
    exit /b 1
)
echo Done.
echo On Windows 11: right-click a folder (or empty area), then "Show more options" to see the LFS entries.
pause
"@
Set-Content -LiteralPath (Join-Path $InstallDir 'Add-LFS-Context-Menu.bat') -Value $batAddContext -Encoding ASCII
Write-Host "  Created: Add-LFS-Context-Menu.bat" -ForegroundColor Green

# --- Registry fragment (paths doubled for .reg) ---
$regPath = $InstallDir -replace '\\', '\\\\'
$regContent = @"
Windows Registry Editor Version 5.00

; "Find LFS object by ID" - folder or background

[HKEY_CLASSES_ROOT\Folder\shell\LFS_IdLookup]
@="Find LFS object by ID"
"Icon"="C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe"

[HKEY_CLASSES_ROOT\Folder\shell\LFS_IdLookup\command]
@="\"$regPath\\\\RunLfsIdLookup.bat\" \"%1\""

[HKEY_CLASSES_ROOT\Directory\Background\shell\LFS_IdLookup]
@="Find LFS object by ID"
"Icon"="C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe"
"NoWorkingDirectory"=""

[HKEY_CLASSES_ROOT\Directory\Background\shell\LFS_IdLookup\command]
@="\"$regPath\\\\RunLfsIdLookup.bat\" \"%V\""

; "Run LFS Checker"

[HKEY_CLASSES_ROOT\Folder\shell\LFS_Checker]
@="Run LFS Checker"
"Icon"="C:\\\\Windows\\\\System32\\\\shell32.dll,22"

[HKEY_CLASSES_ROOT\Folder\shell\LFS_Checker\command]
@="\"$regPath\\\\RunLfsChecker.bat\" \"%1\""

[HKEY_CLASSES_ROOT\Directory\Background\shell\LFS_Checker]
@="Run LFS Checker"
"Icon"="C:\\\\Windows\\\\System32\\\\shell32.dll,22"
"NoWorkingDirectory"=""

[HKEY_CLASSES_ROOT\Directory\Background\shell\LFS_Checker\command]
@="\"$regPath\\\\RunLfsChecker.bat\" \"%V\""
"@
$regFile = Join-Path -Path $InstallDir -ChildPath 'Add_LFS_IdLookup_Context_Menu.reg'
Set-Content -LiteralPath $regFile -Value $regContent -Encoding ASCII
Write-Host "  Created: Add_LFS_IdLookup_Context_Menu.reg (points to $InstallDir)" -ForegroundColor Green

# --- Optional: restore default double-click = open folder (if another tool broke it) ---
$fixFolderOpenReg = @"
Windows Registry Editor Version 5.00

[HKEY_CLASSES_ROOT\Folder\shell]
@="open"
"@
$fixFolderOpenFile = Join-Path -Path $InstallDir -ChildPath 'Fix-Folder-Open.reg'
Set-Content -LiteralPath $fixFolderOpenFile -Value $fixFolderOpenReg -Encoding ASCII
Write-Host "  Created: Fix-Folder-Open.reg (merge if double-click on folder runs a script instead of opening)" -ForegroundColor Green

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host ""

$addContext = Read-Host "Add LFS context menus (Find LFS object by ID + Run LFS Checker) to folder right-click? (Y/n)"
if ($addContext -ne 'n' -and $addContext -ne 'N') {
    try {
        $proc = Start-Process -FilePath 'reg.exe' -ArgumentList 'import', "`"$regFile`"" -Verb RunAs -Wait -PassThru
        if ($proc.ExitCode -eq 0) {
            Write-Host "Context menu added (using $regFile)." -ForegroundColor Green
        } else {
            Write-Host "Reg merge may have been cancelled (UAC). Run Add-LFS-Context-Menu.bat from the install folder." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Could not add context menu. Run Add-LFS-Context-Menu.bat from: $InstallDir" -ForegroundColor Yellow
    }
} else {
    Write-Host "To add the context menus later, run Add-LFS-Context-Menu.bat in the install folder." -ForegroundColor Gray
}

Write-Host ""
Write-Host "Installed location: $InstallDir" -ForegroundColor Cyan
Write-Host "  - GUI: RunMe.bat or RunLfsChecker.bat" -ForegroundColor Gray
Write-Host "  - Runbook CLI: RunInvestigateLfsPush.bat (add --interactive for step-by-step)" -ForegroundColor Gray
Write-Host "  - LFS object lookup: RunLfsIdLookup.bat" -ForegroundColor Gray
Write-Host "  - Re-add context menu: Add-LFS-Context-Menu.bat" -ForegroundColor Gray
Write-Host ""
Write-Host "Windows 11: The entry appears under 'Show more options' when you right-click a folder." -ForegroundColor Yellow
Write-Host ""
