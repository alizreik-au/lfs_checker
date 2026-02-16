"""
Check for files that SHOULD be in LFS (per .gitattributes) but are real files, not LFS pointers.
Use this on any PC/repo to find assets that need to be migrated to LFS.

Usage:
  python check_not_in_lfs.py [repo_path]

If repo_path is omitted, uses current directory (must be inside a Git repo).
Exit code: 0 if none found, 1 if any files are not in LFS.
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path

# LFS pointer detection (same as lfs_checker)
OID_RE = re.compile(r"^oid sha256:([0-9a-f]{64})\s*$", re.IGNORECASE)
VERSION_LINE = "version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer_file(filepath: Path, max_size: int = 500) -> bool:
    """True if file looks like a Git LFS pointer."""
    try:
        if filepath.stat().st_size > max_size:
            return False
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return False
    if len(lines) < 2 or VERSION_LINE not in lines[0]:
        return False
    for line in lines[1:]:
        if OID_RE.match(line.strip()):
            return True
    return False


def find_git_root(start: Path) -> Path | None:
    """Return repo root (directory containing .git) or None."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return current if (current / ".git").exists() else None


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
    """True if relative path matches this .gitattributes LFS pattern."""
    posix = rel_path.as_posix().replace("\\", "/")
    posix_lower = posix.lower()
    if "/" in pattern:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].lower()
            return posix_lower == prefix or posix_lower.startswith(prefix + "/")
        return fnmatch.fnmatch(posix_lower, pattern.lower())
    return fnmatch.fnmatch(rel_path.name.lower(), pattern.lower())


def check_local_assets_not_in_lfs(repo_root: Path) -> list[str]:
    """Paths that match LFS patterns in .gitattributes but are NOT LFS pointers."""
    gitattributes = repo_root / ".gitattributes"
    if not gitattributes.is_file():
        return []
    patterns = parse_lfs_patterns(gitattributes)
    if not patterns:
        return []
    git_dir = repo_root / ".git"
    result: list[str] = []
    for root, _dirs, files in os.walk(repo_root):
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
            result.append(rel.as_posix())
    return sorted(result)


def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    repo_root = find_git_root(repo)
    if not repo_root:
        print("Not a Git repo.", file=sys.stderr)
        sys.exit(2)
    not_in_lfs = check_local_assets_not_in_lfs(repo_root)
    if not not_in_lfs:
        print("OK — no files matching LFS patterns are stored as real files (all are LFS pointers).")
        sys.exit(0)
    print(f"Found {len(not_in_lfs)} file(s) that match .gitattributes LFS patterns but are NOT in LFS:\n")
    for path in not_in_lfs:
        print(path)
    print(f"\nTotal: {len(not_in_lfs)}. Migrate with: git lfs migrate import --include=\"...\" then commit and push.")
    sys.exit(1)


if __name__ == "__main__":
    main()
