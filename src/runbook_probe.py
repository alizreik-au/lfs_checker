from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


Classification = Literal[
    "ok",
    "lfs_missing_local_objects",
    "git_non_fast_forward",
    "auth_or_permissions",
    "repo_locked",
    "repo_dirty_lock",
    "unknown_push_failure",
    "not_a_git_repo",
]


_RE_LFS_MISSING = re.compile(
    r"(git lfs upload missing objects|git lfs upload failed|missing or corrupt local objects)",
    re.IGNORECASE,
)
_RE_MISSING_LINE = re.compile(r"\(missing\)\s+(.+?)\s+\(([0-9a-f]{64})\)", re.IGNORECASE)
_RE_NON_FAST_FORWARD = re.compile(
    r"(non-fast-forward|fetch first|rejected.*non-fast-forward)",
    re.IGNORECASE,
)
_RE_AUTH = re.compile(
    r"(permission denied|denied to .*git-receive-pack|\b401\b|\b403\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CmdResult:
    args: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def combined_text(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(
    repo_root: Path,
    args: list[str],
    *,
    timeout_s: int = 600,
    env: dict[str, str] | None = None,
) -> CmdResult:
    cmd = ["git"] + args
    try:
        r = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env={**os.environ, **(env or {})},
            check=False,
        )
        return CmdResult(
            args=cmd,
            cwd=str(repo_root),
            exit_code=r.returncode,
            stdout=(r.stdout or "").strip(),
            stderr=(r.stderr or "").strip(),
        )
    except subprocess.TimeoutExpired as e:
        return CmdResult(
            args=cmd,
            cwd=str(repo_root),
            exit_code=124,
            stdout=((e.stdout or "") if isinstance(e.stdout, str) else "").strip(),
            stderr=((e.stderr or "") if isinstance(e.stderr, str) else "").strip(),
            timed_out=True,
        )


def parse_missing_pairs(text: str, *, max_items: int | None = None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in _RE_MISSING_LINE.finditer(text or ""):
        out.append({"path": m.group(1).strip(), "oid": m.group(2).lower()})
        if max_items is not None and len(out) >= max_items:
            break
    return out


def classify_push_output(text: str, *, exit_code: int | None = None) -> Classification:
    if _RE_AUTH.search(text or ""):
        return "auth_or_permissions"
    if _RE_NON_FAST_FORWARD.search(text or ""):
        return "git_non_fast_forward"
    if _RE_LFS_MISSING.search(text or "") or _RE_MISSING_LINE.search(text or ""):
        return "lfs_missing_local_objects"
    if exit_code is not None and exit_code == 0:
        return "ok"
    return "unknown_push_failure"


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return current if (current / ".git").exists() else None


def get_current_branch(repo_root: Path) -> str | None:
    r = run_git(repo_root, ["branch", "--show-current"], timeout_s=30)
    name = (r.stdout or "").strip()
    return name or None


def get_upstream(repo_root: Path) -> str | None:
    r = run_git(repo_root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout_s=30)
    upstream = (r.stdout or "").strip()
    return upstream or None


def get_ahead_behind(repo_root: Path) -> dict[str, int] | None:
    upstream = get_upstream(repo_root)
    if not upstream:
        return None
    r = run_git(repo_root, ["rev-list", "--left-right", "--count", f"{upstream}...HEAD"], timeout_s=30)
    if r.exit_code != 0:
        return None
    parts = (r.stdout or "").strip().split()
    if len(parts) != 2:
        return None
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except ValueError:
        return None
    return {"ahead": ahead, "behind": behind}


def read_lfs_allow_incomplete_push(repo_root: Path) -> bool | None:
    r = run_git(repo_root, ["config", "--get", "lfs.allowincompletepush"], timeout_s=30)
    if r.exit_code != 0:
        return None
    v = (r.stdout or "").strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


def index_lock_path(repo_root: Path) -> Path:
    return repo_root / ".git" / "index.lock"


def is_any_git_process_running_windows() -> bool:
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq git.exe"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
        return "git.exe" in text.lower()
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return False


def wait_for_index_lock_clear(
    repo_root: Path,
    *,
    timeout_s: int,
    poll_s: float = 1.0,
) -> tuple[bool, bool]:
    """
    Returns (cleared, saw_git_process).
    Best-effort on Windows: treats presence of any git.exe as a signal.
    """
    lock = index_lock_path(repo_root)
    saw_git = False
    deadline = time.time() + max(0, timeout_s)
    while lock.exists() and time.time() < deadline:
        if os.name == "nt" and is_any_git_process_running_windows():
            saw_git = True
        time.sleep(max(0.1, poll_s))
    return (not lock.exists(), saw_git)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

