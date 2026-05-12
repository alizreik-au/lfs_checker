"""
Tkinter step-by-step runbook wizard (human verification between each git step).
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable

import runbook_interactive as rib


def _combine_show(t: tuple[str, str] | str) -> str:
    if isinstance(t, tuple):
        b, n = t
        return f"{b}\n\n--- What happens next (after you confirm this step) ---\n{n}"
    return t


class RunbookWizard(tk.Toplevel):
    """Runbook investigation with explanation before each action and output after."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        repo_path: Path,
        remote: str,
        refspec: str | None,
        report_path: Path,
        max_missing: int,
        remove_stale_lock: bool,
        lock_wait_s: int,
    ) -> None:
        super().__init__(master)
        self.title("LFS runbook — step by step")
        self.transient(master)
        self.geometry("720x640")
        self.minsize(560, 480)

        self.session = rib.RunbookSession(
            repo_path=repo_path,
            remote=remote,
            refspec=refspec or None,
            report_path=report_path,
            max_missing=max_missing,
            remove_stale_lock=remove_stale_lock,
            lock_wait_s=lock_wait_s,
        )
        self._steps = self._build_steps()
        self._step_i = 0

        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Options (set before starting; used for fetch / push / report path)").pack(anchor=tk.W)
        row = ttk.Frame(top)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="Remote:").grid(row=0, column=0, sticky=tk.W)
        self.var_remote = tk.StringVar(value=remote)
        ttk.Entry(row, textvariable=self.var_remote, width=12).grid(row=0, column=1, padx=4)
        ttk.Label(row, text="Refspec (optional):").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self.var_refspec = tk.StringVar(value=refspec or "")
        ttk.Entry(row, textvariable=self.var_refspec, width=28).grid(row=0, column=3, padx=4)
        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X, pady=4)
        ttk.Label(row2, text="Report file:").grid(row=0, column=0, sticky=tk.W)
        self.var_report = tk.StringVar(value=str(report_path))
        ttk.Entry(row2, textvariable=self.var_report, width=50).grid(row=0, column=1, columnspan=3, sticky=tk.EW, padx=4)
        row2.columnconfigure(1, weight=1)
        row3 = ttk.Frame(top)
        row3.pack(fill=tk.X, pady=4)
        ttk.Label(row3, text="Max missing in JSON:").grid(row=0, column=0, sticky=tk.W)
        self.var_max_missing = tk.StringVar(value=str(max_missing))
        ttk.Entry(row3, textvariable=self.var_max_missing, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(row3, text="Lock wait (s):").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self.var_lock_wait = tk.StringVar(value=str(lock_wait_s))
        ttk.Entry(row3, textvariable=self.var_lock_wait, width=6).grid(row=0, column=3, padx=4)
        self.var_remove_stale = tk.BooleanVar(value=remove_stale_lock)
        ttk.Checkbutton(row3, text="Remove stale index.lock (if still present after wait)", variable=self.var_remove_stale).grid(
            row=0, column=4, padx=(12, 0)
        )

        ttk.Label(self, text="Current step (read before you run)", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=8)
        self.brief = scrolledtext.ScrolledText(self, height=10, font=("Segoe UI", 10), wrap=tk.WORD, state=tk.DISABLED)
        self.brief.pack(fill=tk.BOTH, expand=False, padx=8, pady=4)

        ttk.Label(self, text="Command output", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=8)
        self.out = scrolledtext.ScrolledText(self, height=14, font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED)
        self.out.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        btn = ttk.Frame(self, padding=8)
        btn.pack(fill=tk.X)
        self.btn_go = ttk.Button(btn, text="Run this step", command=self._on_go)
        self.btn_go.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn, text="Close wizard", command=self.destroy).pack(side=tk.LEFT)

        self._apply_options_to_session()
        self._show_step_briefing()
        self._install_tooltips()

    def _install_tooltips(self) -> None:
        ToolTip(self.btn_go, "Runs the git command for this step (if any), appends output below, then advances when safe.")
        ToolTip(self.var_remote, "Remote name passed to git fetch and git push --dry-run (default: origin).")
        ToolTip(self.var_refspec, "Optional refspec for dry-run push. Leave empty to use HEAD:refs/heads/<current-branch>.")
        ToolTip(self.var_report, "Path for lfs_check_report.json (written in the final step if you proceed).")
        ToolTip(self.var_max_missing, "Maximum number of (missing) path/OID pairs stored in the JSON report.")
        ToolTip(self.var_lock_wait, "Seconds to wait for .git/index.lock to disappear before failing.")
        ToolTip(self.var_remove_stale, "If enabled and lock remains with no active git, delete index.lock (use only when sure it is stale).")

    def _apply_options_to_session(self) -> None:
        self.session.remote = self.var_remote.get().strip() or "origin"
        rs = self.var_refspec.get().strip()
        self.session.refspec = rs or None
        self.session.report_path = Path(self.var_report.get().strip() or "lfs_check_report.json")
        try:
            self.session.max_missing = max(1, int(self.var_max_missing.get().strip() or "200"))
        except ValueError:
            self.session.max_missing = 200
        try:
            self.session.lock_wait_s = max(0, int(self.var_lock_wait.get().strip() or "15"))
        except ValueError:
            self.session.lock_wait_s = 15
        self.session.remove_stale_lock = bool(self.var_remove_stale.get())

    def _build_steps(self) -> list[dict[str, Any]]:

        def fsck_show(s: rib.RunbookSession) -> tuple[str, str]:
            if not s.needs_remediation:
                return (
                    "LFS remediation — skipped",
                    "The dry-run did not classify as missing local LFS objects, so fsck/fetch/pull/re-dry-run are skipped. "
                    "Next: read `lfs.allowincompletepush` policy.",
                )
            return rib.step_explain_lfs_fsck(s)

        def fsck_run(s: rib.RunbookSession) -> str:
            if not s.needs_remediation:
                return "(no command — skipped)\n"
            return rib.step_run_lfs_fsck(s)

        def fetch_show(s: rib.RunbookSession) -> tuple[str, str]:
            if not s.needs_remediation:
                return ("git lfs fetch — skipped", "Next: policy step.")
            return rib.step_explain_lfs_fetch(s)

        def fetch_run(s: rib.RunbookSession) -> str:
            if not s.needs_remediation:
                return "(no command — skipped)\n"
            return rib.step_run_lfs_fetch(s)

        def pull_show(s: rib.RunbookSession) -> tuple[str, str]:
            if not s.needs_remediation:
                return ("git lfs pull — skipped", "Next: policy step.")
            return rib.step_explain_lfs_pull(s)

        def pull_run(s: rib.RunbookSession) -> str:
            if not s.needs_remediation:
                return "(no command — skipped)\n"
            return rib.step_run_lfs_pull(s)

        def retry_show(s: rib.RunbookSession) -> tuple[str, str]:
            if not s.needs_remediation:
                return ("Dry-run retry — skipped", "Next: read `lfs.allowincompletepush` policy.")
            return rib.step_explain_dry_run_retry(s)

        def retry_run(s: rib.RunbookSession) -> str:
            if not s.needs_remediation:
                return "(no command — skipped)\n"
            return rib.step_run_dry_run(s, tag="dry_run_push_retry")

        return [
            {
                "title": "Introduction",
                "show": lambda _: _combine_show(rib.step_intro()),
                "run": None,
            },
            {
                "title": "Validate repository",
                "show": lambda _: _combine_show(
                    (
                        "Validate Git repository",
                        "Resolve the repo root by walking up from your folder until a `.git` directory is found. "
                        "No shell commands run in this step.",
                    )
                ),
                "run": rib.step_validate_repo,
            },
            {
                "title": "Index lock (Phase 0)",
                "show": lambda s: _combine_show(rib.step_explain_index_lock(s)),
                "run": rib.step_run_index_lock,
            },
            {
                "title": "Fetch from remote",
                "show": lambda s: _combine_show(rib.step_explain_fetch(s)),
                "run": rib.step_run_fetch,
            },
            {
                "title": "Branch and tracking",
                "show": lambda s: _combine_show(rib.step_explain_branch_context(s)),
                "run": rib.step_run_branch_context,
            },
            {
                "title": "Dry-run push (primary probe)",
                "show": lambda s: _combine_show(rib.step_explain_dry_run(s)),
                "run": lambda s: rib.step_run_dry_run(s, tag="dry_run_push"),
            },
            {"title": "git lfs fsck", "show": fsck_show, "run": fsck_run},
            {"title": "git lfs fetch --recent", "show": fetch_show, "run": fetch_run},
            {"title": "git lfs pull", "show": pull_show, "run": pull_run},
            {"title": "Dry-run push (again)", "show": retry_show, "run": retry_run},
            {
                "title": "Policy: lfs.allowincompletepush",
                "show": lambda s: _combine_show(rib.step_explain_policy(s)),
                "run": rib.step_run_policy,
            },
            {
                "title": "Write JSON report",
                "show": lambda s: _combine_show(rib.step_explain_write_report(s)),
                "run": rib.step_run_write_report,
            },
        ]

    def _show_step_briefing(self) -> None:
        if self._step_i >= len(self._steps):
            self._finish_ok()
            return
        st = self._steps[self._step_i]
        text = st["show"](self.session)
        self.brief.config(state=tk.NORMAL)
        self.brief.delete("1.0", tk.END)
        self.brief.insert(tk.END, f"Step {self._step_i + 1} / {len(self._steps)}: {st['title']}\n\n{text}")
        self.brief.config(state=tk.DISABLED)
        run_fn = st.get("run")
        self.btn_go.config(text="Continue (no git command)" if run_fn is None else "Run this step")

    def _append_out(self, s: str) -> None:
        self.out.config(state=tk.NORMAL)
        self.out.insert(tk.END, s + "\n")
        self.out.see(tk.END)
        self.out.config(state=tk.DISABLED)

    def _on_go(self) -> None:
        self._apply_options_to_session()
        if self._step_i >= len(self._steps):
            self.destroy()
            return
        st = self._steps[self._step_i]
        run_fn: Callable[[rib.RunbookSession], str] | None = st.get("run")
        if run_fn is not None:
            try:
                log = run_fn(self.session)
            except Exception as e:
                messagebox.showerror("Runbook wizard", str(e), parent=self)
                return
            self._append_out(log)
            if self.session.terminal:
                rib._write_report_if_needed(self.session, self.session.report_path)
                messagebox.showinfo(
                    "Runbook wizard",
                    "Stopped: a terminal condition was detected. A JSON report was written (or updated).",
                    parent=self,
                )
                self.destroy()
                return
        self._step_i += 1
        if self._step_i >= len(self._steps):
            self._finish_ok()
            return
        self._show_step_briefing()

    def _finish_ok(self) -> None:
        messagebox.showinfo(
            "Runbook wizard",
            f"All steps finished.\nReport: {self.session.report_path.resolve()}",
            parent=self,
        )
        self.destroy()


class ToolTip:
    """Show a short explanation when the pointer hovers over a widget."""

    def __init__(self, widget: tk.Widget, text: str, wraplength: int = 440) -> None:
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _ev: object = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(500, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Segoe UI", 9),
            wraplength=self.wraplength,
        )
        lbl.pack(ipadx=5, ipady=3)

    def _hide(self, _ev: object = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
