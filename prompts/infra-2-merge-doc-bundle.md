# Prompt — Infra-2: Hand-merge doc-update bundle into backend repo

> Paste this entire block into a fresh Claude Code session running in the
> **backend repo** (`ithina-retail-admin-backend`).

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully.
3. Read `docs/architecture.md` fully — you'll be modifying D-23, the
   deployment topology section, and adding D-31.
4. Read `BUILD_PLAN.md` fully — you'll be modifying Steps 1.7.1, 1.7.2,
   4.1, 4.4, 8.1.1, 8.2.
5. Read the doc-update bundle the operator provides. Its path is something
   like `<BUNDLE_PATH>/docs-update-2026-05-03.md`. **Read it fully before
   making any change.**
6. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Infra-2** — Hand-merge the doc-update bundle into the backend repo's three
canonical documents (`docs/architecture.md`, `BUILD_PLAN.md`, `CLAUDE.md`),
then delete the bundle.

The bundle exists because two architectural decisions changed on 2026-05-03:

1. **D-23 revised** — provision via Terraform from day one (was: defer Terraform
   to post-launch).
2. **D-30 added** — Cloud Run for the frontend (both envs).
3. **D-31 added** — Cloud Run for the backend in dev; GKE in prod.

The bundle is a temporary artefact. After this merge, the truth is the merged
docs, not the bundle. The bundle file is deleted as part of this step.

This is a CLAUDE_CODE step. The operator reviews the resulting diffs before
authorising the commit.

---

## Scope in

### Source of truth

The bundle (`<BUNDLE_PATH>/docs-update-2026-05-03.md`) has 6 numbered sections:

1. `docs/architecture.md` — D-23 revision (replace existing entry).
2. `docs/architecture.md` — new D-31 (insert after D-30).
3. `docs/architecture.md` — deployment topology (replace single GKE diagram
   with two diagrams: dev shape, prod shape).
4. `BUILD_PLAN.md` — replace Steps 1.7.1, 1.7.2, 4.1, 4.4, 8.1.1, 8.2.
5. `CLAUDE.md` — additions: standing-context line, D-30, D-31.
6. `docs/post-launch-backlog.md` — minor updates (delete entry / add entry).

**Read each section in full** before applying. Some sections are
"REPLACE WITH"; some are "INSERT AFTER"; some are "REMOVE FROM".

### Files to modify

- `docs/architecture.md`
- `BUILD_PLAN.md`
- `CLAUDE.md`
- `docs/post-launch-backlog.md` (if it exists; create if it doesn't and
  there's anything to add)

### Files NOT to modify

- The Terraform code (lives in the separate infra repo).
- The 8 DDL files (frozen per project convention).
- Existing prompts in `prompts/` (those are one-shot historical artefacts).
- Existing migrations (encode schema changes only, not doc changes).

### Steps to perform

1. **Snapshot** the current state of each target file. Use `git diff` after
   to confirm changes are exactly what you intended.

2. **`docs/architecture.md` — D-23 revision (Section 1 of bundle).**
   Find the existing D-23 entry. It currently says something like
   "Skip Terraform / ArgoCD / DR / Cloudflare for MVP" — replace with the
   "D-23 — Terraform from day one for infra; skip ArgoCD / DR / Cloudflare
   for MVP (revised 2026-05-03)" entry from the bundle.

   Also find the "What v0 defers" table. The Terraform row should either be
   removed entirely or updated to read "Provisioned via Terraform from day
   one (D-23 revised)." Make the call based on what reads cleaner — the
   table's purpose is to list deferred items, so removing is cleaner.

3. **`docs/architecture.md` — new D-31 (Section 2 of bundle).**
   D-30 was already added at some prior point — check if it exists. If it
   doesn't, also add D-30 from CLAUDE.md Section 5 of the bundle (it's
   short).

   Insert D-31 after D-30 in the decision-history list.

4. **`docs/architecture.md` — deployment topology (Section 3 of bundle).**
   Find the "Per-region stack" subsection. It currently has one ASCII
   diagram showing the GKE-shaped deployment. Replace with the two
   diagrams from Section 3 of the bundle (dev shape, prod shape).

   Update any prose around the diagram that assumed a single shape — e.g.,
   "the FastAPI pod's middleware verifies JWT" still applies, but
   references to "GKE pods" need to be qualified ("in prod" or "in both
   envs depending on environment").

5. **`BUILD_PLAN.md` — Step updates (Section 4 of bundle).**

   - **Step 1.7.1** — replace with the Terraform-for-dev-provisioning
     version. Status: DONE (2026-05-03).
   - **Step 1.7.2** — replace with the Terraform README version. Status: DONE.
   - **Step 4.1** — replace with the Cloud Run Job version. Status: TODO.
     Note the re-ordering (after 4.2/4.3) in the step description.
   - **Step 4.4** — replace with the Cloud Run deploy version. Status: TODO.
     Note D-31. The original GKE version of this step moves to Step 8.2.
   - **Step 8.1.1** — replace with the Terraform-envs/prod version.
     Status: TODO.
   - **Step 8.2** — replace with the GKE-prod version. Status: TODO.

   Don't change step status fields that are currently DONE for steps not
   listed (e.g., 3.x, 5.x).

6. **`CLAUDE.md` — additions (Section 5 of bundle).**

   - Add the "Standing context" line about the `terraform/` directory at
     repo root. **Caveat:** the `terraform/` directory is in the *infra
     repo*, not the backend repo. Adjust the wording to "The
     ithina-retail-admin-infra repo manages this project's GCP infra; read
     `terraform/README.md` there when making infra changes." instead of
     pretending it's at the backend repo's root.
   - Add D-30 if not already present.
   - Add D-31 (full text from Section 2 of the bundle).

7. **`docs/post-launch-backlog.md` (Section 6 of bundle).** Open the file.
   - If "Capture infra in Terraform" exists, remove it.
   - Add: "Move dev backend to GKE for prod-shape parity (re-evaluate D-31)".
   - If the file doesn't exist, **don't create it just for this** — that
     file gets created at BUILD_PLAN Step 10.2. Note in the report that
     this section will be applied at Step 10.2 instead.

8. **Delete the bundle file**:
   ```bash
   rm <BUNDLE_PATH>/docs-update-2026-05-03.md
   ```

   The bundle was always a temporary artefact. After merge, the truth is the
   updated docs.

9. **Verify** with `git diff`:
   - Each modified file should have changes only in the expected sections.
   - No unintended formatting changes (line endings, whitespace, etc.).
   - mypy and tests are not affected (this is doc-only work, but run
     `pytest --collect-only` and `mypy --strict src/` as smoke).

10. **Propose commit** following the per-step bundling convention:
    ```
    Step infra-2: hand-merge doc-update bundle (D-23 rev / D-30 / D-31)

    - architecture.md: D-23 revised (Terraform from day one); D-31 added
      (Cloud Run backend in dev, GKE in prod); deployment topology split
      into dev/prod diagrams
    - BUILD_PLAN.md: Steps 1.7.1, 1.7.2, 4.1, 4.4, 8.1.1, 8.2 updated to
      reflect new shapes
    - CLAUDE.md: standing-context line for infra repo; D-30 + D-31 added
    - bundle file deleted (was temporary)
    ```

    Ask operator "Run? yes / no / edit message".

---

## Scope out

- **Running `terraform` commands** — that's the infra repo's operator workflow.
- **Modifying Terraform code** — out of scope; the infra is a separate repo.
- **Adding new prompts to `prompts/`** — not part of doc merge.
- **Updating the Step 4.1/4.4 prompts** that the operator received earlier —
  those are already in the right shape; just place them in `prompts/` if
  they're not there already (separate operator action).
- **Implementing any code changes** — this is doc-only.

---

## Implementation hints

- The bundle uses "REPLACE WITH" / "INSERT AFTER" markers. Treat them as
  instructions, not as content to copy literally.
- For the deployment topology section (Section 3), the existing diagram
  in `architecture.md` is ASCII art. Match the style of the bundle's
  diagrams (also ASCII). Don't switch to Mermaid or PlantUML.
- The bundle's "D-31" entry is long with multiple paragraphs (What, Why,
  Trade-off accepted, Reconsider if). Keep the full structure; don't
  abbreviate.
- D-30 and D-31 are decisions, so they go in the decision-history list at
  the end of architecture.md (or wherever D-1 through D-29 currently live —
  search for "D-29" to find the spot).
- `BUILD_PLAN.md` step replacements: the existing steps may have additional
  fields (Coordination, Rough effort) that the bundle doesn't include.
  Preserve those fields with their original values unless the bundle
  explicitly changes them.
- The `CLAUDE.md` "Standing context" addition needs adjustment because the
  Terraform isn't actually at this repo's root — see Step 6 above.

---

## Acceptance criteria

- `docs/architecture.md`: D-23 entry reflects the revision; D-30 and D-31
  exist; deployment topology has dev and prod diagrams.
- `BUILD_PLAN.md`: Steps 1.7.1, 1.7.2 marked DONE; 4.1, 4.4, 8.1.1, 8.2
  reflect new shapes.
- `CLAUDE.md`: Standing-context entry mentions the infra repo; D-30 and
  D-31 added.
- `docs/post-launch-backlog.md`: updated if exists; deferral noted in
  report otherwise.
- Bundle file is deleted.
- `git diff` shows only the expected changes in only the expected files.
- Tests/mypy still pass (smoke check; should be unaffected).
- Commit message follows the per-step convention.

---

## Stop and ask if

- The existing D-23 entry's wording is materially different from what the
  bundle assumes (the project may have updated the entry between bundle
  creation and now). Surface and confirm before replacing.
- The deployment topology section in `architecture.md` doesn't match what
  the bundle assumes it looks like — same surface-and-confirm.
- The `CLAUDE.md` already has D-30 from a prior session — don't add it
  twice; just add D-31.
- `BUILD_PLAN.md` Step 4.4 has had work done on it already (status DONE,
  commits referenced) — replacing it would falsify history. Surface and
  ask whether to add a new "Step 4.4 (revised)" or genuinely overwrite.

---

## What to report at end (5-bundle convention)

1. **Code/configs:** None — doc-only step.
2. **CLAUDE.md updates:** Yes (D-30, D-31, standing-context line) — paste
   the diff.
3. **BUILD_PLAN.md updates:** Yes (six steps) — paste the diff.
4. **architecture.md updates:** Yes (D-23, D-31, topology) — paste the diff.
5. **Prompt file:** `prompts/infra-2-merge-doc-bundle.md` should be added
   to the commit set if it isn't already.

Plus: confirmation that the bundle file is deleted; mypy/pytest smoke
results; word-count delta of the three target files.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
