"""
CLI: Check LFS — missing objects and local assets not in LFS.
Uses the same logic as lfs_checker.py.
Usage: python check_lfs.py [repo_path]
"""
import sys
from pathlib import Path

# Avoid UnicodeEncodeError when printing paths with non-ASCII chars on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp1252", "cp850", "ascii"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# Use shared logic from main app
from lfs_checker import (
    check_local_assets_not_in_lfs,
    check_missing_lfs,
    find_git_root,
)


def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    repo_root = find_git_root(repo)
    if not repo_root:
        print("Not a Git repo.", file=sys.stderr)
        sys.exit(1)

    missing = check_missing_lfs(repo_root)
    local_not_lfs = check_local_assets_not_in_lfs(repo_root)

    def safe_print(s: str) -> None:
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("utf-8", errors="replace").decode("ascii"))

    if not missing and not local_not_lfs:
        print("OK — all LFS assets present; no local-only assets under LFS patterns.")
        return

    exit_code = 0
    if missing:
        exit_code = 2
        print(f"Missing {len(missing)} LFS object(s):")
        for path, oid in missing:
            safe_print(f"  {path}")
            print(f"    oid sha256:{oid}")
        print()

    if local_not_lfs:
        if exit_code == 0:
            exit_code = 3
        print(f"Local assets not in LFS ({len(local_not_lfs)} file(s) matching .gitattributes):")
        for path in local_not_lfs:
            safe_print(f"  {path}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
