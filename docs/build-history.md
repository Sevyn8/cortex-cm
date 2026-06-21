# Build History

> Snapshot log of every build step's deployment state. Append one entry per
> step at workflow step 12 (Mark DONE). Format defined in
> `docs/build-step-workflow.md`.
>
> Purpose: rollback target reference + audit trail. When something breaks,
> this file is the first place to look for "what was deployed when?"

---

## Step 6.1 — RBAC read endpoints — 2026-05-05

- **Commit SHA:** `6178546`
- **Alembic head (local):** `22ccfb193cff` (was `0644a4186e48`)
- **Alembic head (Cloud SQL dev):** `22ccfb193cff` (was `0644a4186e48`)
- **Cloud Run revision (pre-deploy):** `admin-backend-00006-jzt`   ← rollback target
- **Cloud Run revision (post-deploy):** `admin-backend-00007-6kb`  ← currently serving
- **Image deployed:** `v0.1.5`
  - Digest: `sha256:c848ed44cb13532024ae0967050f7aec899313ab20971a066f7ed7dd833c6ba5`
  - Registry: `asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.5`
- **Step closure timestamp:** 2026-05-05T14:25:58Z (Cloud Run revision creation time)

### Rollback command

```bash
gcloud run services update-traffic admin-backend \
  --to-revisions=admin-backend-00006-jzt=100 \
  --region=asia-south1 --project=ithina-retail-admin
```

### What shipped

- 2 alembic migrations (`90cd038ae618` RBAC enum cleanup + `22ccfb193cff` 25-row lookups seed for permission display labels).
- 4 new endpoints: `/roles`, `/roles/{id}/permissions`, `/permissions`, `/permission-matrix`.
- 3 ORM models: `Role`, `Permission`, `RolePermission`.
- App-layer audience filtering (TENANT JWTs see only audience='TENANT' roles on E1, E3, E6).
- 23 new integration tests (5 load-bearing); pytest count: 159 → 182.

### What didn't ship (forward notes)

Captured in `BUILD_PLAN.md` under "Step 6.1 → Known follow-ups (RBAC)":
- A1, A2 — inline `roles[]` augmentation on `/tenant-users` and `/platform-users`.
- E4, E5 — `/user-role-assignments` list + single-fetch endpoints.
- MODULES-EXT — module enum extension for ROOS / GOAL_CONSOLE.
- RESOURCES-EXT — resource enum extension for MODULE_ACCESS / GUARDRAILS / APPROVALS.

### Workflow notes

This was the first build step closed using the new 12-step workflow (documented in `docs/build-step-workflow.md`). Total elapsed time post-commit: ~90 minutes (including discussion of unfamiliar mechanics on first run). Pitfalls observed during execution: items 1-9 in the workflow doc's "Pitfalls log" section.

The `scripts/env.sh` and `scripts/smoke_curl.sh` helpers were authored AFTER this step's execution, based on lessons learned. Future build steps will use them from step 5 (env sourcing) and steps 7/11 (smoke) respectively.

---

<!-- Append future step entries above this line, in reverse chronological order -->
