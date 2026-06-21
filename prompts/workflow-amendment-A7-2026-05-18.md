# WORKFLOW.md amendments — Step 6.17.2 retro

Single amendment letter: **A7 — Phase 5 commit discipline in the impl prompt**.

## Context

At Step 6.17.2 the impl prompt enumerated source / test / docs / scripts work
but did NOT spec the commit step itself. CC executed all the implementation
work and stopped at "tests green, working tree dirty," reasonably reading the
prompt as complete at that point. The operator then had to write a separate
"stage and commit" prompt to close Phase 5. Net effect: one extra
operator-CC round trip per step.

WORKFLOW.md Phase 5 is explicit that the commit is part of Phase 5
("CC commits with the agreed message"; deliverable "code commits on the
working branch"). The gap is between WORKFLOW.md's stated discipline and
the impl prompt template that implements it. The impl prompt template
does not currently force the commit step onto CC's plate.

A6 codified that retro-derived doc edits land at Phase 5 commit time. A7
codifies that the Phase 5 commit itself is a required and explicitly-specced
part of the impl prompt.

## A7 — Phase 5 commit discipline in the impl prompt

**Where**: Phase 4 procedure, in the "Implementation prompt" sub-section
that enumerates what the impl prompt MUST include.

**Currently** the Phase 4 work section reads:

```
1. **Implementation prompt** - full, self-contained, following established
   prompt patterns for this project. Includes:
   - context block (what step, what was decided, why)
   - locked decisions
   - DDL facts (carried forward from Phase 1)
   - file-by-file change list
   - sub-step / commit boundaries
   - test catalogue (test names + behavioral coverage)
   - verification harness
   - explicit conventions to follow
   - **explicit non-goals** - what NOT to change
   - **Appendix** for any architecture-doc content the step ships
     (carried forward from Phase 2 approved draft, verbatim)
```

**Replace with**:

```
1. **Implementation prompt** - full, self-contained, following established
   prompt patterns for this project. Includes:
   - context block (what step, what was decided, why)
   - locked decisions
   - DDL facts (carried forward from Phase 1)
   - file-by-file change list
   - sub-step / commit boundaries
   - test catalogue (test names + behavioral coverage)
   - verification harness
   - explicit conventions to follow
   - **explicit non-goals** - what NOT to change
   - **commit instructions** (per "Phase 5 commit discipline" below)
   - **Appendix** for any architecture-doc content the step ships
     (carried forward from Phase 2 approved draft, verbatim)
```

**Then add a new sub-section under Phase 4 (after "Quantified gate — code
volume in impl prompt" and before the structure-conformance checklist)**:

```markdown
#### Phase 5 commit discipline in the impl prompt

Every impl prompt MUST spec the Phase 5 commit explicitly. CC does not
infer the commit step from "tests green"; the prompt must enumerate it
in the change list and in the report shape.

Five disciplines carry the Phase 5 commit through the impl prompt:

**1. A "Commit" section in the change list.** After all source / test /
docs / scripts buckets, the impl prompt enumerates the commit as its own
work bucket:

```
N. Commit
   - Stage all files enumerated above (no `git add -A`; explicit paths)
   - Verify staging via `git status` and `git diff --cached --stat`
   - Commit with the A6 message template (see below)
   - Do NOT push; stop after commit and report the commit hash
```

**2. The A6 commit message template, inline in the prompt.** The
template is not left for CC to invent. The impl prompt provides the
template with placeholders filled for this step:

```
git commit \
  -m "Step X.Y.Z: <feature> + tests + smoke + docs + retro" \
  -m "<code/feature description — 2-3 sentences>" \
  -m "<doc updates summary — architecture, CLAUDE.md, BUILD_PLAN.md, WORKFLOW.md if amended>" \
  -m "<retro highlights — what worked, lessons captured, any deferred items>"
```

The first `-m` is the headline. The remaining three are the
code / doc / retro paragraphs A6 specifies. If a paragraph would be
empty (e.g., no architecture changes), the impl prompt collapses the
template to the relevant paragraphs only and notes the omission.

**3. Report shape MUST include a "Commit" line.** The impl prompt's
report-shape template enumerates:

```
Commit: <hash> (working tree clean post-commit)
   OR
Commit: pending operator approval — staging complete, working tree
        clean except for the 24 files staged; see `git diff --cached --stat` output below
```

The first form is the default. The second is acceptable only when the
impl prompt explicitly tells CC to stop before commit and wait for
operator confirmation (e.g., for high-risk steps where the operator
wants to inspect the staged diff before the commit lands).

**4. Execution prompt default form includes commit-boundary pause.**
The default execution prompt (Phase 4 deliverable #2) is updated from:

> Read `/prompts/step-X_Y-impl-YYYY-MM-DD.md` and execute the WBS,
> pausing for confirmation at each commit boundary.

to:

> Read `/prompts/step-X_Y-impl-YYYY-MM-DD.md` end-to-end. Execute the
> work bucket-by-bucket including the commit step at the end. Report
> commit hash on completion. Do NOT push.

The "Do NOT push" is load-bearing: pushing is the operator's call after
inspecting the commit shape.

**5. CLAUDE.md "Current state — Completed" entry format MUST be quoted
inline in the impl prompt.** The format is:

```
- **Step X.Y[.Z]: <feature>.** <Status>: DONE-LOCAL at `<this-commit>`
  (or DONE at `vX.Y.Z` post-Phase-6). <one-sentence scope summary>.
  Detail: `docs/implementation-steps/step-X_Y-<name>-YYYY-MM-DD.md`.
```

The literal text `<this-commit>` is canonical: the CLAUDE.md entry's
commit IS the commit it references, so the placeholder is the entry's
own commit by construction. Operator (or a future reader) resolves the
hash via `git log` against the file's history if needed. This avoids
the `git commit --amend` dance (which conflicts with the single-commit-
per-step discipline once the commit is pushed) and produces a clean
audit trail where the entry is born complete.

The impl prompt instructs CC to use `<this-commit>` verbatim. CC does
not attempt to fill the hash; the placeholder is the canonical form.

**Why A7**: Step 6.17.2 surfaced the gap. The impl prompt had every
other Phase 5 deliverable specced (code, tests, docs, smoke, retro
file) but stopped at "report back per the report-shape template" with
the report shape not requiring a commit hash. CC closed at tests-green
because that was the explicit endpoint. The fix is to make the commit
endpoint explicit in every impl prompt going forward.

**Failure mode this prevents**: every step from 6.17.2 onward without
this discipline would either (a) require a follow-up operator prompt
to commit, costing a round-trip, or (b) silently drift to "CC commits
with a generic message" which loses the A6 template structure. Neither
is acceptable as a default.
```

## Apply order

1. Apply A7 to WORKFLOW.md at the locations specified above.
2. The 6.17.2 retro commit (which has not yet been written) folds A7 in
   alongside the 6.17.2 step doc retro section. Per A6, this is a single
   commit at Phase 5; for 6.17.2 the retro commit is technically a
   post-hoc cleanup because the main commit (46659b7) already landed.
3. Decision point: either
   (a) **`git commit --amend`** on 46659b7 to fold WORKFLOW.md A7 into
       the original Step 6.17.2 commit (clean history, but rewrites a
       commit that may already be pushed), or
   (b) **separate retro commit** `Retro: Step 6.17.2 — WORKFLOW.md A7`
       that lands after the push (preserves history; one extra commit).

Recommend (b) since 46659b7 is being pushed before this amendment lands.
A7 is the lesson from 6.17.2, not part of 6.17.2's implementation;
shipping it as a follow-on retro commit is honest history.

## Files touched by this amendment

- `WORKFLOW.md` (one new sub-section under Phase 4, plus the one-line
  addition to the existing Phase 4 work-section bullet list).

No other files. A7 is process discipline; it does not introduce code,
schema, or seed deltas.
