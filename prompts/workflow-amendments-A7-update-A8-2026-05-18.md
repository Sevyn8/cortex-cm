# WORKFLOW.md amendments : A7 update + A8 new

Two amendments. A7 update fixes a gap that 6.17.3 surfaced (CC went
straight to staging because A7 didn't specify the report-pause gate).
A8 codifies the drafting discipline that would have prevented the
seven Class-A prompt defects across Steps 6.17.2 and 6.17.3.

## A7 update : Report → Pause → Commit gate in Phase 5

**Context.** A7 (landed at commit 5cfa547) codified that the impl
prompt MUST spec the Phase 5 commit step. It did not codify the
pause-and-authorise gate between report and commit. Step 6.17.3's
CC produced a clean report and immediately asked for `git add`
permission, requiring manual operator intervention to slow it down
to report-first.

**Fix.** Add an explicit two-stage requirement to A7's Phase 5
discipline section in WORKFLOW.md.

**Where:** Under the existing A7 section in WORKFLOW.md (Phase 4
sub-section "Phase 5 commit discipline in the impl prompt"). The
existing 5-discipline enumeration stays; add a 6th item that names
the gate.

**Insert after the existing item 5 (CLAUDE.md "Current state" entry
format):**

```markdown
**6. Two-stage Phase 5 commit (Report-then-Authorise).** CC executes
the implementation work, produces the full report per the
report-shape template, and STOPS. The report appears in chat for
operator review BEFORE any `git add` or `git commit` runs. After
operator review, operator authorises the staging + commit (typically
with a one-line "approved; commit" reply). Only then does CC stage
and commit.

The impl prompt's "Commit" work bucket reads:

```
N. Report and pause (before commit)
   - Run full verification harness; capture outputs.
   - Produce the full report per the report-shape template.
   - STOP. Do not stage. Do not commit.
   - Surface every Surface-and-stop finding, every
     Adjusted-trivial LD, every Stopped-for-confirmation LD,
     every codebase observation in the report.
   - Wait for operator authorisation.

N+1. Stage and commit (after operator authorisation)
   - Stage the file list exactly as enumerated in the report.
   - Verify staging via `git status` and `git diff --cached --stat`.
   - Commit with the A6 message template.
   - Do NOT push.
   - Report commit hash via `git log -1 --stat`.
```

The execution prompt's default form updates accordingly:

> Read `/prompts/step-X_Y-impl-YYYY-MM-DD.md` end-to-end. Execute
> the work bucket-by-bucket. Stop after Bucket N (Report) and wait
> for operator authorisation before staging or committing. Report
> commit hash on completion. Do NOT push.

**Why item 6 separately:** items 1-5 cover what the impl prompt
contains. Item 6 covers the execution flow that uses it. Both are
needed; conflating them caused the 6.17.3 incident.
```

## A8 new : Drafting discipline: cite or verify, never assert

**Context.** Across Steps 6.17.2 and 6.17.3, the impl prompt
drafter (Chat) introduced seven asserted-fact defects that CC
caught and resolved during execution:

| Defect | Asserted | Actual |
|---|---|---|
| LD5 store_code uniqueness | "no DDL UNIQUE constraint" | partial unique INDEX exists |
| LD8 store status default | "DDL default OPENING" | DDL default ACTIVE |
| main.py routing order | "alphabetical convention" | grouped by domain |
| Repo audit-actor signature | `actor: AuthContext` | `actor_user_id + actor_user_type` |
| POST status code | magic number `201` | `status.HTTP_201_CREATED` |
| Sort key naming | `tenant_grouping_*` | `<field>_<asc\|desc>` |
| Country regex | `^[A-Z]{2,3}$` | freeform 2-100 chars per DDL |

Five of seven were caught at adversarial readback or by CC's
pre-flight grep. Two (LD5, LD8) shipped to CC and were caught only
because the prompt carries an explicit contradiction-surfacing
license (per Step 6.17.3). Without the license, both would have
silently produced wrong test fixtures and a follow-on debugging
cycle.

The root cause is shared: the drafter asserted codebase facts from
memory or project-knowledge-search summaries, neither of which is
ground truth. CC's actual code grep is ground truth.

**Fix.** Codify drafting discipline as a Phase 4 sub-section in
WORKFLOW.md.

**Where:** Phase 4 sub-section, new heading after the existing
"Phase 5 commit discipline in the impl prompt" (A7).

**Insert as new sub-section:**

```markdown
#### Drafting discipline: cite or verify, never assert (A8)

The impl prompt is a spec. Every claim about codebase or DDL state
in a locked decision, pre-flight check, or surface-and-stop scenario
MUST be one of:

1. **Cited.** A specific reference to existing code or DDL, in the
   form `file:line` or `db_object.column = value` with the source
   path. The drafter has either viewed the file or run the query at
   draft time. Example: "Per `models/tenant.py:34-39`, audit-actor
   columns use Pattern (b)."

2. **Verify-at-pre-flight.** An explicit pre-flight check that CC
   runs before the impl work begins, with the command and expected
   result stated inline. Example: "Pre-flight check #7: `grep -A 1
   'class TenantsRepo' src/admin_backend/repositories/tenants.py |
   head` should show the `create(...)` method's first kwarg as
   `*` (kwargs-only). If positional, stop and report."

3. **Marked as inference.** Phrased as such with the drafter's
   uncertainty surfaced. Example: "Inference (not codebase-verified
   at draft): tenants and tenant-users likely share the actor_type
   helper. CC should verify at impl time; if separate, follow
   tenants pattern."

**Disallowed:** Asserted facts without citation, pre-flight check,
or inference marker. Phrases like "The DDL default is X" or "The
codebase convention is Y" or "TenantsRepo uses Z" without backing
are drafting defects. CC may catch them via the
contradiction-surfacing license, but the defect rate is the
drafting problem to solve.

**Drafting workflow:** Before writing any locked decision or
pre-flight check:

1. Identify the codebase or DDL claim the LD/check depends on.
2. Decide: cite (open the file or run the query now), pre-flight
   verify (write the check inline), or mark as inference.
3. If the claim is load-bearing for test coverage or schema shape,
   prefer cite > pre-flight > inference. Inference for load-bearing
   claims is a smell.
4. Class C threshold checks (e.g., `grep -c 'stores' >= 20`)
   require empirical basis: drafter should run the grep at draft
   time and use the actual count, OR specify the check as
   "grep returns non-empty" rather than a fragile threshold.

**Adversarial readback (existing in WORKFLOW.md Phase 4 close)
gains a new numbered check:**

The current adversarial readback has 6 numbered checks (function/class
names, new contracts vs locks, test count drift, worked example
fidelity, order-of-checks bugs, cleanup fixture names). Add a 7th:

> **7. Asserted-without-backing codebase claims.** For every claim
> the prompt makes about codebase or DDL state (locked decision,
> pre-flight check, surface-and-stop scenario, design statement),
> confirm it is one of: cited (file:line), pre-flight verified
> (command + expected), or marked as inference. Any
> asserted-without-backing claim is a finding; fix or annotate.
> Specifically check:
>
> - Locked decisions that begin "DDL X is..." or "The codebase
>   convention is..." : these need citations.
> - Pre-flight grep patterns that assume specific PG metadata
>   table contents (pg_constraint, pg_indexes, etc.) : verify the
>   pattern actually returns the expected shape via dry-run.
> - Function/method signatures asserted by analogy ("mirrors X")
>   : verify X's actual signature, not the drafter's recollection
>   of it.
> - HTTP status codes, named constants, enum values, default
>   values : cite the exact source.

**Resolution criterion for A8:** the seven-defect class above
should drop to zero over the next 3 steps. If similar defects
recur, A8 needs strengthening (e.g., mandatory grep transcripts
in the impl prompt's pre-flight section).
```

## Apply order

1. Apply A7 item 6 addition to WORKFLOW.md (under the existing
   "Phase 5 commit discipline in the impl prompt" sub-section).

2. Apply A8 as a new Phase 4 sub-section (after A7's sub-section).

3. Single retro commit:
   ```
   Retro: WORKFLOW.md A7 update (Report-Pause-Commit gate) + A8 new (drafting discipline)
   ```

   Files staged: `WORKFLOW.md` + `prompts/workflow-amendments-A7-update-A8-2026-05-18.md`.

   Optional: append a 2-bullet note to Step 6.17.3's step doc Retro
   section pointing at the source-of-amendment (this prompt file).
   Skip if step doc is already committed under 7ca406c.

4. Push together with 7ca406c (the 6.17.3 commit).

## Coordination

A7 item 6 lands first because it fixes today's incident. A8 lands
in the same commit because the drafting-discipline gap is the
upstream cause; if A8 lands later, the next step's prompt may
introduce more Class-A defects before A8 codifies the prevention.

Step 6.17.4's impl prompt (when drafted) will be the first prompt
authored under A7+A8. The retro for that step will record whether
A8 closed the defect class or needs strengthening.

## Files touched by this amendment

- `WORKFLOW.md` (A7 sub-section gets a new item 6; new A8
  sub-section).
- `prompts/workflow-amendments-A7-update-A8-2026-05-18.md` (source
  of amendment; this file).

No code, no tests, no schema.
