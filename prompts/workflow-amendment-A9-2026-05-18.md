# WORKFLOW.md amendment A9: Pre-flight Report-Pause-Authorise gate

This amendment is a follow-up to the A7+A8 retro commit. A9 adds the
Report-Pause-Authorise gate at pre-flight entry, symmetric to A7's
gate at commit. Together with A7 and A8, this completes the three-
sided commit discipline: A8 (drafting), A9 (pre-flight gate), A7
(commit gate).

This is also the first commit to be applied UNDER the A9 discipline
itself: CC must produce a pre-flight report before editing
WORKFLOW.md.

## Context

A7 codifies the back gate (CC reports, pauses, commits). A8 codifies
drafting discipline (cite or verify, never assert). Both leave a gap
at Phase 5 entry: CC runs pre-flight, treats clean output as license
to begin implementation, surfaces only on explicit surface-and-stop
triggers. If pre-flight reveals codebase state that differs from the
prompt's assumptions in a way that doesn't trip a hard
surface-and-stop, the operator never sees the divergence until
something fails downstream.

Concrete example from Step 6.17.3: pre-flight check #6 queried
`pg_constraint` for a UNIQUE constraint on `(tenant_id, store_code)`
and returned 0 rows. The prompt treated 0 rows as confirmation that
"no DDL uniqueness exists." Actual answer: a partial unique INDEX
exists (in `pg_indexes`, not `pg_constraint`); same factual ground,
different metadata table. CC eventually caught this via the
contradiction-surfacing license while implementing, but the operator
would have caught it instantly from a pre-flight report transcript
showing "0 rows from pg_constraint query" against a pre-flight
expectation that did not specify which metadata table to consult.

## Fix

CC runs pre-flight, produces a pre-flight report covering every
check's actual output, stops, awaits operator authorisation, then
begins implementation.

Symmetric to A7 item 6 (which lands at the back gate). A9 lands at
the front gate.

## Where

Phase 5 procedure in WORKFLOW.md, as a sub-section
"Pre-flight Report-Pause-Authorise gate". Inserted under Phase 5
alongside the existing A6 sub-section (commit scope) and the A7
sub-section (commit discipline). Order in the file: A6, A7, A9.

## Insert as new Phase 5 sub-section

```markdown
#### Pre-flight Report-Pause-Authorise gate (A9)

Phase 5 entry is two stages: pre-flight execution and report, then
operator authorisation, then implementation. CC does NOT begin
implementation on clean pre-flight output alone.

**Stage 1: Pre-flight execution and report.**

CC runs every pre-flight check enumerated in the impl prompt and
reports two parts of output for each:

- The command's actual output (transcripts, not summaries).
- A one-line interpretation: "matches expectation" / "deviates" /
  "ambiguous, surface for operator".

The pre-flight report shape:

```
Pre-flight for Step X.Y.Z

Check #1: <one-line description>
  Command: <exact command>
  Output: <transcript; if transcript exceeds 10 lines, full
           transcript saved to /tmp/pre-flight-N.log and the
           last 5 lines plus a summary appear inline>
  Status: matches expectation / deviates / ambiguous

Check #2: ...

...

Summary:
  - N/M checks match expectation
  - K checks deviate (listed below with details)
  - J checks ambiguous (listed below)

Deviations and ambiguities:
  [per-item: what was expected, what was found, proposed resolution]

Codebase observations beyond pre-flight scope:
  [anything CC noticed while running pre-flight that wasn't
   explicitly checked but might matter]

STOP. Awaiting operator authorisation before implementation begins.
```

**Stage 2: Operator authorisation.**

Operator reads the pre-flight report. Three response shapes:

1. **All clean, proceed.** "Approved, proceed." CC begins
   implementation.
2. **Minor deviation, adjust and proceed.** "Approved with X
   adjustment, e.g., 'use the actual DDL default ACTIVE not the
   OPENING in the prompt'." CC adjusts the relevant locked decision,
   notes the adjustment in the eventual report, proceeds.
3. **Substantive deviation, stop.** Operator escalates back to
   Chat. Prompt may need a Phase 4 revision before implementation
   continues.

**Stage 3: Implementation.**

Begins only after explicit operator authorisation. CC executes the
work bucket-by-bucket per the impl prompt's plan.

**Why item-by-item rather than just "report on findings":** the
explicit per-check report forces CC to produce evidence for the
"clean" verdict, not just claim it. Today's pattern lets CC say
"pre-flight passed" without surfacing intermediate output; the
operator has no visibility into what was actually verified. The
6.17.3 partial-unique-index defect would have been visible in a
pre-flight transcript ("output: 0 rows from pg_constraint query")
even though it didn't trip a surface-and-stop.

**Execution prompt update:**

The execution prompt's pre-flight instruction changes from:

> Pre-flight: ./scripts/check_setup.sh. If any check fails, stop
> and report. Then run the pre-flight section in the prompt.

to:

> Pre-flight: ./scripts/check_setup.sh, then run every pre-flight
> check in the impl prompt. Produce the full pre-flight report per
> the report shape in the prompt. STOP after the report. Wait for
> operator authorisation before implementation begins.

**Why A9 separately from A7:** A7 covers the back gate (commit).
A9 covers the front gate (implementation entry). Both are
Report-Pause-Authorise. Conflating them into a single amendment
risks applying one gate but not the other; separating them keeps
each gate's mechanics distinct and traceable.

**Resolution criterion for A9:** every step from 6.17.4 onward
produces a visible pre-flight report in chat before implementation
begins. If a step skips this and the operator only sees
implementation diffs, A9 was not honoured; re-anchor.
```

## Apply order

1. CC runs pre-flight per A9 itself (this commit dogfoods A9):
   - `git log -1 --oneline` shows the A7+A8 commit landed.
   - `git status` clean.
   - WORKFLOW.md has the expected sub-sections under Phase 5
     (A6 and A7 sub-sections present; A9 will be inserted after
     A7).
   - Operator reviews pre-flight report.

2. Apply A9 as a new Phase 5 sub-section, after the A7 sub-section.

3. Verification harness:
   - `git diff --stat -- '*.py'` returns empty.
   - `grep -c '^#### ' WORKFLOW.md` increments by exactly 1 (A9
     heading).
   - Inserted A9 reads cleanly with no orphan fences.

4. Report (before commit):
   - Diff stat.
   - Verification output.
   - Confirm files staged would be: `WORKFLOW.md` +
     `prompts/workflow-amendment-A9-2026-05-18.md`.
   - STOP. Wait for operator authorisation.

5. After authorisation, stage and commit:
   ```
   Retro: WORKFLOW.md A9 (Pre-flight Report-Pause-Authorise gate)
   ```

6. Push together with the A7+A8 commit and 7ca406c (the 6.17.3
   commit) when operator decides to push.

## Coordination

A9 completes the three-sided discipline:

- A7 = back gate (report-pause before commit), fixes today's
  incident.
- A9 = front gate (report-pause after pre-flight, before
  implementation), prevents tomorrow's silent baseline-drift.
- A8 = drafting discipline that prevents asserted-without-backing
  claims that A9's pre-flight gate would otherwise have to catch.

Without A9, A7 and A8 leave the front gate unguarded: CC can race
past pre-flight findings without operator visibility. Without A8,
A9 still works but has more work to do (catching defects the
drafter could have prevented). The three reinforce each other.

Step 6.17.4's impl prompt (when drafted) will be the first prompt
authored under A7+A8+A9. The retro for that step will record
whether the three amendments closed the defect classes or need
strengthening.

## Files touched by this amendment

- `WORKFLOW.md` (one new A9 sub-section under Phase 5; no other
  edits). The adversarial readback in Phase 4 close was already
  extended with check #7 by A8; A9 does not touch it.
- `prompts/workflow-amendment-A9-2026-05-18.md` (source of
  amendment; this file).

No code, no tests, no schema.
