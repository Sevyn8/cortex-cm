# Git cheat sheet — Ithina Admin Backend

> Practical git commands for the 10-day build, grouped by use case. Solo developer, working mostly on `main`.

---

## Daily workflow (most-used)

After Claude Code completes a step, it proposes the commit. You say "yes" and these run via bash tool. In practice these execute 30+ times across the project.

```bash
git status                                  # see what's staged and unstaged
git add -A                                  # stage all changes
git commit -m "Step <id>: <description>"    # commit with structured message
```

End-of-day:

```bash
git push                                    # push current branch to remote
```

---

## Commit message convention

```
Step <id>: <one-line description>

<optional bullets for multi-aspect steps>
```

Example:

```
Step 1.5: smoke test script with cross-tenant RLS verification

- 11 assertions covering tenant isolation, FK integrity, CHECK constraints
- Self-contained: creates and rolls back own test data
- Handles FORCE RLS by setting app.tenant_id before SELECTs
```

---

## Setup (one-time)

After creating a GitHub repo via web UI:

```bash
git remote add origin <url>                 # connect local to remote
git branch -M main                          # ensure branch is named main
git push -u origin main                     # first push, sets upstream tracking
```

After this, `git push` alone works (no `-u origin main` needed).

---

## Inspection

See state, history, who changed what.

```bash
git status                                  # current state of working tree
git diff                                    # unstaged changes
git diff --staged                           # staged changes (about to be committed)
git log --oneline                           # commit history, one line each
git log --oneline -20                       # last 20 commits
git log -p <file>                           # full diff history of a file
git show HEAD                               # show last commit's full changes
git show <commit-hash>                      # show a specific commit
git blame <file>                            # who changed each line and when
```

---

## Undoing things

Listed by destructiveness, safest first.

### Safe undos (don't lose work)

```bash
git reset HEAD <file>                       # unstage a file (keep changes)
git restore --staged <file>                 # same thing, modern syntax
git revert HEAD                             # undo last commit by creating inverse commit
                                            # (preserves history; safe to push)
```

### Modify the last commit

```bash
git commit --amend -m "Better message"      # change last commit message
git commit --amend --no-edit                # add staged changes to last commit
                                            # (use only if not yet pushed)
```

If you've already pushed and need to amend:

```bash
git push --force-with-lease                 # safer than --force
                                            # (refuses if remote was updated by someone else)
```

### Discard uncommitted changes

```bash
git checkout -- <file>                      # discard unstaged changes to a file
git restore <file>                          # same thing, modern syntax
git reset --hard HEAD                       # discard ALL unstaged changes (destructive)
git clean -fd                               # remove untracked files and directories (destructive)
```

### Undo a commit

```bash
git reset --soft HEAD~1                     # undo last commit, keep changes staged
git reset --mixed HEAD~1                    # undo last commit, keep changes unstaged
git reset --hard HEAD~1                     # undo last commit AND discard changes (destructive)
git reset --hard HEAD~3                     # undo last 3 commits and discard (very destructive)
```

`--soft` and `--mixed` are recoverable. `--hard` loses work.

---

## Branching

For 10 days solo, you can commit directly to `main`. Branches are optional.

If you want per-step isolation:

```bash
git checkout -b step-1.2-check-setup        # create + switch to branch
# ... work, commit ...
git checkout main                           # switch back to main
git merge step-1.2-check-setup              # merge branch into main
git branch -d step-1.2-check-setup          # delete merged branch
```

To list branches:

```bash
git branch                                  # local branches
git branch -a                               # all branches (including remote)
```

---

## Sync with remote

If you ever work on a different machine or someone else commits:

```bash
git fetch                                   # download remote state, don't merge
git pull                                    # fetch + merge into current branch
git pull --rebase                           # fetch + rebase your commits on top
                                            # (cleaner history than merge)
```

For solo work on one machine, you won't need these much.

---

## Tagging (releases)

At the end of D#10, mark v0 launch:

```bash
git tag -a v0.1.0 -m "MVP launch: first paying customer ready"
git push origin v0.1.0                      # push the tag to remote
```

To list tags:

```bash
git tag                                     # list all tags
git show v0.1.0                             # show what v0.1.0 points at
```

---

## Stashing (saving uncommitted work temporarily)

If you need to switch context but aren't ready to commit:

```bash
git stash                                   # save uncommitted changes
git stash list                              # see what's stashed
git stash pop                               # restore most recent stash
git stash drop                              # discard most recent stash
git stash clear                             # discard all stashes
```

Useful if Claude Code is mid-task and you need to pull urgently.

---

## What .gitignore handles

The `.gitignore` we set up ignores:

- `.venv/` (virtual environment)
- `keys/`, `*.pem`, `*.key` (auth keys)
- `uv.lock` (lockfile)
- `__pycache__/`, `*.py[cod]` (Python compiled)
- `.env`, `.env.local` (env files with secrets)
- `.vscode/`, `.idea/`, `*.swp` (editor files)
- `.pytest_cache/`, `.mypy_cache/` (tool caches)

If you accidentally committed something that should be ignored:

```bash
git rm --cached <file>                      # remove from git, keep on disk
git commit -m "Remove <file> from tracking"
```

---

## Common scenarios

### Scenario 1: Step completed, ready to commit

```bash
git status                                  # check what's there
git add -A                                  # stage everything
git commit -m "Step 1.5: smoke test passing with cross-tenant RLS"
git push                                    # push to remote
```

### Scenario 2: Wrong commit message, not yet pushed

```bash
git commit --amend -m "Step 1.5: smoke test with FORCE RLS handling"
```

### Scenario 3: Wrong commit message, already pushed

```bash
git commit --amend -m "Step 1.5: smoke test with FORCE RLS handling"
git push --force-with-lease
```

### Scenario 4: Committed too early, want to add more

```bash
# make additional changes
git add -A
git commit --amend --no-edit                # adds to previous commit
```

### Scenario 5: Want to undo last commit but keep changes

```bash
git reset --soft HEAD~1                     # commit gone, changes staged
# fix things
git commit -m "Step 1.5: fixed message"
```

### Scenario 6: Just totally broke everything, want to reset to last good state

```bash
git log --oneline                           # find the last good commit hash
git reset --hard <commit-hash>              # destructive: jumps back, loses work since
```

### Scenario 7: Want to see what changed in a specific file over time

```bash
git log -p docs/architecture.md
```

### Scenario 8: Found a bug introduced in past commits

```bash
git bisect start
git bisect bad HEAD                         # current is bad
git bisect good <known-good-commit>         # mark a commit you know was good
# git checks out a midpoint; you test, then run:
git bisect good                             # if midpoint is good
git bisect bad                              # if midpoint is bad
# repeat until git tells you which commit introduced the bug
git bisect reset                            # done
```

---

## Frequency reference

| Command | When | Frequency |
|---|---|---|
| `git status` | Anytime | Many times daily |
| `git add -A` | End of step | ~30 times across project |
| `git commit -m "..."` | End of step | ~30 times |
| `git push` | End of day or milestone | ~10 times |
| `git diff` | Reviewing changes | A few times daily |
| `git log --oneline` | Checking history | Few times daily |
| `git commit --amend` | Fixing message | Occasional |
| `git reset --soft HEAD~1` | Fixing bad commit | Rare |
| `git tag` | At v0 launch | 1 time |
| `git stash` | Context switch | Rare |
| `git revert` | Production rollback | Hopefully zero |

---

## Cheat sheet for the actual MVP build

If you only remember three patterns:

**Pattern 1 — Standard step end:**
```bash
git status
git add -A
git commit -m "Step <id>: <description>"
git push
```

**Pattern 2 — Fix bad commit message (not pushed):**
```bash
git commit --amend -m "Step <id>: <better description>"
```

**Pattern 3 — Bad commit, want to redo (not pushed):**
```bash
git reset --soft HEAD~1
# make corrections
git commit -m "Step <id>: <correct description>"
```

These three handle 95% of what you'll need.

---

## End of cheat sheet
