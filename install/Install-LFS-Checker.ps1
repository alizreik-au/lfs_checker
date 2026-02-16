#Requires -Version 5.1
<#
.SYNOPSIS
    Installs LFS Checker and LFS Object Lookup to the user's AppData\Local\LFS-Checker.
#>

$ErrorActionPreference = 'Stop'
# Repo root is parent of this script's directory (install/)
$script:SourceDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$InstallDir = Join-Path -Path $env:LOCALAPPDATA -ChildPath 'LFS-Checker'

# Source paths (repo layout) -> flat destination name
$CopyMap = @(
    @{ Src = 'src\lfs_checker.py'; Dest = 'lfs_checker.py' },
    @{ Src = 'src\check_lfs.py'; Dest = 'check_lfs.py' },
    @{ Src = 'src\check_not_in_lfs.py'; Dest = 'check_not_in_lfs.py' },
    @{ Src = 'scripts\FindLfsObject.ps1'; Dest = 'FindLfsObject.ps1' },
    @{ Src = 'assets\lfs.svg'; Dest = 'lfs.svg' },
    @{ Src = 'bin\RunMe.bat'; Dest = 'RunMe.bat' },
    @{ Src = 'bin\RunLfsChecker.bat'; Dest = 'RunLfsChecker.bat' },
    @{ Src = 'bin\RunLfsIdLookup.bat'; Dest = 'RunLfsIdLookup.bat' },
    @{ Src = 'install\Add-LFS-Context-Menu.bat'; Dest = 'Add-LFS-Context-Menu.bat' },
    @{ Src = 'requirements.txt'; Dest = 'requirements.txt' },
    @{ Src = 'README.md'; Dest = 'README.md' }
)

Write-Host "LFS Checker Installer" -ForegroundColor Cyan
Write-Host "=====================" -ForegroundColor Cyan
Write-Host "Install to: $InstallDir" -ForegroundColor White
Write-Host ""

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    Write-Error "Source directory not found: $SourceDir"
    exit 1
}

# Create install directory
if (-not (Test-Path -LiteralPath $InstallDir -PathType Container)) {
    New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
    Write-Host "Created: $InstallDir" -ForegroundColor Green
} else {
    Write-Host "Directory exists: $InstallDir" -ForegroundColor Gray
}

# Copy files
foreach ($entry in $CopyMap) {
    $src = Join-Path -Path $SourceDir -ChildPath $entry.Src
    if (Test-Path -LiteralPath $src -PathType Leaf) {
        $dest = Join-Path -Path $InstallDir -ChildPath $entry.Dest
        Copy-Item -LiteralPath $src -Destination $dest -Force
        Write-Host "  Copied: $($entry.Dest)" -ForegroundColor Green
    } else {
        Write-Host "  Skipped (not found): $($entry.Src)" -ForegroundColor Yellow
    }
}

# Write context menu .reg with installed path (backslashes doubled for .reg format)
$regPath = $InstallDir -replace '\\', '\\\\'
$regContent = @"
Windows Registry Editor Version 5.00

; "Find LFS object by ID" - when right-clicking a folder OR when right-clicking in the empty area inside a folder

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

; "Run LFS Checker" - open LFS Checker GUI with this folder as repo (folder or background)

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
Write-Host "  - Run LFS Checker: RunMe.bat" -ForegroundColor Gray
Write-Host "  - Run LFS Object Lookup: RunLfsIdLookup.bat" -ForegroundColor Gray
Write-Host "  - Run LFS Checker (GUI): RunLfsChecker.bat or RunMe.bat" -ForegroundColor Gray
Write-Host "  - Re-add context menu: Add-LFS-Context-Menu.bat" -ForegroundColor Gray
Write-Host ""
Write-Host "Windows 11: The entry appears under 'Show more options' when you right-click a folder." -ForegroundColor Yellow
Write-Host ""
