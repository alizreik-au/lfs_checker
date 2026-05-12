# LFS Checker

A small Python app that checks a Git repo for **missing LFS objects** and shows which files they belong to.

## What it does

- You pick a repo folder (must contain a `.git` directory).
- It finds all Git LFS pointer files in the repo.
- It checks whether each object exists in `.git/lfs/objects/`.
- It lists every file that points to a missing LFS object, with its OID.
- It also lists **local assets not in LFS**: files that match `.gitattributes` LFS patterns but are real files (not LFS pointers).

**Views:** Default is **Simple** (sortable tables). Switch to **Detailed** to see full OID hashes in a text view. On Windows, the app requests DPI awareness for a sharp (non-blurry) window.

## Repo layout

```
lfs_checker/
├── Install LFS Checker.bat   # Main install entrypoint (same idea as InitGit)
├── RunMe.bat                 # Quick launcher from repo (calls bin)
├── README.md
├── requirements.txt
├── bin/                      # Dev launchers (repo-relative paths)
│   ├── RunMe.bat
│   ├── RunLfsChecker.bat
│   ├── RunLfsIdLookup.bat
│   └── RunInvestigateLfsPush.bat
├── src/
│   ├── lfs_checker.py
│   ├── investigate_lfs_push.py
│   ├── runbook_probe.py
│   ├── runbook_interactive.py
│   ├── runbook_wizard_gui.py
│   ├── check_lfs.py
│   └── check_not_in_lfs.py
├── scripts/
│   └── FindLfsObject.ps1
├── assets/
│   └── lfs.svg
├── docs/
└── install/
    ├── Install-LFS-Checker.ps1
    ├── Install-LFS-Checker.bat
    ├── Add-LFS-Context-Menu.bat
    └── Add_LFS_IdLookup_Context_Menu.reg  # Template (installer also writes one into %LOCALAPPDATA%)
```

## Run it

**From repo root (easiest):** double‑click `RunMe.bat` or run:

```bash
cd lfs_checker
bin\RunMe.bat
```

Or run the GUI directly:

```bash
cd lfs_checker
python src\lfs_checker.py
```

From anywhere:

```bash
python path\to\lfs_checker\src\lfs_checker.py
```

Core app uses only the standard library. For the **LFS logo** in the window, install optional deps:

```bash
pip install -r requirements.txt
```

## CLI: only “files not in LFS”

On another PC (or without the GUI), use the CLI script to list files that **match .gitattributes LFS patterns but are real files**, not LFS pointers:

```bash
# From repo root (or any path inside the repo)
python path\to\lfs_checker\src\check_not_in_lfs.py

# Or specify repo path
python path\to\lfs_checker\src\check_not_in_lfs.py C:\path\to\repo
```

Copy only `src/check_not_in_lfs.py` to the other PC; it uses the repo’s `.gitattributes` and needs no other files. Exit code 0 = none found, 1 = some files are not in LFS.

## Unattended runbook: investigate LFS push failures

This repo also includes an unattended, runbook-aligned investigator that performs:

- `git fetch`
- `git push --dry-run -v` (primary probe)
- regex classification and `(missing) <path> (<oid>)` extraction
- optional `git lfs fsck` + `git lfs fetch --recent` + `git lfs pull` when LFS missing objects are detected
- writes a JSON report

Run (all steps in one go, non-interactive):

```bash
python src\investigate_lfs_push.py [repo_path] --remote origin --report lfs_check_report.json
```

**Step-by-step with human confirmation** (each git command waits for Enter in the terminal; prints what will happen next before each step):

```bash
python src\investigate_lfs_push.py [repo_path] --interactive --remote origin --report lfs_check_report.json
```

The **GUI** (`src\lfs_checker.py`) adds **Runbook (step-by-step)…**: the same pipeline in a window, with options (remote, refspec, report path, lock handling) and **hover tooltips** on main buttons and on wizard controls.

Or double-click:

- `bin\RunInvestigateLfsPush.bat`

### Exit codes (per runbook)

- `0`: no issues detected
- `10`: LFS missing/corrupt local objects
- `20`: non-fast-forward / behind upstream
- `30`: auth/permissions
- `40`: repo locked (`.git/index.lock`)
- `50`: unknown/unclassified failure

## Install (Windows)

Same pattern as the **InitGit** installer in the companion repo (`initgit` next to this folder): from the **repo root**, double‑click **`Install LFS Checker.bat`**, or run:

```cmd
Install LFS Checker.bat
```

That runs `install\Install-LFS-Checker.bat`, which invokes the PowerShell installer. It will:

- Install or **replace** everything under `%LOCALAPPDATA%\LFS-Checker` (full `src\` tree, `scripts\`, `assets\`, `docs\`, `README.md`, `requirements.txt`)
- **Generate** launchers there (`RunMe.bat`, `RunLfsChecker.bat`, `RunLfsIdLookup.bat`, `RunInvestigateLfsPush.bat`, `Add-LFS-Context-Menu.bat`) with paths pointing at the installed copy
- Write `Add_LFS_IdLookup_Context_Menu.reg` and optionally merge it (UAC) for folder right‑click menus

You can still run `install\Install-LFS-Checker.bat` directly if you prefer.

After install, use **`%LOCALAPPDATA%\LFS-Checker\RunMe.bat`** (or the context menu) so Python loads modules from the installed `src` folder.

## Requirements

- Python 3.7+
