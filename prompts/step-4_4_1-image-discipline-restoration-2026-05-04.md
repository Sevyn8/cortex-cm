# Prompt — Step 4.4.1: Dockerfile.seed teardown + image discipline restoration

> Paste this entire block into a fresh Claude Code session when starting Step 4.4.1.
>
> **Trigger.** Step 4.3.5 commit has landed; row counts and FORCE-RLS verified
> against dev Cloud SQL; `curl /api/v1/tenants/stats` returns
> `{"total_tenants": 7, "total_stores": 25}` through the deployed service.
>
> **What this step does.** Reverses the temporary discipline deviation
> introduced at Step 4.3.5. Removes `Dockerfile.seed`,
> `scripts/build_seed_image.sh`, the `v0.1.3-seed` registry tag, and
> (operator-decided) the `admin-backend-seed-dev-data` Cloud Run Job.
> Closes FN-AB-XX. Unblocks Step 4.5.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` FN-AB-XX block. Confirm it's still in the open
   state (no RESOLVED note appended yet) with resolution criterion
   matching what this step does.
3. Read `BUILD_PLAN.md` Step 4.4.1 entry (added at Step 4.3.5 commit).
   Confirm Status is TODO and the "Blocks Step 4.5" line is intact.
   Also read Step 4.3.5 entry — confirm Status is DONE, the seeding
   ran successfully, and the Outcome notes match what was reported
   at Phase 3 of Step 4.3.5.
4. Read `prompts/step-4_3_5-cloud-sql-seed-loader-2026-05-04.md`
   "Phase 4" and "After completing" sections — confirms what shipped
   at Step 4.3.5 and what this cleanup must reverse.
5. **Note on Step 4.4 independence.** Step 4.4 (cross-tenant test
   per `prompts/step-4_4-cloud-run-deploy-dev.md`) is unblocked by
   Step 4.3.5 but does NOT block Step 4.4.1. The two run in
   parallel: 4.4.1 is repo cleanup; 4.4 section 5 is verification.
   Either can complete first.
6. Verify the Step 4.3.5 row-count assertions are still passing:
   ```bash
   curl -sS https://admin-backend-f2qhpcdeba-el.a.run.app/api/v1/tenants/stats
   ```
   Should return `{"total_tenants": 7, "total_stores": 25}`. If it
   doesn't, STOP — Step 4.3.5's seeded data has been clobbered, which
   is a different problem than this cleanup. Investigate before
   proceeding.
7. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 4.4.1** — Restore the v0.1.2-era image discipline now that
one-off Cloud SQL seeding (Step 4.3.5) is complete and verified. Close
FN-AB-XX. Unblock Step 4.5.

This is bookkeeping with operator-driven GCP cleanup. Claude Code
removes the two temp files from the repo and amends the docs. The
operator runs `gcloud artifacts docker tags delete` and (optionally)
`gcloud run jobs delete`.

Single commit. Smaller than Step 4.3.5.

---

## Scope in

### Phase 1 — verify pre-conditions (Claude Code)

Run these checks and report each result:

- `Dockerfile.seed` exists in repo HEAD and is unmodified since Step 4.3.5:
  ```bash
  git log -1 --format='%h %s' -- Dockerfile.seed
  # Should be the Step 4.3.5 commit; nothing newer.
  git diff HEAD -- Dockerfile.seed
  # Should be empty (no working-tree mods).
  ```
- `scripts/build_seed_image.sh` exists in repo HEAD and is unmodified
  since Step 4.3.5 (same two commands).
- **Production-file drift check.** `git diff HEAD -- Dockerfile
  .dockerignore pyproject.toml uv.lock` should return empty. The Step
  4.3.5 build script's EXIT-trap should have restored `.dockerignore`
  cleanly + the post-build `git diff` check would have caught any
  drift before Step 4.3.5 commit landed. This is the belt-and-suspenders
  re-verification.

  **If drift IS found here:** the working tree has changes that weren't
  part of Step 4.3.5's commit. Recovery:
  ```bash
  git checkout HEAD -- .dockerignore Dockerfile pyproject.toml uv.lock
  ```
  Then re-verify with `git diff`. Only proceed once clean.

- `gcloud artifacts docker tags list \
    asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend \
    --filter="tag:v0.1.3-seed"` returns one entry (confirming the tag
  is still present and ready for cleanup).
- `gcloud run jobs list --region=asia-south1 --project=ithina-retail-admin
    --filter="metadata.name:admin-backend-seed-dev-data"` returns one
  entry.

STOP. Show operator the pre-condition check results. Wait for OK.

### Phase 2 — repo cleanup (Claude Code)

Four edits. Stage but don't commit yet.

#### Edit 1: Delete `Dockerfile.seed`

```bash
git rm Dockerfile.seed
```

#### Edit 2: Delete `scripts/build_seed_image.sh`

```bash
git rm scripts/build_seed_image.sh
```

#### Edit 3: `CLAUDE.md` — flip FN-AB-XX to RESOLVED

Append a one-line resolution note to the FN-AB-XX block:

```markdown
**Resolved at Step 4.4.1 (commit <SHA>, 2026-05-04).** The temporary
`Dockerfile.seed` and `scripts/build_seed_image.sh` removed from repo.
Artifact Registry tag `v0.1.3-seed` deleted (digest GC-eligible). The
`admin-backend-seed-dev-data` Cloud Run Job [deleted | retained as
paused artifact] per operator decision (see commit description).
Image discipline restored to v0.1.2-era posture. Step 4.5 unblocked.
```

The `<SHA>` placeholder gets resolved post-commit (either via a
follow-up amend, or just left as `<SHA>` — historical record, not
load-bearing). The `[deleted | retained]` choice gets resolved by
operator decision in Phase 3 before the commit message is finalised.

#### Edit 4: `BUILD_PLAN.md` — flip Step 4.4.1 to DONE

Update Step 4.4.1's Status:

```markdown
**Status.** DONE
```

Append an Outcome block to the entry:

```markdown
**Outcome.** Dockerfile.seed and scripts/build_seed_image.sh removed
from repo HEAD. `git diff` confirms Dockerfile, .dockerignore,
pyproject.toml, uv.lock unchanged from pre-Step-4.3.5 baseline.
v0.1.3-seed tag deleted from Artifact Registry. admin-backend-seed-dev-data
Cloud Run Job [deleted | retained]. FN-AB-XX RESOLVED. Step 4.5 unblocked.
```

STOP. Show operator the four edits. Wait for OK to proceed to Phase 3.

### Phase 3 — operator-driven GCP cleanup

Hand the operator two commands. The second is conditional on operator
preference.

#### Command (a) — delete the v0.1.3-seed tag (always)

```bash
gcloud artifacts docker tags delete \
  asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.3-seed \
  --quiet
```

The underlying digest stays (registry GC-eligible on its own schedule).
Tag removal is the load-bearing part — prevents accidental redeployment.

Verify:

```bash
gcloud artifacts docker tags list \
  asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend \
  --filter="tag:v0.1.3-seed"
```

Should return empty.

#### Command (b) — Cloud Run Job disposition (operator's call)

**Recommended default: delete.**

```bash
gcloud run jobs delete admin-backend-seed-dev-data \
  --region=asia-south1 \
  --project=ithina-retail-admin \
  --quiet
```

**Alternative: retain as paused artifact.** No command needed. The Job
sits in the project consuming zero compute (Jobs only run when explicitly
executed); inspectable via `gcloud run jobs describe`. If operator
chooses retain, the Job's frozen reference to the (now-untagged)
v0.1.3-seed digest is harmless because (i) the digest is GC-eligible
and will eventually go away, (ii) executing the Job after digest GC
would fail with "image not found" — a clear failure mode, not silent.

**Why default to delete.** The Dockerfile.seed pattern is reconstructable
from the Step 4.3.5 prompt + git history (~10 min of work). A retained
Job with a dangling digest reference is a minor footgun outweighing
the small reconstruction cost.

After operator decides, update Edit 3 and Edit 4 in Phase 2 (the
FN-AB-XX resolution line and the BUILD_PLAN.md Outcome line) with the
chosen disposition. The commit hasn't been proposed yet at this point —
the edits are in the working tree but not staged or committed.

### Phase 4 — commit (after operator OK)

Re-show the working tree state:

```bash
git status
# Expected:
#   deleted:  Dockerfile.seed
#   deleted:  scripts/build_seed_image.sh
#   modified: CLAUDE.md
#   modified: BUILD_PLAN.md
#   new:      prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md
```

Stage:

```bash
git add CLAUDE.md BUILD_PLAN.md \
        prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md
# Note: Dockerfile.seed and scripts/build_seed_image.sh are already
# staged for deletion via the `git rm` calls in Phase 2.
```

Propose this commit message (with the operator's disposition substituted
into the bracketed choices):

```
Step 4.4.1: Dockerfile.seed teardown + image discipline restored

- Delete Dockerfile.seed and scripts/build_seed_image.sh from repo.
- Verify Dockerfile, .dockerignore, pyproject.toml, uv.lock are
  byte-identical to pre-Step-4.3.5 state (build script's EXIT-trap
  restoration + post-build git diff check worked as designed at
  Step 4.3.5 commit time; re-verified here).
- Operator deleted v0.1.3-seed tag from Artifact Registry; underlying
  digest is GC-eligible.
- Operator [deleted | retained] admin-backend-seed-dev-data Cloud
  Run Job per [chosen disposition].
- CLAUDE.md FN-AB-XX → RESOLVED; image discipline at v0.1.2-era posture.
- BUILD_PLAN.md Step 4.4.1 → DONE; Step 4.5 unblocked.
- prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md
  added (this prompt).
```

Ask operator: "Run? yes / no / edit message".

---

## Scope out

- The long-term prod/dev image split. Day 8 concern (pre-prod). This
  step does NOT amend Step 4.2's Dockerfile or introduce new
  Dockerfile targets.
- Any change to the deployed admin-backend service. Service stays on
  v0.1.2 (which is unaffected by the v0.1.3-seed tag removal — service
  references its image by digest, not tag).
- Any change to Cloud SQL state. Seeded data persists.
- Step 4.4 cross-tenant test (the existing
  `prompts/step-4_4-cloud-run-deploy-dev.md` section 5 work).
  Independent track; not blocked by Step 4.4.1.
- Any new BUILD_PLAN entries beyond marking 4.4.1 DONE. Step 4.5
  was already created at Step 3.4.5 era; this step just unblocks it
  by satisfying the "Blocked by Step 4.4.1" precondition.

---

## Testing and regression discipline

### Pre-commit checks

- `git ls-files | grep -E 'Dockerfile.seed|build_seed_image'` returns
  empty.
- `git diff HEAD~1 -- Dockerfile .dockerignore pyproject.toml uv.lock`
  empty (no drift in production files between pre-Step-4.3.5 and
  post-Step-4.4.1).
- `./scripts/check_setup.sh` 35/35 (no regressions from removing
  the two files).
- pytest count unchanged from Step 4.3.5 commit (no test changes in
  this step).
- mypy strict clean (no source code changes).

### Post-commit checks

- `gcloud artifacts docker tags list ...` does not show `v0.1.3-seed`.
- `gcloud run jobs list ...` matches operator's chosen disposition
  (Job present if retained, absent if deleted).
- `curl /api/v1/tenants/stats` still returns
  `{"total_tenants": 7, "total_stores": 25}` (proves cleanup did not
  affect runtime data — sanity since this step doesn't touch Cloud SQL,
  but worth confirming).
- `/api/v1/health` and `/api/v1/ready` still 200 with `db:ok`.

### Tests deliberately not added

- New tests covering "image discipline." This is a one-time
  bookkeeping cleanup; testing the absence of a file is over-engineering.

### Regression risk surface

Minimal. Three failure modes worth naming:

1. The build script's EXIT-trap didn't actually restore `.dockerignore`
   cleanly during Step 4.3.5, AND the Step 4.3.5 commit's post-build
   `git diff` check missed it (extremely unlikely — the check is in
   the build script's own output and would have failed Step 4.3.5's
   acceptance). The Phase 1 `git diff` check here is the second-line
   catch.
2. The operator forgets to run the registry tag removal. Phase 3's
   verification command catches this; commit message reflects actual
   state.
3. The operator deletes the Cloud Run Job AND wants to re-seed later.
   Mitigation: Dockerfile.seed pattern is reconstructable from the
   Step 4.3.5 prompt + git history. Cost of "rebuild from history" is
   ~10 min; cost of "keep dangling Job around" is ongoing footgun.

---

## Acceptance criteria

- `Dockerfile.seed` and `scripts/build_seed_image.sh` removed from repo.
- `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`
  byte-identical to their pre-Step-4.3.5 state.
- `v0.1.3-seed` tag removed from Artifact Registry.
- `admin-backend-seed-dev-data` Cloud Run Job either deleted or
  retained per operator decision (recorded in commit message and
  FN-AB-XX resolution note).
- CLAUDE.md FN-AB-XX block has the RESOLVED suffix.
- BUILD_PLAN.md Step 4.4.1 → DONE with Outcome summary; Step 4.5
  "Blocked by" line resolved (still present for historical record;
  status is unblocked).
- One commit lands containing: deletions of Dockerfile.seed and
  scripts/build_seed_image.sh, CLAUDE.md amend, BUILD_PLAN.md amend,
  this prompt file.
- `curl /api/v1/tenants/stats` still returns the seeded counts
  (cleanup did not affect runtime data).

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Pre-condition check.** Output of `git diff HEAD -- Dockerfile
   .dockerignore pyproject.toml uv.lock` (should be empty); confirmation
   Dockerfile.seed and scripts/build_seed_image.sh exist in HEAD;
   confirmation v0.1.3-seed tag and admin-backend-seed-dev-data Job
   present in registry / Cloud Run.

2. **Repo edits.** The four edits with line counts (file deletions
   confirmed via `git status`; CLAUDE.md FN-AB-XX diff;
   BUILD_PLAN.md Step 4.4.1 diff).

3. **GCP cleanup.** Operator's outputs from `tags delete` and (if
   chosen) `jobs delete`. Verification command outputs showing the
   tag is gone and the Job state matches operator's decision.

4. **Post-cleanup verification.** `/api/v1/health`, `/api/v1/ready`,
   `/api/v1/tenants/stats` outputs (all should still pass and return
   the seeded counts). check_setup 35/35; pytest count unchanged;
   mypy strict clean.

5. **Anything that needed adjustment.** Drift findings from the
   `git diff` check (and recovery if any was needed); operator-side
   surprises; rationale if operator chose to retain the Job.

Wait for explicit authorisation before staging or committing.

---

## After completing

When operator authorises (after reviewing the report), propose:

```
git status
git add CLAUDE.md BUILD_PLAN.md \
        prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md
# (Dockerfile.seed and scripts/build_seed_image.sh are staged for
# deletion via `git rm` in Phase 2.)
git commit -m "<commit message from Phase 4>"
```

Ask operator: "Run? yes / no / edit message".

After commit lands: Step 4.5 is unblocked; resume normal BUILD_PLAN
sequencing. Step 4.4 cross-tenant test (existing prompt section 5)
is independent and runs when operator is ready.

---

## Guardrails throughout

- **Do NOT modify** `Dockerfile`, `.dockerignore`, `pyproject.toml`,
  or `uv.lock`. These should already be at pre-Step-4.3.5 state from
  the EXIT-trap restoration + the Step 4.3.5 commit's post-build check.
  If `git diff` shows drift here, the recovery is
  `git checkout HEAD -- .dockerignore Dockerfile pyproject.toml uv.lock`
  and STOP — investigate before any cleanup. Do not "fix forward" on
  drift; restore from HEAD first.
- **Do NOT modify** the deployed admin-backend Cloud Run service.
  Service stays on v0.1.2.
- **Do NOT delete** any other Cloud Run Job (`admin-backend-alembic`
  is the Step 4.1 precedent — leave it alone).
- **Do NOT delete** the v0.1.2 image tag, the v0.1.1 tag, or any
  other tag in Artifact Registry. Only `v0.1.3-seed` comes out.
- **Do NOT modify** `prompts/step-4_4-cloud-run-deploy-dev.md`. That
  was amended at Step 4.3.5; this step doesn't touch it.
- **Do NOT touch** Cloud SQL state, Secret Manager, IAM bindings, VPC,
  or any other piece of infra. This is a documentation + tag-removal
  step.
- **STOP at every STOP gate.** Phase 1 pre-conditions → Phase 2 edits
  proposed → Phase 3 operator decision → Phase 4 commit proposal.
  Four STOP gates total.

---

## End of prompt
