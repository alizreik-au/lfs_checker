from __future__ import annotations

import json
from pathlib import Path

import runbook_interactive as ri


def test_interactive_quit_at_intro_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "out.json"
    responses = iter(["q"])

    def reader(_prompt: str) -> str:
        return next(responses)

    logs: list[str] = []

    def writer(s: str) -> None:
        logs.append(s)

    classification, _rep = ri.run_interactive_cli(
        tmp_path / "not_a_repo_dir",
        remote="origin",
        refspec=None,
        report_path=report_path,
        max_missing=10,
        remove_stale_lock=False,
        lock_wait_s=0,
        writer=writer,
        reader=reader,
    )
    assert classification == "unknown_push_failure"
    assert report_path.is_file()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["classification"] == "unknown_push_failure"
    assert any("What happens next" in x for x in logs)
