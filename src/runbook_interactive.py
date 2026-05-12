"""
Step-by-step Git LFS push investigation (runbook-aligned).

Used by the GUI wizard and by ``investigate_lfs_push.py --interactive``.
Each step exposes human-readable text *before* confirmation and runs only after approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from runbook_probe import (
    Classification,
    classify_push_output,
    find_git_root,
    get_ahead_behind,
    get_current_branch,
    index_lock_path,
    parse_missing_pairs,
    read_lfs_allow_incomplete_push,
    run_git,
    utc_now_iso,
    wait_for_index_lock_clear,
    write_json,
)


@dataclass
class RunbookSession:
    """Mutable state while stepping through the runbook."""

    repo_path: Path
    remote: str
    refspec: str | None
    report_path: Path
    max_missing: int
    remove_stale_lock: bool
    lock_wait_s: int

    repo_root: Path | None = None
    branch: str | None = None
    refspec_resolved: str | None = None
    classification: Classification | None = None
    terminal: bool = False
    needs_remediation: bool = False
    actions_taken: list[str] = field(default_factory=list)
    step_logs: dict[str, str] = field(default_factory=dict)
    _ahead_behind: dict[str, int] | None = field(init=False, default=None)
    _dry_run: dict[str, Any] = field(
        init=False,
        default_factory=lambda: {"exitCode": None, "missingCount": 0, "missing": []},
    )
    _dry_run_combined: str = field(init=False, default="")
    _allow_incomplete: bool | None = field(init=False, default=None)
    _next_steps: list[str] = field(init=False, default_factory=list)
    _push_degraded: bool = field(init=False, default=False)

    def report_dict(self) -> dict[str, Any]:
        """Current partial or final report (same shape as unattended JSON)."""
        return {
            "repoPath": str(self.repo_root) if self.repo_root else str(self.repo_path),
            "branch": self.branch,
            "remote": self.remote,
            "timestamp": utc_now_iso(),
            "classification": self.classification,
            "aheadBehind": self._ahead_behind,
            "dryRunPush": dict(self._dry_run),
            "lfsAllowIncompletePush": self._allow_incomplete,
            "actionsTaken": list(self.actions_taken),
            "nextSteps": list(self._next_steps),
        }

    def _append_log(self, step_id: str, text: str) -> None:
        self.step_logs[step_id] = text

    def _set_dry_run(self, r: Any) -> None:
        combined = r.combined_text() if hasattr(r, "combined_text") else (getattr(r, "stdout", "") + "\n" + getattr(r, "stderr", ""))
        missing = parse_missing_pairs(combined, max_items=self.max_missing)
        self._dry_run = {
            "exitCode": r.exit_code,
            "missingCount": len(missing),
            "missing": missing,
        }
        self._dry_run_combined = combined


def _default_refspec(repo_root: Path) -> str | None:
    b = get_current_branch(repo_root)
    if not b:
        return None
    return f"HEAD:refs/heads/{b}"


def _finalize_next_steps(session: RunbookSession) -> None:
    c = session.classification or "unknown_push_failure"
    ns: list[str] = []
    if c == "ok":
        ns.append("No issues detected by dry-run push.")
    elif c == "git_non_fast_forward":
        ns.append("Your branch is behind upstream; pull/rebase then retry.")
    elif c == "auth_or_permissions":
        ns.append("Check repo permissions and authentication; retry fetch/push.")
    elif c == "lfs_missing_local_objects":
        ns.append("Some LFS objects referenced by commits are missing locally.")
        ns.append("If missing persists after fetch/pull, recovery requires another clone/machine/backup or history rewrite.")
    elif c in ("repo_locked", "repo_dirty_lock"):
        ns.append("Resolve index.lock (wait for git, or remove stale lock with care), then rerun.")
    elif c == "not_a_git_repo":
        ns.append("Run from inside a Git repository (folder containing .git).")
    else:
        ns.append("Inspect dry-run output and logs; patterns did not match known cases.")
    session._next_steps = ns


def step_intro() -> tuple[str, str]:
    """No git commands."""
    before = (
        "This wizard follows the LFS investigation runbook.\n\n"
        "You will confirm each step before any command runs. "
        "After each step you will see the command output and what comes next."
    )
    next_will = (
        "Next (after you confirm): validate that the selected folder is inside a Git repository "
        "(looks for a .git directory walking up from the path). No changes are made."
    )
    return before, next_will


def step_validate_repo(session: RunbookSession) -> str:
    session.repo_root = find_git_root(session.repo_path)
    if not session.repo_root:
        session.classification = "not_a_git_repo"
        session.terminal = True
        session.actions_taken.append("validate: not a git repo")
        log = "No Git repository found at this path (no .git parent)."
        session._append_log("validate_repo", log)
        return log
    session.actions_taken.append(f"validate: repo root = {session.repo_root}")
    log = f"Repository root: {session.repo_root}"
    session._append_log("validate_repo", log)
    return log


def step_explain_index_lock(session: RunbookSession) -> tuple[str, str]:
    lock = index_lock_path(session.repo_root)  # type: ignore[arg-type]
    before = (
        "Phase 0 — index lock\n\n"
        "Git creates `.git/index.lock` while the index is being updated. "
        "If this file exists, other git commands can fail until the lock clears."
    )
    if lock.exists():
        next_will = (
            f"Next: wait up to {session.lock_wait_s} second(s) for the lock to disappear. "
            "If it remains, we classify as repo_locked (if git appeared active) or repo_dirty_lock. "
            f"Stale removal is {'enabled' if session.remove_stale_lock else 'disabled'} (CLI: --remove-stale-lock)."
        )
    else:
        next_will = "Next: no lock file — we will skip waiting and go to fetching from the remote."
    return before, next_will


def step_run_index_lock(session: RunbookSession) -> str:
    assert session.repo_root is not None
    repo = session.repo_root
    lock = index_lock_path(repo)
    lines: list[str] = []
    if lock.exists():
        session.actions_taken.append("index.lock detected")
        lines.append("index.lock exists — waiting…")
        cleared, saw_git = wait_for_index_lock_clear(repo, timeout_s=session.lock_wait_s)
        if not cleared:
            session.classification = "repo_locked" if saw_git else "repo_dirty_lock"
            session.terminal = True
            session.actions_taken.append(f"lock still present (saw_git_process={saw_git})")
            lines.append("Lock did not clear in time. Stop and resolve before continuing.")
            session._append_log("index_lock", "\n".join(lines))
            return "\n".join(lines)
        session.actions_taken.append("index.lock cleared after wait")
        lines.append("Lock cleared after wait.")

    if lock.exists() and session.remove_stale_lock:
        try:
            lock.unlink()
            session.actions_taken.append("removed stale index.lock")
            lines.append("Removed stale index.lock (policy allowed).")
        except OSError as e:
            session.classification = "repo_dirty_lock"
            session.terminal = True
            lines.append(f"Could not remove lock: {e}")
            session._append_log("index_lock", "\n".join(lines))
            return "\n".join(lines)

    session._append_log("index_lock", "\n".join(lines))
    return "\n".join(lines) if lines else "No index.lock; repository looks runnable."


def step_explain_fetch(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Phase 1 — sync with remote\n\n"
        f"`git fetch {session.remote}` updates remote-tracking branches locally. "
        "It does not merge or change your working files. "
        "If this fails, the problem is usually network or authentication (not LFS object layout)."
    )
    next_will = f"Next: run `git fetch {session.remote}`."
    return before, next_will


def step_run_fetch(session: RunbookSession) -> str:
    assert session.repo_root is not None
    r = run_git(session.repo_root, ["fetch", session.remote], timeout_s=600)
    session.actions_taken.append(f"git fetch {session.remote}")
    log = f"$ git fetch {session.remote}\nexit={r.exit_code}\n"
    log += (r.stdout + "\n" if r.stdout else "") + (r.stderr + "\n" if r.stderr else "")
    if r.exit_code != 0:
        session.classification = "auth_or_permissions"
        session.terminal = True
    session._append_log("fetch", log)
    return log


def step_explain_branch_context(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Branch and tracking context\n\n"
        "We read the current branch name and, if an upstream exists, ahead/behind counts. "
        "This helps interpret push failures (e.g. behind upstream vs LFS missing objects)."
    )
    next_will = (
        "Next: run `git branch --show-current`, upstream resolution, and "
        "`git rev-list --left-right --count` when upstream is set. Read-only."
    )
    return before, next_will


def step_run_branch_context(session: RunbookSession) -> str:
    assert session.repo_root is not None
    session.branch = get_current_branch(session.repo_root)
    session._ahead_behind = get_ahead_behind(session.repo_root)
    if not session.refspec:
        session.refspec_resolved = _default_refspec(session.repo_root)
    else:
        session.refspec_resolved = session.refspec
    lines = [f"branch: {session.branch or '(none)'}", f"refspec: {session.refspec_resolved or '(none)'}"]
    if session._ahead_behind:
        ab = session._ahead_behind
        lines.append(f"ahead/behind vs upstream: +{ab['ahead']} / -{ab['behind']}")
    else:
        lines.append("ahead/behind: (no upstream or could not compute)")
    if not session.refspec_resolved:
        session.classification = "unknown_push_failure"
        session.terminal = True
        lines.append("Detached HEAD or no branch — set refspec manually in wizard/CLI.")
    log = "\n".join(lines)
    session.actions_taken.append("branch context collected")
    session._append_log("branch_context", log)
    return log


def step_explain_dry_run(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Phase 2 — primary probe (safe)\n\n"
        "`git push --dry-run -v` does not change the remote. "
        "It is the most reliable way to surface `(missing) path (oid)` lines and LFS upload errors."
    )
    if not session.refspec_resolved:
        next_will = "Cannot run dry-run: no refspec (fix branch context first)."
        return before, next_will
    next_will = f"Next: `git push --dry-run -v {session.remote} {session.refspec_resolved}`"
    return before, next_will


def step_run_dry_run(session: RunbookSession, *, tag: str = "dry_run_push") -> str:
    assert session.repo_root is not None and session.refspec_resolved
    r = run_git(
        session.repo_root,
        ["push", "--dry-run", "-v", session.remote, session.refspec_resolved],
        timeout_s=600,
    )
    session.actions_taken.append(f"git push --dry-run -v {session.remote} {session.refspec_resolved}")
    combined = r.combined_text()
    session._set_dry_run(r)
    session.classification = classify_push_output(combined, exit_code=r.exit_code)
    session.needs_remediation = session.classification == "lfs_missing_local_objects"
    log = f"$ git push --dry-run -v {session.remote} {session.refspec_resolved}\nexit={r.exit_code}\n{combined}"
    session._append_log(tag, log)
    return log


def step_explain_lfs_fsck(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Phase 3 — LFS integrity (optional but recommended)\n\n"
        "`git lfs fsck` checks local LFS object store consistency and can report missing/corrupt objects."
    )
    next_will = "Next: run `git lfs fsck`."
    return before, next_will


def step_run_lfs_fsck(session: RunbookSession) -> str:
    assert session.repo_root is not None
    r = run_git(session.repo_root, ["lfs", "fsck"], timeout_s=600)
    session.actions_taken.append("git lfs fsck")
    log = f"$ git lfs fsck\nexit={r.exit_code}\n{r.combined_text()}"
    session._append_log("lfs_fsck", log)
    return log


def step_explain_lfs_fetch(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Restore LFS objects from remote\n\n"
        f"`git lfs fetch {session.remote} --recent` downloads recent LFS objects the remote knows about. "
        "This writes under `.git/lfs` locally."
    )
    next_will = f"Next: `git lfs fetch {session.remote} --recent`."
    return before, next_will


def step_run_lfs_fetch(session: RunbookSession) -> str:
    assert session.repo_root is not None
    r = run_git(session.repo_root, ["lfs", "fetch", session.remote, "--recent"], timeout_s=600)
    session.actions_taken.append(f"git lfs fetch {session.remote} --recent")
    log = f"$ git lfs fetch {session.remote} --recent\nexit={r.exit_code}\n{r.combined_text()}"
    session._append_log("lfs_fetch_recent", log)
    return log


def step_explain_lfs_pull(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Apply LFS objects to the working tree\n\n"
        "`git lfs pull` updates your working copy with LFS content after fetch. "
        "It can touch large files; ensure editors are not locking them if you see errors."
    )
    next_will = "Next: run `git lfs pull`."
    return before, next_will


def step_run_lfs_pull(session: RunbookSession) -> str:
    assert session.repo_root is not None
    r = run_git(session.repo_root, ["lfs", "pull"], timeout_s=600)
    session.actions_taken.append("git lfs pull")
    log = f"$ git lfs pull\nexit={r.exit_code}\n{r.combined_text()}"
    session._append_log("lfs_pull", log)
    return log


def step_explain_dry_run_retry(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Re-run the primary probe\n\n"
        "After fetch/pull we run the same dry-run push again to see if missing OIDs disappeared."
    )
    assert session.refspec_resolved
    next_will = f"Next: `git push --dry-run -v {session.remote} {session.refspec_resolved}` (again)."
    return before, next_will


def step_explain_policy(session: RunbookSession) -> tuple[str, str]:
    before = (
        "Phase 4 — policy\n\n"
        "`git config --get lfs.allowincompletepush` controls whether a push may succeed while LFS objects are missing. "
        "If true, pushes can be 'successful' but incomplete for teammates."
    )
    next_will = "Next: read `lfs.allowincompletepush` from git config."
    return before, next_will


def step_run_policy(session: RunbookSession) -> str:
    assert session.repo_root is not None
    allow = read_lfs_allow_incomplete_push(session.repo_root)
    session._allow_incomplete = allow
    session.actions_taken.append("git config --get lfs.allowincompletepush")
    dr = session._dry_run
    if (
        allow is True
        and dr.get("missingCount", 0) > 0
        and dr.get("exitCode") == 0
    ):
        session._push_degraded = True
    else:
        session._push_degraded = False
    log = f"lfs.allowincompletepush = {allow}\n"
    if getattr(session, "_push_degraded", False):
        log += "Flag: pushSucceededButLfsIncomplete = true (dry-run exit 0 but missing lines present).\n"
    session._append_log("policy_allow_incomplete", log)
    return log


def step_explain_write_report(session: RunbookSession) -> tuple[str, str]:
    path = session.report_path
    before = (
        "Write machine-readable report\n\n"
        f"The report will be written to:\n{path.resolve()}\n\n"
        "You can use this file in CI or attach it to an issue."
    )
    next_will = f"Next: write JSON to `{path}`."
    return before, next_will


def step_run_write_report(session: RunbookSession) -> str:
    if session.classification is None:
        session.classification = classify_push_output(
            session._dry_run_combined,
            exit_code=session._dry_run.get("exitCode"),
        )
    _finalize_next_steps(session)
    rep = session.report_dict()
    if session._push_degraded:
        rep["pushSucceededButLfsIncomplete"] = True
    write_json(session.report_path, rep)
    session.actions_taken.append(f"write report: {session.report_path}")
    log = f"Wrote report to {session.report_path.resolve()}"
    session._append_log("write_report", log)
    return log


def _write_report_if_needed(session: RunbookSession, report_path: Path) -> None:
    """Single write path for early abort and skip-write-report."""
    if session.classification is None:
        session.classification = classify_push_output(
            session._dry_run_combined,
            exit_code=session._dry_run.get("exitCode"),
        )
    _finalize_next_steps(session)
    rep = session.report_dict()
    if session._push_degraded:
        rep["pushSucceededButLfsIncomplete"] = True
    write_json(report_path, rep)


def run_interactive_cli(
    repo_path: Path,
    *,
    remote: str,
    refspec: str | None,
    report_path: Path,
    max_missing: int,
    remove_stale_lock: bool,
    lock_wait_s: int,
    writer: Callable[[str], None] | None = None,
    reader: Callable[[str], str] | None = None,
) -> tuple[Classification, dict[str, Any]]:
    """Run full wizard on stdout/stdin. ``reader`` returns line after prompt (for tests)."""
    w = writer or (lambda s: print(s, flush=True))
    rfn = reader or (lambda prompt: input(prompt))

    session = RunbookSession(
        repo_path=repo_path,
        remote=remote,
        refspec=refspec,
        report_path=report_path,
        max_missing=max_missing,
        remove_stale_lock=remove_stale_lock,
        lock_wait_s=lock_wait_s,
    )

    def confirm(before: str, next_will: str) -> bool:
        w("\n" + "=" * 60)
        w(before)
        w("\n--- What happens next (after you approve) ---\n" + next_will)
        ans = rfn("\nPress Enter to run this step, or type 'q' then Enter to quit: ").strip().lower()
        return ans != "q"

    # Intro
    b, n = step_intro()
    if not confirm(b, n):
        session.classification = "unknown_push_failure"
        session.terminal = True
        _write_report_if_needed(session, report_path)
        return session.classification, session.report_dict()

    # validate
    b, n = ("Validate Git repository", "Next: resolve repo root from path (no git command yet).")
    if not confirm(b, n):
        session.classification = "unknown_push_failure"
        session.terminal = True
        _write_report_if_needed(session, report_path)
        return session.classification, session.report_dict()
    w("\n>>> " + step_validate_repo(session))
    if session.terminal:
        _write_report_if_needed(session, report_path)
        return session.classification or "not_a_git_repo", session.report_dict()

    # index lock
    b, n = step_explain_index_lock(session)
    if not confirm(b, n):
        session.classification = "unknown_push_failure"
        session.terminal = True
        _write_report_if_needed(session, report_path)
        return session.classification, session.report_dict()
    w("\n>>> " + step_run_index_lock(session))
    if session.terminal:
        _write_report_if_needed(session, report_path)
        return session.classification or "unknown_push_failure", session.report_dict()

    # fetch
    b, n = step_explain_fetch(session)
    if not confirm(b, n):
        session.classification = "unknown_push_failure"
        session.terminal = True
        _write_report_if_needed(session, report_path)
        return session.classification, session.report_dict()
    w("\n>>> " + step_run_fetch(session))
    if session.terminal:
        _write_report_if_needed(session, report_path)
        return session.classification or "auth_or_permissions", session.report_dict()

    # branch
    b, n = step_explain_branch_context(session)
    if not confirm(b, n):
        session.classification = "unknown_push_failure"
        session.terminal = True
        _write_report_if_needed(session, report_path)
        return session.classification, session.report_dict()
    w("\n>>> " + step_run_branch_context(session))
    if session.terminal:
        _write_report_if_needed(session, report_path)
        return session.classification or "unknown_push_failure", session.report_dict()

    # dry run 1
    b, n = step_explain_dry_run(session)
    if not confirm(b, n):
        session.classification = "unknown_push_failure"
        session.terminal = True
        _write_report_if_needed(session, report_path)
        return session.classification, session.report_dict()
    w("\n>>> " + step_run_dry_run(session, tag="dry_run_push"))

    if session.needs_remediation:
        for expl, runner, title in [
            (step_explain_lfs_fsck, step_run_lfs_fsck, "git lfs fsck"),
            (step_explain_lfs_fetch, step_run_lfs_fetch, "git lfs fetch --recent"),
            (step_explain_lfs_pull, step_run_lfs_pull, "git lfs pull"),
            (step_explain_dry_run_retry, lambda s: step_run_dry_run(s, tag="dry_run_push_retry"), "dry-run again"),
        ]:
            bb, nn = expl(session)
            if not confirm(bb, nn):
                break
            w(f"\n>>> ({title})\n" + runner(session))

    # policy
    b, n = step_explain_policy(session)
    if confirm(b, n):
        w("\n>>> " + step_run_policy(session))

    # write
    b, n = step_explain_write_report(session)
    if confirm(b, n):
        w("\n>>> " + step_run_write_report(session))
    else:
        _write_report_if_needed(session, report_path)

    return session.classification or "unknown_push_failure", session.report_dict()
