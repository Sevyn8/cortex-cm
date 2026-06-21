# prompts/ — Per-step prompts for Claude Code

> One file per build-plan step. Each file is a self-contained prompt to paste into a fresh Claude Code session when starting that step.

---

## How to use

When starting a new Claude Code session:

1. **First message:** paste `00-bootstrap.md`. Claude Code orients itself: pre-flight, reads standing-context docs, inventories the repo, reports back. **Do this every new session.** It is not optional.
2. **After bootstrap:** open the step-specific prompt for the work you're starting. Paste as the next message.
3. Claude Code reads pre-flight requirements, asks clarifying questions if needed, then executes.
4. Review output before confirming. Confirm git commit per CLAUDE.md Pattern A.

**Skip bootstrap only if** you are mid-session in the same Claude Code instance with full context already loaded. In a fresh session, always bootstrap first.

---

## Available prompts

| Step | File | Pattern | Status |
|---|---|---|---|
| **00** | **00-bootstrap.md** | **Session start orientation. Run FIRST in any new Claude Code session.** | **Ready** |
| 1.3 | step-1.3-ddl-stress-test.md | Read-only DDL review with severity-tagged issue list | Ready |
| 1.5 | step-1.5-smoke-test.md | Self-contained script with FORCE RLS handling | Ready |
| 2.1 | step-2.1-stub-auth.md | Foundational infra: keys, AuthContext, JWT verification | Ready |
| 2.3 | step-2.3-middleware.md | Cross-cutting middleware (auth + audit context) | Ready |
| 3.1 | step-3.1-tenant-model-schema.md | Schema layer: ORM model + Pydantic schema | Ready |
| 3.3 | step-3.3-tenants-router.md | Full vertical: router + endpoints + tests + endpoint doc | Ready |
| 4.4 | step-4.4-k8s-deploy.md | Hybrid: Claude writes YAML + human runs kubectl | Ready |
| 6.3 | step-6.3-seeds.md | Multi-file SQL + idempotent runner | Ready |

---

## Patterns covered

These seven prompts cover the major patterns in the build plan:

1. **Self-contained script** (no app integration) — Step 1.5
2. **Foundational infra** with cryptography and Pydantic types — Step 2.1
3. **Cross-cutting middleware** integrated into main.py — Step 2.3
4. **Schema layer** that locks the pattern for all resources — Step 3.1
5. **Full endpoint vertical** with tests and per-endpoint doc — Step 3.3
6. **Hybrid step** with manual deploy commands — Step 4.4
7. **Multi-file SQL** with idempotent runner — Step 6.3

Steps not covered here follow one of the patterns above. Generate prompts for those steps just-in-time, copying from the closest match:

- Steps 1.2, 1.3, 1.4, 1.6 → similar to 1.5 (scripts/SQL).
- Steps 2.2, 2.4 → similar to 2.1/2.3.
- Step 3.2 → similar to 3.1 but for repository class.
- Steps 4.5, 5.x, 6.1, 6.2 → clones of 3.3 with different table.
- Steps 4.2, 4.3 → simpler than 4.4.
- Step 7.x → mostly similar to 1.5 (test scripts) or 2.x (instrumentation wiring).
- Steps 8.x → similar to 4.4 but for production.
- Steps 9.x → mostly HUMAN coordination + CLAUDE_AI docs; Step 9.3 follows the doc-only pattern.

---

## Prompt structure

Every prompt has roughly the same shape:

1. **Pre-flight** — what to read first (CLAUDE.md, architecture.md, api-contract.md, BUILD_PLAN.md, the relevant DDLs).
2. **Step ID and intent** — what this step is and why it matters.
3. **Scope in** — files to create/modify, with code shapes where useful.
4. **Scope out** — explicit list of what NOT to do.
5. **Implementation hints** — gotchas, library notes, design pointers.
6. **Acceptance criteria** — bullet list, must all pass.
7. **Stop and ask if** — explicit triggers to pause and surface to user.
8. **What to report at end** — summary expected from Claude Code on completion.
9. **After completing** — git commit proposal in Pattern A format.

---

## Adding a new prompt

When you reach a step not yet in this directory:

1. Identify the closest existing prompt (see "Patterns covered" above).
2. Copy that file, rename to `step-<id>-<short-description>.md`.
3. Edit:
   - Pre-flight section (file references).
   - Step ID and intent.
   - Scope in (the actual files for the new resource).
   - Scope out.
   - Acceptance criteria.
   - Git commit message.
4. Add the new entry to the table above.
5. Commit `prompts/step-<id>-*.md` to git.

Time to write a prompt by template: ~10 min.

---

## End of README
