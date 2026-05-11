# Git LFS Investigation Script Runbook (Unattended)

This document describes how to build a **non-interactive** script that detects and classifies Git LFS push/pull problems like:

- `Git LFS upload failed`
- `Git LFS upload missing objects`
- `(missing) <path> (<oid>)`
- `Your push was rejected due to missing or corrupt local objects`

It focuses on **how to run checks in order**, **how to detect results**, and **what safe actions** the script can take automatically.

---

## Goals and non-goals

### Goals

- **Identify** whether a failure is **Git divergence** vs **LFS missing/corrupt objects** vs **environment/locks**.
- Produce a **machine-readable report** (JSON) plus a short human-readable summary.
- Be **idempotent**: safe to run repeatedly on a repo without creating extra changes.
- Avoid “guessing”: prefer **deterministic signals** (exit codes + known error strings).

### Non-goals

- Not a full “auto-heal everything” tool (some LFS problems require blobs from another machine/backup).
- Not a replacement for repo policy decisions (e.g., whether incomplete pushes are allowed).

---

## Key signals (what the script learns from)

### Deterministic signals

- **Exit codes** of git commands.
- **Known stderr patterns** (regex) indicating LFS vs normal Git failures.
- **Branch ancestry** and ahead/behind counts.
- Presence of **`.git/index.lock`** and running `git.exe` processes.
- `git config lfs.allowincompletepush` value (changes meaning of a “successful” push).

### Why “verbose dry-run push” is the primary probe

The most reliable way to reveal LFS upload issues is:

- `git push --dry-run -v origin <refspec>`

Because it tends to emit the concrete `(missing)` list with **paths and OIDs**, whereas a plain `git push` often collapses to:

- `error: failed to push some refs`

---

## Recommended command order and how to interpret results

This is the recommended **unattended** decision pipeline.

### Phase 0: Ensure the repo is in a runnable state

1. **Validate repo root**
   - Check `.git/` exists (or `git rev-parse --git-dir` succeeds).

2. **Detect active/blocked Git operations**
   - If `.git/index.lock` exists:
     - If any `git` process is running in that repo, **wait with timeout**, then fail with a “repo locked” classification.
     - If no git process is running, treat as **stale lock**; remove it only if your automation policy allows.

**Classification outcomes**

- `repo_locked`: lock exists and active git process present beyond timeout.
- `repo_dirty_lock`: lock exists but no git process; safe cleanup may fix it.

### Phase 1: Sync and branch context

3. `git fetch origin`
   - If this fails, stop early: this is **auth/network** (not LFS).

4. Determine current branch and tracking:
   - `git branch --show-current`
   - `git status -sb`

**Interpretation**

- If branch is **behind** its upstream: push may fail for normal Git reasons; fix via pull/rebase (policy-dependent).
- If branch is **ahead only** (or you are pushing to a new branch) and push still fails, suspect **LFS or permissions/hooks**.

### Phase 2: High-signal probe (detect LFS vs Git)

5. Run a verbose dry-run push (do not mutate remote):

Recommended refspec patterns:

- Pushing current branch to same name:
  - `git push --dry-run -v origin HEAD:refs/heads/<branch>`
- Or “what would happen if I push my branch name”:
  - `git push --dry-run -v origin <branch>:<branch>`

**Detect LFS problems by regex on stderr/stdout**

- LFS missing upload objects:
  - `Git LFS upload missing objects`
  - `Git LFS upload failed`
  - `\\(missing\\) .* \\([0-9a-f]{64}\\)`
  - `missing or corrupt local objects`

- Normal Git push failures (not LFS):
  - `non-fast-forward`
  - `fetch first`
  - `permission denied`
  - `protected branch hook declined`

**Classification outcomes**

- `lfs_missing_local_objects`: `(missing)` OIDs are being referenced but not present locally.
- `git_non_fast_forward`: typical behind/upstream failure.
- `auth_or_permissions`: 401/403, “permission denied”, etc.
- `unknown_push_failure`: push failed but no known patterns matched; keep logs for inspection.

### Phase 3: Local LFS integrity checks (optional but recommended)

If phase 2 indicates LFS problems:

6. `git lfs fsck`
   - If it reports missing/corrupt objects, you have strong confirmation.

7. Attempt to restore from remote:
   - `git lfs fetch origin --recent` (or `--all` if you can afford it)
   - `git lfs pull`

Then re-run the **dry-run push**.

**Interpretation**

- If missing list disappears: the issue was an incomplete local LFS cache.
- If missing list persists: the missing OIDs likely **do not exist on the remote** (or not under your reachable refs); recovery must come from another clone/machine/backup or by rewriting history to stop referencing them.

### Phase 4: Policy check — “incomplete pushes”

8. Read:
   - `git config --get lfs.allowincompletepush`

**Interpretation**

- If `true`: pushes may “succeed” while emitting `(missing)` lines.
  - Your script should still mark the run as **degraded** for team readiness.
  - Emit a report flag: `pushSucceededButLfsIncomplete: true`.

---

## What “fixes” can be done automatically (safe vs unsafe)

### Safe automatic actions

- **Fetch/pull refs**: `git fetch origin`
- **Verbose dry-run push** for diagnosis
- **`git lfs fetch` / `git lfs pull`** (read-only remote side effects; local-only writes)
- **Detect and report**: list missing `(path, oid)` pairs and counts
- **Exit with meaningful codes** so CI/hooks can block or warn

### Conditionally safe (policy/guardrail required)

- Remove stale `.git/index.lock`
  - Only if no `git` process is running and you have strong evidence the lock is stale.
- Retry pushes
  - Only a limited number of times, with exponential backoff, and only for transient errors.

### Unsafe to do without human or explicit flags

- Setting `lfs.allowincompletepush true`
  - This changes repository behavior and can make missing blobs everyone else’s problem.
- Rewriting history (squash/rebase/filter-branch) to remove bad pointers
  - Powerful but disruptive; should be explicit and audited.
- Killing arbitrary `git` processes
  - Can corrupt a legitimate in-progress operation if done blindly.

---

## Output format (recommended)

Write `lfs_check_report.json` with fields like:

- `repoPath`
- `branch`
- `remote`
- `timestamp`
- `classification` (one of the outcomes above)
- `aheadBehind` (if available)
- `dryRunPush`:
  - `exitCode`
  - `missingCount`
  - `missing` (array of `{ path, oid }`, truncated to a max count)
- `lfsAllowIncompletePush` (true/false/null)
- `actionsTaken` (array of strings)
- `nextSteps` (array of strings; human-readable)

Also print a one-screen summary to stdout for quick viewing.

---

## Minimal pseudo-code structure

```text
main(repoPath, remote="origin"):
  assert isGitRepo(repoPath)

  if indexLockExists(repoPath):
    if gitProcessRunning(repoPath):
      return report(repo_locked)
    else:
      maybeRemoveStaleLock()

  run("git fetch origin") or return report(auth_or_network)

  ctx = getBranchAndTracking()

  pushProbe = run("git push --dry-run -v origin HEAD:refs/heads/<branch>")
  classification = classify(pushProbe.output)

  if classification == lfs_missing_local_objects:
    fsck = run("git lfs fsck") (optional)
    run("git lfs fetch origin --recent")
    run("git lfs pull")
    pushProbe2 = run("git push --dry-run -v ...")
    classification = classify(pushProbe2.output) (maybe resolved)

  config = read("git config --get lfs.allowincompletepush")

  writeReport()
  exit(codeFor(classification))
```

---

## Practical detection regexes

Use case-insensitive matching.

- LFS missing objects:
  - `Git LFS upload missing objects`
  - `Git LFS upload failed`
  - `\\(missing\\)\\s+.+\\s+\\([0-9a-f]{64}\\)`
  - `missing or corrupt local objects`

- Non-fast-forward:
  - `non-fast-forward`
  - `fetch first`
  - `rejected.*non-fast-forward`

- Permissions/auth:
  - `403`
  - `401`
  - `permission denied`
  - `denied to .*git-receive-pack`

---

## Notes for Windows + Unreal repos

- `.uasset` / `.umap` files are frequently **locked** by Unreal Editor or indexing tools.
  - Prefer investigative commands that do not require checking out old commits.
  - If you must “squash from a base,” consider approaches that avoid heavy disk churn (e.g., plumbing-based commit creation) **only when policy allows**.

---

## Recommended exit codes

- `0`: no issues detected
- `10`: `lfs_missing_local_objects`
- `20`: `git_non_fast_forward`
- `30`: `auth_or_permissions`
- `40`: `repo_locked`
- `50`: unknown / unclassified failure

