# Prompt — Section 6.9 housekeeping commit

> Generated 2026-05-13. Calibrated against codebase HEAD at commit
> `6c92661` ("Step 6.9.3.2 cleanup: Phase 3 seed update applied").
> Pytest baseline: 319 passed.
>
> Paste this entire block into a fresh Claude Code session.

---

## Standing discipline (read first)

### On the code sketches in this prompt

Code blocks are STARTING POINTS, not the answer. You have live access
to the codebase. Where a better implementation exists, use it; surface
deviations in the report.

For documentation edits specifically, the "code" being written is 
CLAUDE.md prose. Same discipline applies: read existing CLAUDE.md 
sections to mirror voice/structure before adding new content.

Locked decisions remain locked. Everything else is calibrated guidance.

### Documentation writing

Updates to CLAUDE.md must be technical, sharp, concise. State facts, 
active voice present tense, one sentence per fact. No meta-commentary, 
no adjectives that don't add information.

For this commit specifically (which is ENTIRELY CLAUDE.md edits), 
the documentation-writing rules are the load-bearing constraint. 
Sloppy prose here propagates; the file is consulted across every 
future step.

### Definition of done

Before reporting complete:
1. All tests pass (319, unchanged — this is a docs-only commit).
2. CLAUDE.md updates sharp.
3. No code edits (verify via `git diff --stat` showing only 
   CLAUDE.md modified).

---

## Context — why this step exists

Section 6.9 fully shipped across 5 commits ending at `6c92661`. 
During the run, ~6 housekeeping items accumulated as forward-notes 
or operator observations. None blocks future work, but landing 
them together as a single cleanup commit:

- Closes Section 6.9's documentation surface
- Keeps CLAUDE.md authoritative for tomorrow's Stage 2 write-endpoint 
  work
- Prevents drift if items linger as un-actioned forward-notes

### Out of scope

- Architecture.md RBAC section (separate task; deferred for fresh 
  session per operator decision)
- Any code edits
- Test changes (319 baseline must hold unchanged)
- BUILD_PLAN.md edits (Section 6.9 already marked COMPLETE in 
  6c92661)
- New FN-AB entries beyond what's listed below

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -3`. Confirm HEAD is `6c92661` (Step 6.9.3.2 
   cleanup).
3. `git status`. Expect clean.
4. `uv run alembic heads`. Expect `3e05299cb533`.
5. `uv run pytest --tb=no -q | tail -3`. Expect 319 passed, 0 
   failed, 0 xfailed.
6. Read `CLAUDE.md` fully. Specifically locate:
   - "Code conventions and structure" section (or equivalent — 
     where existing "Note on X" maintenance conventions live)
   - "Current state — Completed" section
   - Forward-notes section (FN-AB entries; latest assigned number)
   - Any existing "Investigation-vs-implementation" discipline 
     mention (search for it; may already exist as a brief mention 
     that this commit expands)

---

## Step ID and intent

**Section 6.9 housekeeping** — single commit of 6 CLAUDE.md edits 
that landed as forward-notes during Section 6.9 execution. No code 
edits; no test edits.

### Scope in

Six CLAUDE.md changes:

1. **module_access_status_enum live values** — Correct any stale 
   reference to the live enum values. Live: ENABLED, DISABLED only. 
   Older sketches may have said SUSPENDED or NOT_PROVISIONED (caught 
   during Section 6.9.1 investigation as stale).

2. **Tighten seed count hedges** — Replace any "approximately N" 
   or "~N" hedges with concrete numbers reflecting current seed state:
     - tenant_users: 17
     - platform_users: 3
     - tenant_user_role_assignments: 19
     - platform_user_role_assignments: 3
     - tenant_module_access (enabled): 23
     - permissions: 31 (post-Phase-3)
     - role_permissions: 122 (post-Phase-3)
     - roles: 15

3. **Investigation-vs-implementation discipline note** — Codify the 
   6 cases caught across Section 6.9 where investigation reports 
   surfaced stale or incorrect claims that implementation verified 
   and corrected. Lists the 6 cases concretely.

4. **Seed loader column semantics audit** — New FN-AB entry (assign 
   next available number). The `id` column in Excel is regenerated 
   server-side via DEFAULT uuidv7(); the `code` column is honored 
   verbatim by the loader and can drift from M.R.A.S. Audit and 
   document; consider load-time validation.

5. **Smoke scripts update convention on new endpoints** — When new 
   endpoints land, three scripts in `scripts/` must update:
     - `smoke_curl.sh`
     - `test_endpoints.sh`
     - `test_endpoints_cloud.sh`

6. **Documentation-vs-locked-decisions sanity check** — Add to 
   standing-discipline-style guidance: implementation reports must 
   verify documented decisions (FN-AB entries, design lock 
   summaries) against the actual shipped code, not just the prompt's 
   claims. The FN-AB-31 typo on /role-assignments (caught during 
   6.9.3.2 commit review) is the concrete example.

Also (one decision recorded, not a new FN-AB):

7. **Frontend doc reference convention** — Doc stays in frontend 
   repo. Backend references it cross-repo as 
   `"frontend repo, Ithina_Admin_Frontend.md, section X.Y"` when 
   needed. Locked as housekeeping decision, not a forward note.

### Scope out

- Architecture.md
- Code changes
- Test changes  
- BUILD_PLAN.md changes (Section 6.9 already closed)
- FN-AB renumbering or reorganization

### Acceptance criteria

- All 6 CLAUDE.md changes land in a single commit.
- pytest stays at 319 passed, 0 failed.
- mypy strict clean (no code touched; should pass trivially).
- check_setup 35/35.
- `git diff --stat` shows ONLY CLAUDE.md modified (plus the bundled 
  prompt file).
- The new FN-AB entry (item 4) has the next sequentially-available 
  number.
- Existing CLAUDE.md voice/structure preserved — new content matches 
  existing entries' style.

### Locked decisions

1. CLAUDE.md is the only file edited (plus bundled prompt).
2. The frontend-doc-location decision is Option A (stays in frontend 
   repo); recorded as a housekeeping note, NOT a new FN-AB entry 
   (it's a decision, not deferred work).
3. Each item below has a defined target section in CLAUDE.md — do 
   not relocate; match the existing structural pattern.

---

## Implementation outline per item

### Item 1: module_access_status_enum live values correction

**Where to look in CLAUDE.md:** any reference to 
`module_access_status_enum` values. Likely in Step 6.8.x or Step 6.9.x 
current-state entries, or in a conventions section.

**The correction:** ensure no mention of SUSPENDED or NOT_PROVISIONED 
as valid enum values. The live enum at HEAD has 2 values only:

```
module_access_status_enum:
  ENABLED
  DISABLED
```

If a stale reference exists, replace it. If no reference is found, 
no edit needed — surface in the report.

### Item 2: Seed count hedges

**Where to look:** any place in CLAUDE.md saying "~17 tenant_users" 
or "approximately 30 permissions" or similar. Replace hedges with 
concrete current values.

**Current values (post-Phase-3, verified at HEAD 6c92661):**

```
tenant_users:                       17
platform_users:                      3
tenant_user_role_assignments:       19
platform_user_role_assignments:      3
tenant_module_access (ENABLED):     23
permissions:                        31
role_permissions:                  122
roles:                              15
```

Survey CLAUDE.md for hedges. Replace each with the exact number.
If a hedge predates Phase 3 (e.g., uses 30 for permissions), update 
to 31 — the post-Phase-3 truth.

### Item 3: Investigation-vs-implementation discipline note

**Location in CLAUDE.md:** alongside existing "Note on X" maintenance 
conventions (e.g., "Note on org-hierarchy coupling" from Step 6.9.3.1, 
"Note on gate allowlist coupling" from Step 6.9.3.2). Match that 
bold-inline-paragraph format.

**Content (paraphrase to match house style; do not copy verbatim):**

```
### Note on investigation-vs-implementation discipline

Investigation reports describe codebase state at a point in time;
implementation steps verify each claim against actual code before
acting. Stale findings surface via Surface-and-stop triggers, not 
silent workarounds. Section 6.9 caught 6 such cases:

  1. 6.9.1 F-REPO-4: investigation cited type-drift; annotation 
     correct since Step 6.8.2
  2. 6.9.1 Caution #6: stale enum values 
     (SUSPENDED/NOT_PROVISIONED never existed in module_access_status_enum)
  3. 6.9.2 F-GATE-2: incorrect file location for 
     _audience_filter_for
  4. 6.9.3.1 Caution #2: stale claim about psycopg/CAST behavior 
     with enum arrays (empirically wrong; Postgres rejects 
     out-of-enum strings at CAST time)
  5. 6.9.3.1 frontend doc reference: cited file not in this repo 
     at HEAD
  6. 6.9.3.2 commit review: FN-AB-31 documentation contained typo
     (.GLOBAL vs .TENANT) that propagated from the final report
     into CLAUDE.md before catch

Convention: implementation prompts include a "Surface-and-stop" 
section listing triggers where Claude Code pauses before silent 
workarounds. Operator reviews surfaced findings; either confirms 
the prompt's claim or accepts the empirical correction.
```

Phrasing should match other "Note on X" entries — terse, technical, 
no marketing voice.

### Item 4: Seed loader column semantics audit (NEW FN-AB)

**Location:** Forward-notes section. Assign next available FN-AB 
number (likely FN-AB-34 if not already assigned, or next after 
that — verify by reading the latest FN-AB-N).

**Entry shape:**

```
### FN-AB-NN — Seed loader column semantics audit

The seed loader treats Excel columns inconsistently:
  - `id` columns (permissions, roles, tenant_users, etc.): IGNORED. 
    Server-side DEFAULT uuidv7() regenerates UUIDs at INSERT time. 
    Excel UUID values are not used.
  - `code` column on permissions: HONORED verbatim. Excel typos 
    propagate to the database. Caught during Phase 3: Excel had 
    `ADMIN.TENANTS.VIEW.TENANTS` (plural, typo); local DB inherited 
    the typo; Cloud SQL was correct only because operator hand-wrote 
    the INSERT.

Action items (deferred):
  - Document seed loader column semantics: which are authoritative 
    at INSERT time vs advisory/derived
  - Add seed-loader validation step:
    For each permissions row, assert 
      excel.code == f"{module}.{resource}.{action}.{scope}"
    Fail the seed load on mismatch
  - Decide whether to keep `code` column in Excel (with strict 
    validation) or derive server-side and drop from Excel

This would have caught the TENANTS-vs-TENANT typo at load time 
rather than in DB query review.
```

### Item 5: Smoke scripts convention on new endpoints

**Location:** "Code conventions and structure" section or equivalent. 
Match existing convention-note format.

**Entry shape:**

```
### Note on smoke and endpoint test scripts

When new endpoints land, three scripts in `scripts/` update in 
lockstep:
  - `smoke_curl.sh` — quick smoke against local backend
  - `test_endpoints.sh` — full per-endpoint integration check 
    (local)
  - `test_endpoints_cloud.sh` — same against Cloud Run deploy

Adding a new endpoint without updating these scripts means smoke 
PASS counts no longer reflect actual surface coverage. Mandatory-
gate-discipline test (tests/integration/test_gate_discipline.py) 
catches missing gates on new endpoints, but the smoke scripts 
catch missing per-endpoint behavioral coverage in CI/cloud.

Convention: a Stage 2+ commit adding a new endpoint must include 
matching smoke_curl.sh + test_endpoints.sh updates. Cloud-only 
scripts (test_endpoints_cloud.sh) update via inspection, 
verified post-deploy.
```

### Item 6: Documentation-vs-locked-decisions sanity check

**Location:** Implementation prompt template guidance OR a new 
"Note on documentation discipline" entry. Pick the cleanest 
placement — surface choice if ambiguous.

**Entry shape:**

```
### Note on documentation-vs-locked-decisions

Implementation reports (the final report from Claude Code per 
step) must verify documented decisions against the actual shipped 
code. Specifically:
  - FN-AB entries: cite real file paths, real tuple values, real 
    code references — not paraphrases that drift from the lock
  - Final report summary tables: cross-check each row against the 
    actual handler/file before claiming "matches Phase X lock"
  - CLAUDE.md current-state entries: copy the locked tuple/value 
    verbatim from the design conversation; do not retype from 
    memory

Caught example: Step 6.9.3.2 final report and FN-AB-31 both 
contained `.GLOBAL` for /role-assignments when the locked design 
decision and shipped code both used `.TENANT`. The discrepancy 
propagated through report + CLAUDE.md before operator review 
caught it.

Convention: prompts include a "Verify against locked decisions" 
item in the report template. Operator review treats locked-decision 
deviations as Surface-and-stop, not silent corrections.
```

### Item 7: Frontend doc reference convention (housekeeping decision, not FN-AB)

**Location:** Within an existing maintenance-conventions section. 
Brief — one short paragraph.

**Entry shape:**

```
### Note on cross-repo references

The frontend product spec (Ithina_Admin_Frontend.md) lives in the 
frontend repo, not the admin-backend repo. When backend prompts 
or design conversations need to cite the frontend spec, the 
convention is:
  
  "frontend repo, Ithina_Admin_Frontend.md, section X.Y"
  
Sections frequently cited: 5.5 (cascade rules), 7.2.11 (tenant 
administration), 7.3 (organization tree). The frontend doc 
remains authoritative for product intent; backend prompts cite 
by section reference rather than copying content. Revisit if 
cross-repo references become friction during Stage 2 / Stage 3 
write-endpoint design.
```

---

## Caution-first risks

1. **Voice consistency.** CLAUDE.md has a specific terse 
   technical voice. Read 3-5 existing entries before drafting any 
   new content. Match sentence cadence, terminology, conciseness. 
   If a new entry reads materially differently from existing 
   ones, revise.

2. **FN-AB numbering.** The latest FN-AB number in CLAUDE.md is 
   the next-available baseline. Verify by reading FN-AB-NN entries 
   in order; the new item 4 entry takes max(N) + 1. If the next 
   number is unclear, surface and pause.

3. **Stale references search.** Item 1 (module_access_status_enum) 
   requires searching for stale terms. Use `grep -n "SUSPENDED\|NOT_PROVISIONED" 
   CLAUDE.md` to locate. If 0 hits, no edit needed; surface in 
   report. Same for Item 2 hedges: 
   `grep -nE "~[0-9]+|approximately [0-9]+|about [0-9]+" CLAUDE.md`.

4. **Test counts unchanged.** This is a docs-only commit. pytest 
   MUST stay 319 / 0 / 0. If anything drifts, that's a Surface-and-
   stop event.

5. **Section ordering.** CLAUDE.md has a structure (top: current 
   state, middle: conventions/notes, bottom: FN-AB forward-notes). 
   New entries go into their natural sections; do not relocate 
   existing entries. Surface if any new entry doesn't have an 
   obvious natural home.

6. **No code edits.** Verify before staging:
   ```
   git diff --stat | grep -v CLAUDE.md | grep -v "step-6_9_housekeeping"
   ```
   Should return empty (everything else unchanged).

---

## Verification harness

```bash
# 0. Confirm clean starting state.
git status
git log --oneline -1   # 6c92661

# 1. pytest sanity (no test changes, but verify nothing broke).
uv run pytest --tb=no -q | tail -3
# Expected: 319 passed, 0 failed, 0 xfailed

# 2. mypy sanity.
uv run mypy src/admin_backend/
# Expected: clean

# 3. check_setup.
./scripts/check_setup.sh
# Expected: 35/35

# 4. Diff scope verification.
git diff --stat
# Expected: ONLY CLAUDE.md (and the bundled prompt file) appear in 
# the diff. No source files, no test files.

# 5. Grep verification on key terms.
grep -n "SUSPENDED\|NOT_PROVISIONED" CLAUDE.md
# Expected: 0 hits (or only inside historical-context entries that 
# explicitly document the discovery)

grep -nE "~[0-9]+|approximately [0-9]+" CLAUDE.md
# Expected: 0 hits in non-historical contexts (specific counts now 
# concrete)
```

---

## Report (BEFORE proposing commit)

1. Pre-flight outputs (items 1-6).
2. Per-item resolution table:
   - Item N: changes made in CLAUDE.md (section, line range, summary)
   - Item N: changes NOT made (if surfaced finding "no stale 
     references exist") and rationale
3. New FN-AB number assigned for item 4.
4. Diff stat showing only CLAUDE.md (+ prompt file) modified.
5. Verification harness output (pytest still 319, mypy clean, 
   check_setup 35/35, grep results).
6. Any deviation from locked decisions (should be none).

Wait for explicit operator authorisation before staging or 
committing.

---

## Surface-and-stop scenarios

Stop and report if:

1. Item 1 (module_access_status_enum) shows MORE than expected 
   stale references — possible that the term appears in historical 
   contexts that should be preserved. Surface for operator 
   decision per-occurrence.

2. The next FN-AB number can't be unambiguously determined (e.g., 
   numbering has a gap or duplication).

3. Any pytest test fails post-edit (should be impossible for a 
   docs-only commit; if it happens, something else is wrong).

4. `git diff --stat` shows ANY file other than CLAUDE.md and the 
   bundled prompt file changed.

5. CLAUDE.md voice/structure differs materially from existing 
   entries after the edits land — re-read and revise before 
   reporting complete.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" 
Pattern A:

```bash
git status
git add CLAUDE.md \
        prompts/step-6_9-housekeeping-2026-05-13.md

git commit -m "$(cat <<'EOF'
Section 6.9 housekeeping: CLAUDE.md cleanup

Six small documentation updates that landed as forward-notes during
Section 6.9 execution. No code or test changes; pytest 319 unchanged.

- module_access_status_enum live values corrected (ENABLED/DISABLED 
  only; remove stale SUSPENDED/NOT_PROVISIONED references if any).
- Seed count hedges replaced with concrete current values:
    tenant_users=17, platform_users=3, perm=31, rp=122, roles=15, 
    tura=19, pura=3, tma_enabled=23.
- "Note on investigation-vs-implementation discipline" added; 
  codifies the 6 stale-finding cases caught across Section 6.9 
  and the Surface-and-stop convention.
- FN-AB-NN added: Seed loader column semantics audit (Excel `id` 
  ignored, `code` honored verbatim; load-time validation deferred).
- "Note on smoke and endpoint test scripts" added: smoke_curl.sh, 
  test_endpoints.sh, test_endpoints_cloud.sh update lockstep on 
  new endpoints.
- "Note on documentation-vs-locked-decisions" added: implementation 
  reports verify documented decisions against shipped code (caught 
  example: FN-AB-31 typo .GLOBAL → .TENANT during 6.9.3.2 commit 
  review).
- "Note on cross-repo references" added: frontend product spec stays 
  in frontend repo; backend cites by section reference. Housekeeping 
  decision, not a new FN-AB entry.

prompts/step-6_9-housekeeping-2026-05-13.md bundled.

Section 6.9 documentation surface fully closed. Architecture.md 
RBAC section is a separate task (deferred to fresh session).
EOF
)" && git status
```

Run? yes / no / edit message — awaiting authorisation.

---

## End of prompt
