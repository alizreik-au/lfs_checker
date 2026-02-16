"""
Cute LFS Checker — Find missing Git LFS objects and local assets not in LFS.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

# Enable DPI awareness on Windows for sharp rendering (before any GUI)
def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes = __import__("ctypes")
        shcore = getattr(ctypes.windll, "shcore", None)
        if shcore:
            # PROCESS_PER_MONITOR_DPI_AWARE (2) = sharp per-monitor
            shcore.SetProcessDpiAwareness(2)
        else:
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

_enable_dpi_awareness()

# LFS pointer: first line version, second line oid sha256:<64 hex>
OID_RE = re.compile(r"^oid sha256:([0-9a-f]{64})\s*$", re.IGNORECASE)
VERSION_LINE = "version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer_file(filepath: Path, max_size: int = 500) -> bool:
    """Return True if file looks like a Git LFS pointer (small + version + oid)."""
    try:
        if filepath.stat().st_size > max_size:
            return False
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return False
    if len(lines) < 2:
        return False
    if VERSION_LINE not in lines[0]:
        return False
    for line in lines[1:]:
        if OID_RE.match(line.strip()):
            return True
    return False


def get_oid_from_pointer(filepath: Path) -> Optional[str]:
    """Extract the 64-char OID from an LFS pointer file."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = OID_RE.match(line.strip())
                if m:
                    return m.group(1)
    except (OSError, IOError):
        pass
    return None


def lfs_object_path(git_dir: Path, oid: str) -> Path:
    """Path where Git LFS stores the object: .git/lfs/objects/aa/bb/<oid>."""
    return git_dir / "lfs" / "objects" / oid[0:2] / oid[2:4] / oid


def find_git_root(start: Path) -> Optional[Path]:
    """Return repo root (directory containing .git) or None."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return current if (current / ".git").exists() else None


def scan_lfs_pointers(
    repo_root: Path,
    log: Optional[list[str]] = None,
    stop_event: Optional[threading.Event] = None,
) -> list[tuple[Path, str]]:
    """Walk repo (excluding .git) and return list of (relative_path, oid) for LFS pointers."""
    def _log(msg: str) -> None:
        if log is not None:
            log.append(msg)

    results: list[tuple[Path, str]] = []
    git_dir = repo_root / ".git"
    _log("Scanning local repo only (no remote).")
    _log("Phase 1: Finding LFS pointer files…")
    for root, _dirs, files in os.walk(repo_root):
        if stop_event and stop_event.is_set():
            _log("  Stopped by user.")
            return results
        root_path = Path(root)
        if root_path == git_dir or git_dir in root_path.parents or ".git" in root_path.parts:
            continue
        for name in files:
            filepath = root_path / name
            if not is_lfs_pointer_file(filepath):
                continue
            oid = get_oid_from_pointer(filepath)
            if oid:
                try:
                    rel = filepath.relative_to(repo_root)
                except ValueError:
                    rel = filepath
                results.append((rel, oid))
    _log(f"  Found {len(results)} LFS pointer file(s).")
    return results


def check_missing_lfs(
    repo_root: Path,
    log: Optional[list[str]] = None,
    stop_event: Optional[threading.Event] = None,
) -> list[tuple[str, str]]:
    """
    Return list of (relative_file_path, oid) for LFS files whose object is missing locally.
    """
    def _log(msg: str) -> None:
        if log is not None:
            log.append(msg)

    git_dir = repo_root / ".git"
    if not git_dir.is_dir():
        return []
    pointers = scan_lfs_pointers(repo_root, log=log, stop_event=stop_event)
    if stop_event and stop_event.is_set():
        _log("  Stopped by user.")
    _log("Phase 2: Checking which LFS objects are missing locally…")
    missing: list[tuple[str, str]] = []
    for rel_path, oid in pointers:
        if stop_event and stop_event.is_set():
            _log("  Stopped by user.")
            break
        obj_path = lfs_object_path(git_dir, oid)
        if not obj_path.is_file():
            missing.append((str(rel_path), oid))
    _log(f"  Missing: {len(missing)} object(s).")
    return missing


# --- Local assets not in LFS (should be LFS per .gitattributes but are real files) ---

def parse_lfs_patterns(gitattributes_path: Path) -> list[str]:
    """Read .gitattributes and return path patterns that use filter=lfs."""
    patterns: list[str] = []
    try:
        with open(gitattributes_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "filter=lfs" not in line:
                    continue
                parts = line.split()
                if parts:
                    patterns.append(parts[0])
    except (OSError, IOError):
        pass
    return patterns


def path_matches_lfs_pattern(rel_path: Path, pattern: str) -> bool:
    """Return True if relative path matches this .gitattributes LFS pattern."""
    posix = rel_path.as_posix().replace("\\", "/")
    posix_lower = posix.lower()
    if "/" in pattern:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].lower()
            return posix_lower == prefix or posix_lower.startswith(prefix + "/")
        return fnmatch.fnmatch(posix_lower, pattern.lower())
    return fnmatch.fnmatch(rel_path.name.lower(), pattern.lower())


# Batch size for git check-ignore (avoid command-line length limits)
_GITIGNORE_BATCH = 200


def _gitignored_batch(repo_root: Path, paths: list[str]) -> set[str]:
    """Return set of paths that are ignored by .gitignore. Uses batched git check-ignore."""
    ignored: set[str] = set()
    for i in range(0, len(paths), _GITIGNORE_BATCH):
        batch = paths[i : i + _GITIGNORE_BATCH]
        try:
            r = subprocess.run(
                ["git", "check-ignore", "--stdin"],
                cwd=str(repo_root),
                input="\n".join(batch),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if r.returncode == 0 and r.stdout:
                for line in r.stdout.strip().splitlines():
                    p = line.strip()
                    if p in batch:
                        ignored.add(p)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return ignored


def check_local_assets_not_in_lfs(
    repo_root: Path,
    log: Optional[list[str]] = None,
    stop_event: Optional[threading.Event] = None,
) -> list[str]:
    """
    Return paths of files that match LFS patterns in .gitattributes but are NOT LFS pointers.
    Paths listed in .gitignore are excluded. Only scans local repo (no remote).
    """
    def _log(msg: str) -> None:
        if log is not None:
            log.append(msg)

    gitattributes = repo_root / ".gitattributes"
    if not gitattributes.is_file():
        return []
    patterns = parse_lfs_patterns(gitattributes)
    if not patterns:
        return []
    git_dir = repo_root / ".git"
    candidates: list[str] = []
    _log("Finding local assets (match .gitattributes, not LFS pointers)…")
    for root, _dirs, files in os.walk(repo_root):
        if stop_event and stop_event.is_set():
            _log("  Stopped by user.")
            break
        root_path = Path(root)
        if root_path == git_dir or git_dir in root_path.parents or ".git" in root_path.parts:
            continue
        for name in files:
            filepath = root_path / name
            try:
                rel = filepath.relative_to(repo_root)
            except ValueError:
                continue
            if not any(path_matches_lfs_pattern(rel, p) for p in patterns):
                continue
            if is_lfs_pointer_file(filepath):
                continue
            candidates.append(rel.as_posix())
    _log(f"  Found {len(candidates)} candidate(s).")
    if not candidates:
        return []
    _log("Filtering by .gitignore (batched)…")
    ignored = _gitignored_batch(repo_root, candidates)
    result = sorted(p for p in candidates if p not in ignored)
    _log(f"  After .gitignore: {len(result)} local-only asset(s).")
    return result


# --- Logo from SVG ---

def _load_logo_photo(size: int = 40) -> Optional[tk.PhotoImage]:
    """Load lfs.svg as a tkinter PhotoImage; returns None if unavailable."""
    script_dir = Path(__file__).resolve().parent
    svg_path = script_dir / "lfs.svg"
    if not svg_path.is_file():
        svg_path = script_dir.parent / "assets" / "lfs.svg"
    if not svg_path.is_file():
        return None
    try:
        import cairosvg
        from PIL import Image
        buf = BytesIO()
        cairosvg.svg2png(
            url=str(svg_path),
            output_width=size,
            output_height=size,
            write_to=buf,
        )
        buf.seek(0)
        img = Image.open(buf).convert("RGBA")
        # tkinter needs RGB + alpha handled; use a solid bg for compatibility
        bg = Image.new("RGB", img.size, (254, 247, 240))
        bg.paste(img, mask=img.split()[3])
        buf2 = BytesIO()
        bg.save(buf2, format="PNG")
        buf2.seek(0)
        photo = tk.PhotoImage(data=buf2.getvalue())
        return photo
    except Exception:
        return None


# --- Safe display for Windows (avoid charmap encode errors with non-ASCII paths) ---


def _safe_display(s: str) -> str:
    """Return a string safe for display in widgets that may use system encoding on Windows."""
    try:
        return s.encode("cp1252", errors="replace").decode("cp1252")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


# --- GUI ---

class LfsCheckerApp:
    VIEW_SIMPLE = "simple"
    VIEW_DETAILED = "detailed"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("LFS Checker")
        self.root.minsize(560, 480)
        self.root.geometry("720x580")

        self.repo_path = tk.StringVar()
        self.view_mode = tk.StringVar(value=self.VIEW_SIMPLE)
        self._stop_requested = threading.Event()
        self._last_missing: list[tuple[str, str]] = []
        self._last_local_not_lfs: list[str] = []
        self._logo_photo: Optional[tk.PhotoImage] = None
        self._build_ui()

    def _build_ui(self) -> None:
        bg = "#fef7f0"
        card_bg = "#fff5eb"
        text = "#3d2c29"
        self.root.configure(bg=bg)

        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        # Header with logo
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 12))
        self._logo_photo = _load_logo_photo(40)
        if self._logo_photo:
            logo_label = ttk.Label(header, image=self._logo_photo)
            logo_label.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(header, text="LFS Checker", font=("Segoe UI", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(
            header,
            text="  Find missing LFS objects and local assets not in LFS",
            font=("Segoe UI", 10),
            foreground="gray",
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Folder row
        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=8)
        ttk.Label(row, text="Repo folder:", width=12).pack(side=tk.LEFT, padx=(0, 8))
        entry = ttk.Entry(row, textvariable=self.repo_path, width=50)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(row, text="Browse…", command=self._browse).pack(side=tk.LEFT)

        # Buttons + view mode
        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=8)
        self.check_missing_btn = ttk.Button(btn_row, text="Check missing (local)", command=self._run_check_missing)
        self.check_missing_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.check_local_btn = ttk.Button(btn_row, text="Check local not in LFS", command=self._run_check_local)
        self.check_local_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.commit_btn = ttk.Button(btn_row, text="Add & commit", command=self._run_commit)
        self.commit_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.push_btn = ttk.Button(btn_row, text="Push to remote", command=self._run_push)
        self.push_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._on_stop)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn.state(["disabled"])
        ttk.Separator(btn_row, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        ttk.Label(btn_row, text="View:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Radiobutton(
            btn_row,
            text="Simple (table)",
            variable=self.view_mode,
            value=self.VIEW_SIMPLE,
            command=self._on_view_changed,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(
            btn_row,
            text="Detailed (with hashes)",
            variable=self.view_mode,
            value=self.VIEW_DETAILED,
            command=self._on_view_changed,
        ).pack(side=tk.LEFT, padx=2)
        self.status_var = tk.StringVar(value="Choose a repo folder, then run one or both checks.")
        ttk.Label(btn_row, textvariable=self.status_var, foreground="gray").pack(
            side=tk.LEFT, padx=(12, 0)
        )

        # Progress bar (shown only during scan / commit / push)
        self.progress_frame = ttk.Frame(main)
        self.progress_frame.pack(fill=tk.X, pady=(4, 0))
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            mode="indeterminate",
            length=320,
        )
        # Don't pack the bar initially; _show_progress() / _hide_progress() toggle it

        # Log area (scan progress; local repo only, no remote)
        log_frame = ttk.LabelFrame(main, text="Log (local scan only, no remote)")
        log_frame.pack(fill=tk.X, pady=(6, 0))
        self.log_text = tk.Text(log_frame, height=5, font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.X, padx=4, pady=4)

        # Results area: two stacked frames (only one visible)
        self.results_container = ttk.Frame(main)
        self.results_container.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # --- Simple view: two tables ---
        self.simple_frame = ttk.Frame(self.results_container)
        # Missing LFS table
        ttk.Label(
            self.simple_frame,
            text="Missing LFS objects (pointer exists, blob missing)",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 4))
        missing_container = ttk.Frame(self.simple_frame)
        missing_container.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.missing_tree = self._make_sortable_tree(
            missing_container,
            columns=("#", "File path", "OID"),
            column_widths=(28, 400, 220),
            height=8,
        )
        # Local not in LFS table
        ttk.Label(
            self.simple_frame,
            text="Local assets not in LFS (match .gitattributes but are real files)",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, pady=(8, 4))
        local_container = ttk.Frame(self.simple_frame)
        local_container.pack(fill=tk.BOTH, expand=True)
        self.local_tree = self._make_sortable_tree(
            local_container,
            columns=("#", "File path"),
            column_widths=(28, 600),
            height=8,
        )

        # --- Detailed view: text ---
        self.detailed_frame = ttk.Frame(self.results_container)
        self.result_text = scrolledtext.ScrolledText(
            self.detailed_frame,
            height=22,
            font=("Consolas", 10),
            wrap=tk.WORD,
            bg=card_bg,
            fg=text,
            insertbackground=text,
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        self.result_text.pack(fill=tk.BOTH, expand=True)
        self.result_text.tag_configure("heading", font=("Consolas", 10, "bold"))

        # Show default view (Simple)
        self.simple_frame.pack(fill=tk.BOTH, expand=True)
        self.detailed_frame.pack_forget()

    def _show_progress(self) -> None:
        self.progress_bar.pack(fill=tk.X)
        self.progress_bar.start(50)

    def _hide_progress(self) -> None:
        self.progress_bar.stop()
        self.progress_bar.pack_forget()

    def _on_stop(self) -> None:
        self._stop_requested.set()
        self.status_var.set("Stopping…")

    def _make_sortable_tree(
        self,
        parent: ttk.Frame,
        columns: tuple[str, ...],
        column_widths: tuple[int, ...],
        height: int = 10,
    ) -> ttk.Treeview:
        """Build a Treeview with scrollbars and sortable columns."""
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=height, selectmode="extended")
        first_col_width = 28
        for i, (col, w) in enumerate(zip(columns, column_widths)):
            tree.heading(col, text=col, command=lambda c=col: self._sort_tree_by_column(tree, c))
            # First column (#) stays narrow and fixed; others keep room to read
            if i == 0:
                tree.column(col, width=first_col_width, minwidth=20)
            else:
                tree.column(col, width=w, minwidth=60)
        # Keep first column fixed on resize (no scaling)
        def _fix_first_column(_ev: object, tr: ttk.Treeview = tree, w: int = first_col_width) -> None:
            tr.column(tr["columns"][0], width=w)
        tree.bind("<Configure>", _fix_first_column)
        vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        return tree

    def _sort_tree_by_column(self, tree: ttk.Treeview, column: str) -> None:
        """Sort tree by column; toggle asc/desc. Re-insert rows in sorted order."""
        key = "_sort_dir"
        if not hasattr(tree, key):
            setattr(tree, key, {})
        sort_dir = getattr(tree, key)
        reverse = sort_dir.get(column, False)
        sort_dir[column] = not reverse
        col_index = tree["columns"].index(column)
        rows = []
        for iid in tree.get_children(""):
            vals = tree.item(iid, "values")
            rows.append((vals, (vals[col_index].lower() if col_index < len(vals) else "")))
        rows.sort(key=lambda x: x[1], reverse=reverse)
        for iid in tree.get_children(""):
            tree.delete(iid)
        for values, _ in rows:
            tree.insert("", tk.END, values=values)

    def _on_view_changed(self) -> None:
        if self.view_mode.get() == self.VIEW_SIMPLE:
            self.detailed_frame.pack_forget()
            self.simple_frame.pack(fill=tk.BOTH, expand=True)
            self._populate_simple_view()
        else:
            self.simple_frame.pack_forget()
            self.detailed_frame.pack(fill=tk.BOTH, expand=True)
            self._populate_detailed_view()

    def _populate_simple_view(self) -> None:
        """Fill the two trees from cached results."""
        for t in (self.missing_tree, self.local_tree):
            for i in t.get_children(""):
                t.delete(i)
        for i, (path, oid) in enumerate(self._last_missing, 1):
            self.missing_tree.insert("", tk.END, values=(i, _safe_display(path), _safe_display(oid)))
        for i, path in enumerate(self._last_local_not_lfs, 1):
            self.local_tree.insert("", tk.END, values=(i, _safe_display(path)))
        if not self._last_missing and not self._last_local_not_lfs:
            self.missing_tree.insert("", tk.END, values=("", "No missing LFS objects.", ""))
            self.local_tree.insert("", tk.END, values=("", "No local-only assets under LFS patterns."))

    def _populate_detailed_view(self) -> None:
        """Fill the detailed text area from cached results."""
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, "1) Missing LFS objects (pointer exists, blob missing)\n", "heading")
        self.result_text.insert(tk.END, "   " + "-" * 50 + "\n")
        if not self._last_missing:
            self.result_text.insert(tk.END, "   None.\n\n")
        else:
            for path, oid in self._last_missing:
                self.result_text.insert(tk.END, f"   {_safe_display(path)}\n")
                self.result_text.insert(tk.END, f"      oid sha256:{_safe_display(oid)}\n")
            self.result_text.insert(tk.END, "\n")
        self.result_text.insert(tk.END, "2) Local assets not in LFS (match .gitattributes but are real files)\n", "heading")
        self.result_text.insert(tk.END, "   " + "-" * 50 + "\n")
        if not self._last_local_not_lfs:
            self.result_text.insert(tk.END, "   None.\n")
        else:
            for path in self._last_local_not_lfs:
                self.result_text.insert(tk.END, f"   {_safe_display(path)}\n")
        self.result_text.see("1.0")

    def _browse(self) -> None:
        path = filedialog.askdirectory(title="Select repo folder")
        if path:
            self.repo_path.set(path)

    def _run_commit(self) -> None:
        path = self.repo_path.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo("No repo", "Choose a repo folder first.")
            return
        repo_root = find_git_root(Path(path))
        if not repo_root:
            messagebox.showerror("Not a Git repo", "No .git found.")
            return
        if not self._last_local_not_lfs:
            messagebox.showinfo(
                "Add & commit",
                "No local-only assets to commit. Run Check LFS first; commit only adds files from the \"Local assets not in LFS\" list.",
            )
            return
        self.repo_path.set(str(repo_root))
        self.check_missing_btn.state(["disabled"])
        self.check_local_btn.state(["disabled"])
        self.commit_btn.state(["disabled"])
        self.push_btn.state(["disabled"])
        self.status_var.set("Adding & committing…")
        self._show_progress()
        threading.Thread(target=self._do_commit, args=(repo_root,), daemon=True).start()

    def _do_commit(self, repo_root: Path) -> None:
        """Add local-only assets to LFS and commit (no push)."""
        log: list[str] = []
        try:
            for rel in self._last_local_not_lfs:
                ok = self._git_run(repo_root, ["add", rel], log)
                if not ok:
                    self.root.after(0, lambda: self._commit_done(False, "\n".join(log)))
                    return
            ok = self._git_run(repo_root, ["commit", "-m", "Add local assets to LFS"], log)
            if not ok:
                full_log = "\n".join(log).lower()
                if "nothing to commit" not in full_log and "no changes" not in full_log:
                    self.root.after(0, lambda: self._commit_done(False, "\n".join(log)))
                    return
            self.root.after(0, lambda: self._commit_done(True, "\n".join(log)))
        except Exception as e:
            log.append(str(e))
            self.root.after(0, lambda: self._commit_done(False, "\n".join(log)))

    def _commit_done(self, success: bool, log_text: str) -> None:
        self._hide_progress()
        self.check_missing_btn.state(["!disabled"])
        self.check_local_btn.state(["!disabled"])
        self.commit_btn.state(["!disabled"])
        self.push_btn.state(["!disabled"])
        self.status_var.set("Commit finished." if success else "Commit failed.")
        if success:
            messagebox.showinfo("Add & commit", "Local assets committed to LFS. Verify, then use Push to remote when ready.\n\n" + log_text)
        else:
            messagebox.showerror("Add & commit", "Commit failed.\n\n" + log_text)

    def _run_push(self) -> None:
        path = self.repo_path.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo("No repo", "Choose a repo folder first.")
            return
        repo_root = find_git_root(Path(path))
        if not repo_root:
            messagebox.showerror("Not a Git repo", "No .git found.")
            return
        self.repo_path.set(str(repo_root))
        self.check_missing_btn.state(["disabled"])
        self.check_local_btn.state(["disabled"])
        self.commit_btn.state(["disabled"])
        self.push_btn.state(["disabled"])
        self.status_var.set("Pushing…")
        self._show_progress()
        threading.Thread(target=self._do_push, args=(repo_root,), daemon=True).start()

    def _get_remote_and_branch(self, repo_root: Path, log: list[str]) -> tuple[str, str] | None:
        """Get remote and branch for current HEAD. Uses upstream tracking first, then branch.remote, then first remote."""
        branch = self._git_output(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], log).strip()
        if not branch or branch == "HEAD":
            return None
        # Prefer upstream: e.g. origin/main
        upstream = self._git_output(
            repo_root,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            log,
        ).strip()
        if upstream and "/" in upstream:
            remote, remote_branch = upstream.split("/", 1)
            log.append(f"Using upstream: {remote}/{remote_branch}\n")
            return (remote, remote_branch)
        # Else branch.remote for this branch
        remote = self._git_output(
            repo_root,
            ["config", "--get", f"branch.{branch}.remote"],
            log,
        ).strip()
        if remote:
            log.append(f"Using branch.{branch}.remote: {remote}\n")
            return (remote, branch)
        # Fallback: first remote from git remote
        remotes = self._git_output(repo_root, ["remote"], log).strip().splitlines()
        remote = remotes[0].strip() if remotes else "origin"
        log.append(f"No upstream set; using first remote: {remote}, branch: {branch}\n")
        return (remote, branch)

    def _do_push(self, repo_root: Path) -> None:
        """Push LFS objects and refs to remote (no add/commit)."""
        log: list[str] = []
        try:
            rb = self._get_remote_and_branch(repo_root, log)
            if not rb:
                self.root.after(0, lambda: self._push_done(False, "\n".join(log) + "\nDetached HEAD or no branch."))
                return
            remote, branch = rb
            log.append(f"Branch: {branch}, remote: {remote}\n")

            ok = self._git_run(repo_root, ["lfs", "push", remote, branch, "--all"], log)
            if not ok:
                self.root.after(0, lambda: self._push_done(False, "\n".join(log)))
                return
            ok = self._git_run(repo_root, ["push", remote, branch], log)
            self.root.after(0, lambda: self._push_done(ok, "\n".join(log)))
        except Exception as e:
            log.append(str(e))
            self.root.after(0, lambda: self._push_done(False, "\n".join(log)))

    def _git_run(self, repo_root: Path, args: list[str], log: list[str]) -> bool:
        cmd = ["git"] + args
        log.append("$ " + " ".join(cmd) + "\n")
        try:
            r = subprocess.run(
                cmd,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=600,
            )
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if out:
                log.append(out + "\n")
            if err:
                log.append(err + "\n")
            if r.returncode != 0:
                log.append(f"Exit code: {r.returncode}\n")
                return False
            return True
        except subprocess.TimeoutExpired:
            log.append("Command timed out.\n")
            return False
        except Exception as e:
            log.append(str(e) + "\n")
            return False

    def _git_output(self, repo_root: Path, args: list[str], log: list[str]) -> str:
        cmd = ["git"] + args
        try:
            r = subprocess.run(
                cmd,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            log.append("$ " + " ".join(cmd) + "\n")
            if r.returncode != 0 and r.stderr:
                log.append(r.stderr)
            return (r.stdout or "").strip()
        except Exception as e:
            log.append(str(e) + "\n")
            return ""

    def _push_done(self, success: bool, log_text: str) -> None:
        self._hide_progress()
        self.check_missing_btn.state(["!disabled"])
        self.check_local_btn.state(["!disabled"])
        self.commit_btn.state(["!disabled"])
        self.push_btn.state(["!disabled"])
        self.status_var.set("Push finished." if success else "Push failed.")
        if success:
            messagebox.showinfo("Push to remote", "Push completed.\n\n" + log_text)
        else:
            messagebox.showerror("Push to remote", "Push failed.\n\n" + log_text)

    def _run_check_missing(self) -> None:
        path = self.repo_path.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo("No folder", "Choose a repo folder first.")
            return
        repo_root = find_git_root(Path(path))
        if not repo_root:
            messagebox.showerror("Not a Git repo", "No .git found.")
            return
        self.repo_path.set(str(repo_root))
        self._stop_requested.clear()
        self.check_missing_btn.state(["disabled"])
        self.check_local_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.status_var.set("Checking missing (local)…")
        self._show_progress()
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "Checking missing LFS objects (local only)…")
        self.log_text.config(state=tk.DISABLED)
        if self.view_mode.get() == self.VIEW_SIMPLE:
            for i in self.missing_tree.get_children(""):
                self.missing_tree.delete(i)
            self.missing_tree.insert("", tk.END, values=("", "Scanning…", ""))
        threading.Thread(target=self._do_check_missing, args=(repo_root,), daemon=True).start()

    def _do_check_missing(self, repo_root: Path) -> None:
        try:
            log_lines: list[str] = []
            missing = check_missing_lfs(repo_root, log=log_lines, stop_event=self._stop_requested)
            self.root.after(0, lambda: self._show_results_missing(missing, log_lines))
        except Exception as e:
            self.root.after(0, lambda: self._show_error(str(e)))

    def _run_check_local(self) -> None:
        path = self.repo_path.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo("No folder", "Choose a repo folder first.")
            return
        repo_root = find_git_root(Path(path))
        if not repo_root:
            messagebox.showerror("Not a Git repo", "No .git found.")
            return
        self.repo_path.set(str(repo_root))
        self._stop_requested.clear()
        self.check_missing_btn.state(["disabled"])
        self.check_local_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.status_var.set("Checking local not in LFS…")
        self._show_progress()
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "Checking local assets not in LFS…")
        self.log_text.config(state=tk.DISABLED)
        if self.view_mode.get() == self.VIEW_SIMPLE:
            for i in self.local_tree.get_children(""):
                self.local_tree.delete(i)
            self.local_tree.insert("", tk.END, values=("", "Scanning…", ""))
        threading.Thread(target=self._do_check_local, args=(repo_root,), daemon=True).start()

    def _do_check_local(self, repo_root: Path) -> None:
        try:
            log_lines: list[str] = []
            local_not_lfs = check_local_assets_not_in_lfs(repo_root, log=log_lines, stop_event=self._stop_requested)
            self.root.after(0, lambda: self._show_results_local(local_not_lfs, log_lines))
        except Exception as e:
            self.root.after(0, lambda: self._show_error(str(e)))

    def _show_results_missing(
        self,
        missing: list[tuple[str, str]],
        log_lines: Optional[list[str]] = None,
    ) -> None:
        self._hide_progress()
        self.stop_btn.state(["disabled"])
        self.check_missing_btn.state(["!disabled"])
        self.check_local_btn.state(["!disabled"])
        self.commit_btn.state(["!disabled"])
        self.push_btn.state(["!disabled"])
        self._last_missing = missing
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        if log_lines:
            self.log_text.insert(tk.END, _safe_display("\n".join(log_lines)))
        self.log_text.config(state=tk.DISABLED)
        self.status_var.set("Stopped." if self._stop_requested.is_set() else f"Done — {len(missing)} missing LFS (local).")
        if self.view_mode.get() == self.VIEW_SIMPLE:
            self._populate_simple_view()
        else:
            self._populate_detailed_view()

    def _show_results_local(
        self,
        local_not_lfs: list[str],
        log_lines: Optional[list[str]] = None,
    ) -> None:
        self._hide_progress()
        self.stop_btn.state(["disabled"])
        self.check_missing_btn.state(["!disabled"])
        self.check_local_btn.state(["!disabled"])
        self.commit_btn.state(["!disabled"])
        self.push_btn.state(["!disabled"])
        self._last_local_not_lfs = local_not_lfs
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        if log_lines:
            self.log_text.insert(tk.END, _safe_display("\n".join(log_lines)))
        self.log_text.config(state=tk.DISABLED)
        self.status_var.set("Stopped." if self._stop_requested.is_set() else f"Done — {len(local_not_lfs)} local asset(s) not in LFS.")
        if self.view_mode.get() == self.VIEW_SIMPLE:
            self._populate_simple_view()
        else:
            self._populate_detailed_view()

    def _show_error(self, msg: str) -> None:
        self._hide_progress()
        self.stop_btn.state(["disabled"])
        self.check_missing_btn.state(["!disabled"])
        self.check_local_btn.state(["!disabled"])
        self.commit_btn.state(["!disabled"])
        self.push_btn.state(["!disabled"])
        self.status_var.set("Error.")
        messagebox.showerror("LFS Checker", _safe_display(msg))
        if self.view_mode.get() == self.VIEW_DETAILED:
            self.result_text.delete("1.0", tk.END)
            self.result_text.insert(tk.END, f"Error: {_safe_display(msg)}\n")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    initial_repo = None
    if len(sys.argv) >= 2 and os.path.isdir(sys.argv[1]):
        initial_repo = os.path.abspath(sys.argv[1])
    app = LfsCheckerApp()
    if initial_repo:
        app.repo_path.set(initial_repo)
    app.run()
