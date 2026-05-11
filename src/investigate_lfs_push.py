from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

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


EXIT_CODES: dict[Classification, int] = {
    "ok": 0,
    "lfs_missing_local_objects": 10,
    "git_non_fast_forward": 20,
    "auth_or_permissions": 30,
    "repo_locked": 40,
    "repo_dirty_lock": 40,
    "unknown_push_failure": 50,
    "not_a_git_repo": 50,
}


def _default_refspec(repo_root: Path) -> str | None:
    branch = get_current_branch(repo_root)
    if not branch:
        return None
    return f"HEAD:refs/heads/{branch}"


def _one_screen_summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"repoPath: {report.get('repoPath')}")
    lines.append(f"branch: {report.get('branch') or '(unknown)'}")
    lines.append(f"remote: {report.get('remote')}")
    ab = report.get("aheadBehind")
    if isinstance(ab, dict) and "ahead" in ab and "behind" in ab:
        lines.append(f"ahead/behind: +{ab['ahead']} / -{ab['behind']}")
    lines.append(f"classification: {report.get('classification')}")
    dr = report.get("dryRunPush") or {}
    if isinstance(dr, dict):
        lines.append(f"dryRunPush.exitCode: {dr.get('exitCode')}")
        lines.append(f"dryRunPush.missingCount: {dr.get('missingCount')}")
    allow = report.get("lfsAllowIncompletePush")
    if allow is not None:
        lines.append(f"lfs.allowincompletepush: {allow}")
    degraded = report.get("pushSucceededButLfsIncomplete")
    if degraded:
        lines.append("degraded: push succeeded but LFS incomplete")
    lines.append("")
    for ns in report.get("nextSteps") or []:
        lines.append(f"- {ns}")
    return "\n".join(lines).rstrip() + "\n"


def investigate(
    repo_path: Path,
    *,
    remote: str,
    refspec: str | None,
    report_path: Path,
    max_missing: int,
    remove_stale_lock: bool,
    lock_wait_s: int,
) -> tuple[Classification, dict[str, Any]]:
    actions: list[str] = []
    next_steps: list[str] = []

    repo_root = find_git_root(repo_path)
    if not repo_root:
        report: dict[str, Any] = {
            "repoPath": str(repo_path),
            "branch": None,
            "remote": remote,
            "timestamp": utc_now_iso(),
            "classification": "not_a_git_repo",
            "aheadBehind": None,
            "dryRunPush": {"exitCode": None, "missingCount": 0, "missing": []},
            "lfsAllowIncompletePush": None,
            "actionsTaken": actions,
            "nextSteps": ["Run from inside a Git repository (folder containing .git)."],
        }
        return ("not_a_git_repo", report)

    report: dict[str, Any] = {
        "repoPath": str(repo_root),
        "branch": None,
        "remote": remote,
        "timestamp": utc_now_iso(),
        "classification": None,
        "aheadBehind": None,
        "dryRunPush": {"exitCode": None, "missingCount": 0, "missing": []},
        "lfsAllowIncompletePush": None,
        "actionsTaken": actions,
        "nextSteps": next_steps,
    }

    # Phase 0: repo runnable state (index lock)
    lock = index_lock_path(repo_root)
    if lock.exists():
        actions.append("index.lock detected")
        cleared, saw_git_process = wait_for_index_lock_clear(repo_root, timeout_s=lock_wait_s)
        if not cleared:
            report["classification"] = "repo_locked" if saw_git_process else "repo_dirty_lock"
            next_steps.append("Wait for any in-progress git operation to finish, then rerun.")
            if not saw_git_process:
                next_steps.append("If you believe the lock is stale, rerun with --remove-stale-lock.")
            return (report["classification"], report)  # type: ignore[return-value]
        actions.append("waited for index.lock to clear")

    if lock.exists() and remove_stale_lock:
        try:
            lock.unlink()
            actions.append("removed stale index.lock")
        except OSError:
            report["classification"] = "repo_dirty_lock"
            next_steps.append("Could not remove .git/index.lock. Remove it manually if stale.")
            return ("repo_dirty_lock", report)

    # Phase 1: sync + branch context
    branch = get_current_branch(repo_root)
    report["branch"] = branch
    report["aheadBehind"] = get_ahead_behind(repo_root)

    fetch = run_git(repo_root, ["fetch", remote], timeout_s=600)
    actions.append(f"git fetch {remote}")
    if fetch.exit_code != 0:
        report["classification"] = "auth_or_permissions"
        next_steps.append("Check your network/VPN and authentication, then rerun.")
        return ("auth_or_permissions", report)

    # Determine refspec
    if not refspec:
        refspec = _default_refspec(repo_root)
    if not refspec:
        report["classification"] = "unknown_push_failure"
        next_steps.append("Detached HEAD or no branch; provide --refspec explicitly.")
        return ("unknown_push_failure", report)

    # Phase 2: primary probe
    push_probe = run_git(repo_root, ["push", "--dry-run", "-v", remote, refspec], timeout_s=600)
    actions.append(f"git push --dry-run -v {remote} {refspec}")
    combined = push_probe.combined_text()
    missing = parse_missing_pairs(combined, max_items=max_missing)
    classification = classify_push_output(combined, exit_code=push_probe.exit_code)
    report["dryRunPush"] = {
        "exitCode": push_probe.exit_code,
        "missingCount": len(missing),
        "missing": missing,
    }

    # Phase 3: optional LFS remediation loop
    if classification == "lfs_missing_local_objects":
        fsck = run_git(repo_root, ["lfs", "fsck"], timeout_s=600)
        actions.append("git lfs fsck")
        if fsck.exit_code != 0:
            actions.append("git lfs fsck reported issues")
        _ = run_git(repo_root, ["lfs", "fetch", remote, "--recent"], timeout_s=600)
        actions.append(f"git lfs fetch {remote} --recent")
        _ = run_git(repo_root, ["lfs", "pull"], timeout_s=600)
        actions.append("git lfs pull")

        # Re-run probe
        push_probe2 = run_git(repo_root, ["push", "--dry-run", "-v", remote, refspec], timeout_s=600)
        actions.append("re-ran dry-run push after lfs fetch/pull")
        combined2 = push_probe2.combined_text()
        missing2 = parse_missing_pairs(combined2, max_items=max_missing)
        classification = classify_push_output(combined2, exit_code=push_probe2.exit_code)
        report["dryRunPush"] = {
            "exitCode": push_probe2.exit_code,
            "missingCount": len(missing2),
            "missing": missing2,
        }

    # Phase 4: policy check
    allow = read_lfs_allow_incomplete_push(repo_root)
    report["lfsAllowIncompletePush"] = allow
    if allow is True and report["dryRunPush"]["missingCount"] > 0 and push_probe.exit_code == 0:
        report["pushSucceededButLfsIncomplete"] = True

    report["classification"] = classification

    # next steps
    if classification == "ok":
        next_steps.append("No issues detected by dry-run push.")
    elif classification == "git_non_fast_forward":
        next_steps.append("Your branch is behind upstream; pull/rebase then retry.")
    elif classification == "auth_or_permissions":
        next_steps.append("Check repo permissions and authentication; retry fetch/push.")
    elif classification == "lfs_missing_local_objects":
        next_steps.append("Some LFS objects referenced by commits are missing locally.")
        next_steps.append("If missing persists after fetch/pull, recovery requires another clone/machine/backup or history rewrite.")
    else:
        next_steps.append("Inspect dry-run output and logs; patterns did not match known cases.")

    write_json(report_path, report)
    return (classification, report)


def main() -> None:
    p = argparse.ArgumentParser(description="Unattended Git LFS push investigation (runbook-aligned).")
    p.add_argument("repo_path", nargs="?", default=".", help="Path inside the target repo (default: current directory)")
    p.add_argument("--remote", default="origin", help="Remote name (default: origin)")
    p.add_argument("--refspec", default=None, help="Refspec for dry-run push (default: HEAD:refs/heads/<branch>)")
    p.add_argument("--report", default="lfs_check_report.json", help="Output JSON report path")
    p.add_argument("--max-missing", type=int, default=200, help="Max missing entries to include in report (default: 200)")
    p.add_argument("--remove-stale-lock", action="store_true", help="Remove .git/index.lock if present and considered stale")
    p.add_argument("--lock-wait-s", type=int, default=15, help="Seconds to wait for index.lock to clear (default: 15)")
    args = p.parse_args()

    repo_path = Path(args.repo_path)
    report_path = Path(args.report)

    classification, report = investigate(
        repo_path,
        remote=args.remote,
        refspec=args.refspec,
        report_path=report_path,
        max_missing=max(1, args.max_missing),
        remove_stale_lock=bool(args.remove_stale_lock),
        lock_wait_s=max(0, args.lock_wait_s),
    )
    sys.stdout.write(_one_screen_summary(report))
    sys.exit(EXIT_CODES.get(classification, 50))


if __name__ == "__main__":
    main()

