# LFS Checker

A small **PowerShell** app (modern Windows Forms GUI) that checks a Git repo for **missing LFS objects** and shows which files they belong to. Runs natively on Windows with no Python required.

## What it does

- You pick a repo folder (must contain a `.git` directory).
- It finds all Git LFS pointer files in the repo.
- It checks whether each object exists in `.git/lfs/objects/`.
- It lists every file that points to a missing LFS object, with its OID.
- It also lists **local assets not in LFS**: files that match `.gitattributes` LFS patterns but are real files (not LFS pointers).
- **Add & commit** local-only assets to LFS, then **Push to remote**.

**Views:** Default is **Simple** (DataGridView tables). Switch to **Detailed** to see full OID hashes in a text view. UI uses a modern flat style (Segoe UI, accent buttons, light theme).

## Repo layout

```
lfs_checker/
├── RunMe.bat              # Quick launcher (double‑click to run GUI)
├── README.md
├── bin/                   # Launchers
│   ├── RunMe.bat          # Launches PowerShell LFS Checker GUI
│   ├── RunLfsChecker.bat
│   └── RunLfsIdLookup.bat
├── src/                   # PowerShell GUI + core; optional Python CLIs
│   ├── LfsChecker.ps1     # Main GUI (Windows Forms)
│   ├── LfsCheckerCore.ps1 # LFS logic (dot-sourced by GUI and runspaces)
│   ├── lfs_checker.py     # Legacy Python GUI (optional)
│   ├── check_lfs.py       # CLI: missing + local not in LFS
│   └── check_not_in_lfs.py # CLI: only "files not in LFS"
├── scripts/
│   └── FindLfsObject.ps1  # LFS object ID lookup (PowerShell GUI)
├── assets/
│   └── lfs.svg            # Logo (optional, used by Python GUI)
└── install/
    ├── Install-LFS-Checker.ps1
    ├── Install-LFS-Checker.bat
    ├── Add-LFS-Context-Menu.bat
    └── Add_LFS_IdLookup_Context_Menu.reg  # Template
```

## Run it

**From repo root (easiest):** double‑click `RunMe.bat` or run:

```batch
cd lfs_checker
bin\RunMe.bat
```

Or run the PowerShell GUI directly (optional repo path argument):

```powershell
powershell -ExecutionPolicy Bypass -File "src\LfsChecker.ps1"
powershell -ExecutionPolicy Bypass -File "src\LfsChecker.ps1" "C:\path\to\repo"
```

**Requirements:** Windows, PowerShell 5.1+, Git (and Git LFS for push). No Python needed for the main GUI.

## CLI: only “files not in LFS”

On another PC (or without the GUI), use the CLI script to list files that **match .gitattributes LFS patterns but are real files**, not LFS pointers:

```bash
# From repo root (or any path inside the repo)
python path\to\lfs_checker\src\check_not_in_lfs.py

# Or specify repo path
python path\to\lfs_checker\src\check_not_in_lfs.py C:\path\to\repo
```

Copy only `src/check_not_in_lfs.py` to the other PC; it uses the repo’s `.gitattributes` and needs no other files. Exit code 0 = none found, 1 = some files are not in LFS.

## Install (Windows)

To install into `%LOCALAPPDATA%\LFS-Checker` and optionally add folder right‑click context menus:

```bash
install\Install-LFS-Checker.bat
```

## Requirements

- **PowerShell 5.1+** (Windows; built-in). Git (and Git LFS for push).
- Optional: **Python 3.7+** for the legacy `lfs_checker.py` GUI and CLI scripts (`check_lfs.py`, `check_not_in_lfs.py`).
