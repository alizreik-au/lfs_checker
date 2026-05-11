from __future__ import annotations

import json
from pathlib import Path

import investigate_lfs_push as cli
import runbook_probe as probe


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_investigate_writes_report_when_not_git_repo(tmp_path: Path) -> None:
    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        tmp_path / "not_repo",
        remote="origin",
        refspec=None,
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert classification == "not_a_git_repo"
    assert report_path.is_file()
    on_disk = json.loads(report_path.read_text(encoding="utf-8"))
    assert on_disk["classification"] == "not_a_git_repo"


def test_investigate_classifies_repo_locked(tmp_path: Path, monkeypatch) -> None:
    repo = _mk_repo(tmp_path)
    (repo / ".git" / "index.lock").write_text("", encoding="utf-8")

    def fake_wait(_repo_root: Path, *, timeout_s: int, poll_s: float = 1.0):
        return (False, True)

    monkeypatch.setattr(cli, "wait_for_index_lock_clear", fake_wait)

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec="HEAD:refs/heads/main",
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=1,
    )
    assert classification == "repo_locked"
    assert report["classification"] == "repo_locked"
    assert report_path.is_file()


def test_investigate_fetch_failure_is_auth_or_permissions(tmp_path: Path, monkeypatch) -> None:
    repo = _mk_repo(tmp_path)

    def fake_run_git(repo_root: Path, args: list[str], *, timeout_s: int = 600, env=None) -> probe.CmdResult:
        if args[:2] == ["fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr="403")
        if args[:2] == ["branch", "--show-current"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="main", stderr="")
        return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_git", fake_run_git)

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec="HEAD:refs/heads/main",
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert classification == "auth_or_permissions"
    assert report["classification"] == "auth_or_permissions"
    assert report_path.is_file()


def test_investigate_detects_missing_objects_and_records_pairs(tmp_path: Path, monkeypatch) -> None:
    repo = _mk_repo(tmp_path)

    missing_line = "(missing) Content/Foo.uasset (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)"

    def fake_run_git(repo_root: Path, args: list[str], *, timeout_s: int = 600, env=None) -> probe.CmdResult:
        if args[:2] == ["fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["push", "--dry-run", "-v"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr=missing_line)
        if args[:2] == ["branch", "--show-current"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="main", stderr="")
        if args[:3] == ["config", "--get", "lfs.allowincompletepush"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr="")
        return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_git", fake_run_git)

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec="HEAD:refs/heads/main",
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert classification == "lfs_missing_local_objects"
    assert report["dryRunPush"]["missingCount"] == 1
    assert report["dryRunPush"]["missing"][0]["path"] == "Content/Foo.uasset"
    assert report_path.is_file()


def test_investigate_uses_default_refspec_when_none_provided(tmp_path: Path, monkeypatch) -> None:
    repo = _mk_repo(tmp_path)

    calls: list[list[str]] = []

    def fake_run_git(repo_root: Path, args: list[str], *, timeout_s: int = 600, env=None) -> probe.CmdResult:
        calls.append(args)
        if args[:2] == ["branch", "--show-current"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="main", stderr="")
        if args[:2] == ["fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["push", "--dry-run", "-v"]:
            # Ensure refspec is defaulted correctly.
            assert args[-1] == "HEAD:refs/heads/main"
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="ok", stderr="")
        if args[:3] == ["config", "--get", "lfs.allowincompletepush"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr="")
        return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_git", fake_run_git)
    monkeypatch.setattr(cli, "get_current_branch", lambda _repo_root: "main")

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec=None,
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert classification == "ok"
    assert report["classification"] == "ok"
    assert report_path.is_file()


def test_investigate_sets_degraded_flag_when_allowincomplete_true_and_missing_present_with_success_exit(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _mk_repo(tmp_path)
    missing_line = "(missing) X (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)"

    def fake_run_git(repo_root: Path, args: list[str], *, timeout_s: int = 600, env=None) -> probe.CmdResult:
        if args[:2] == ["branch", "--show-current"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="main", stderr="")
        if args[:2] == ["fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["push", "--dry-run", "-v"]:
            # Exit code 0 but still emits missing lines when allowincompletepush=true
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr=missing_line)
        if args[:3] == ["config", "--get", "lfs.allowincompletepush"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="true", stderr="")
        return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_git", fake_run_git)
    monkeypatch.setattr(cli, "read_lfs_allow_incomplete_push", lambda _repo_root: True)

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec="HEAD:refs/heads/main",
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert classification == "lfs_missing_local_objects"
    assert report.get("pushSucceededButLfsIncomplete") is True


def test_investigate_remediation_loop_reruns_dry_run_and_can_resolve(tmp_path: Path, monkeypatch) -> None:
    repo = _mk_repo(tmp_path)
    missing_line = "(missing) X (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)"
    push_calls = 0

    def fake_run_git(repo_root: Path, args: list[str], *, timeout_s: int = 600, env=None) -> probe.CmdResult:
        nonlocal push_calls
        if args[:2] == ["branch", "--show-current"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="main", stderr="")
        if args[:2] == ["fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["push", "--dry-run", "-v"]:
            push_calls += 1
            if push_calls == 1:
                return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr=missing_line)
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="ok", stderr="")
        if args[:2] == ["lfs", "fsck"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["lfs", "fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:2] == ["lfs", "pull"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["config", "--get", "lfs.allowincompletepush"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr="")
        return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_git", fake_run_git)

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec="HEAD:refs/heads/main",
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert push_calls == 2
    assert classification == "ok"
    assert report["dryRunPush"]["missingCount"] == 0


def test_investigate_truncates_missing_list_to_max_missing(tmp_path: Path, monkeypatch) -> None:
    repo = _mk_repo(tmp_path)
    missing_lines = "\n".join(
        [
            f"(missing) f{i} (0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)"
            for i in range(5)
        ]
    )

    def fake_run_git(repo_root: Path, args: list[str], *, timeout_s: int = 600, env=None) -> probe.CmdResult:
        if args[:2] == ["branch", "--show-current"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="main", stderr="")
        if args[:2] == ["fetch", "origin"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")
        if args[:3] == ["push", "--dry-run", "-v"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr=missing_lines)
        if args[:3] == ["config", "--get", "lfs.allowincompletepush"]:
            return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=1, stdout="", stderr="")
        return probe.CmdResult(args=["git"] + args, cwd=str(repo_root), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "run_git", fake_run_git)

    report_path = tmp_path / "out.json"
    classification, report = cli.investigate(
        repo,
        remote="origin",
        refspec="HEAD:refs/heads/main",
        report_path=report_path,
        max_missing=2,
        remove_stale_lock=False,
        lock_wait_s=0,
    )
    assert classification == "lfs_missing_local_objects"
    assert report["dryRunPush"]["missingCount"] == 2
    assert [m["path"] for m in report["dryRunPush"]["missing"]] == ["f0", "f1"]

