# Workflow amendments: lean CLAUDE.md current-state, framing-only Appendix A, with_root fixture, pre-flight reframing

## Scope

One focused commit. Four amendments bundled, all rooted in the Step 6.15 token-budget post-mortem:

1. CLAUDE.md `### Completed` entries (under `## Current state`) cap at ~200 words; detail lives in the step doc.
2. Impl-prompt Appendix A carries architectural framing only, not verbatim code bodies.
3. `tests/integration/conftest.py::make_tenant` gains `with_root: bool = False` to retire the duplicated `_make_tenant_with_root` helper pattern.
4. Pre-flight directives in impl prompts stop telling the implementer to "read CLAUDE.md end-to-end" (it is pre-loaded as project knowledge); reframe to "surface any contradiction."

No new WBS, no Phase 0-6 ceremony. Treat as a focused docs+small-code commit. Single commit; report-before-commit gate applies.

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 36/36 PASS.
2. Confirm HEAD is `bfbbe82` (Step 6.15) and tree is clean (excluding the known pre-existing `prompts/retro-step-6_10_1-2026-05-15-v2.md` untracked file).
3. Grep current call sites of the duplicated helper pattern:
   - `grep -rn "_make_tenant_with_root\|make_tenant.*make_org_node" tests/`
   - Confirm the only occurrence is in `tests/integration/test_module_access_writes_router.py`; surface any other site.

## Amendments

### Amendment 1: CLAUDE.md `### Completed` entries (under `## Current state`) cap at ~200 words

**WORKFLOW.md change.** In the "Per-step outcome" section bullet list (line near "CLAUDE.md updated only if a new project-wide convention emerged"), append a sibling bullet:

> - The CLAUDE.md `### Completed` entry (under `## Current state`) for a new step is a 1-2 sentence summary at most: step ID, feature name, status with commit or revision tag, and a pointer to `docs/implementation-steps/step-X_Y-<name>-YYYY-MM-DD.md`. Detailed metrics, LD honour records, surface-and-stop findings, FN-AB cross-references, and per-resource regression counts live in the step doc, not in CLAUDE.md. Convention rationale lands in CLAUDE.md only if it survives the existing "Doc Economy" test: applies beyond this step, actionable without further context, absence would plausibly cause a future bug.

**CLAUDE.md change.** Under "Document maintenance" section, replace or extend the existing bullet "Update CLAUDE.md when a decision changes, a new convention is set, or current state moves significantly" with:

> - Update CLAUDE.md when a decision changes (D-NN), a new convention is set, or a forward-note (FN-AB) opens or resolves. `### Completed` entries are 1-2 sentence pointers to the step doc, NOT step changelogs. The step doc is the canonical record for retro detail per A6; CLAUDE.md is the index of standing context.

**Suggested entry format** for the next step (do NOT edit any existing entries in this commit; new format applies going forward):

```
- **Step X.Y[.Z]: <feature>.** <Status>: DONE-LOCAL at `<commit>` (or DONE at `vX.Y.Z`). <one-sentence scope summary>. Detail: `docs/implementation-steps/step-X_Y-<name>-YYYY-MM-DD.md`.
```

**Out of scope.** Retro-trimming the existing 6.10.1 / 6.11.2 / 6.15 long-form entries. That is a separate cleanup commit; surface only if the operator asks.

### Amendment 2: Impl-prompt Appendix A carries framing, not verbatim code

**WORKFLOW.md change.** Under "Phase 4 - Prompt", in the sub-section "Architecture doc updates — inline in impl prompt as Appendix", replace the existing guidance with:

> When a step requires updates to architecture docs (`architecture.md`, `architecture_RBAC.md`, or similar):
>
> - **Phase 2 close**: Chat drafts the **architectural framing prose** for the doc update: design intent, divergence notes, transition matrices, lifecycle diagrams, what-this-demonstrates summaries. Captured as a review artifact in `/mnt/user-data/outputs/`.
> - **Phase 4 draft**: the approved framing prose is folded into the impl prompt as Appendix A. **The code body of the worked example is NOT included verbatim**; the implementer composes it from the shipped implementation at apply time.
> - **Phase 5 implementation**: the implementer applies the Appendix A framing prose to the target doc file and composes any accompanying code block fresh against the live code. Surface composed code in the final report for operator review.
> - **The appendix remains excluded from the code-volume gate.**
>
> Rationale: verbatim code in Appendix A round-trips through the wire three times (prompt input, Edit old_string of surrounding text, Edit new_string), with the code body itself adding zero design value beyond what live code already conveys. Framing prose (URL convention rationale, cascade semantics commentary, divergence notes vs precedents) is what Chat-side design adds; that stays in Appendix A.

### Amendment 3: `make_tenant(with_root: bool = False)` and retire the helper

**`tests/integration/conftest.py` change.** Extend the existing `make_tenant` fixture signature with a `with_root: bool = False` keyword-only parameter. When `with_root=True`, after inserting the tenant row, also insert a TENANT-type root org_node anchored at the tenant. The org_node `code` follows the DDL CHECK constraint `^[A-Za-z0-9][A-Za-z0-9-]+[A-Za-z0-9]$` (no underscores); use `t-<short-hex>` format (mirror of the retired helper's existing convention). The org_node `path` is the tenant's ltree root (single segment); `name` mirrors the tenant name or a sensible default.

Behaviorally:
- `make_tenant(...)` (no kwarg): unchanged; no root org_node created. All existing callers continue to work.
- `make_tenant(..., with_root=True)`: creates both tenant row AND root org_node; anchor-using endpoints (`anchor_dep=get_tenant_anchor` and equivalents) resolve successfully.

**`tests/integration/test_module_access_writes_router.py` change.** Delete the local `_make_tenant_with_root` helper. Update its call sites to use `make_tenant(..., with_root=True)`. Same fixture-order discipline applies.

**`tests/integration/conftest.py` docstring.** The `make_tenant` fixture's docstring gains a paragraph naming the caller contract:
> Pass `with_root=True` when the test exercises any endpoint gated with `anchor_dep=get_tenant_anchor` (or any anchor dep that resolves an org_node). Without the root, the anchor dep returns 404 ahead of the gate body, masking the actual test intent. Default False to preserve the existing semantics for tests that do not need anchor reachability.

**CLAUDE.md change.** Under "Code conventions" near the existing fixture-related notes (the cleanup-fixture-ordering note from Step 6.11), add a new subsection:

> **Note on `make_tenant` and anchor reachability.** Tests exercising endpoints gated with `anchor_dep=get_tenant_anchor` (or any anchor dep that resolves an org_node) must construct the tenant with `make_tenant(..., with_root=True)`. The default `with_root=False` creates only the tenant row; without a TENANT-type root org_node, the anchor dep returns 404 before the gate body fires, masking test intent. The pattern surfaced twice (6.9.3.2 cleanup audit observation; Step 6.15 surface-and-stop finding #4) before being promoted to a fixture parameter at this commit; do not regress to manual `make_tenant + make_org_node(node_type='TENANT')` pairing in test files.

### Amendment 4: pre-flight directives reframe "read X end-to-end"

**WORKFLOW.md change.** Under "Pre-flight discipline — read project context docs end-to-end" sub-section in Phase 4, replace the current guidance with:

> Every impl prompt MUST include a pre-flight directive making the implementer aware of the project context docs. These carry load-bearing conventions and current-state assertions the step's changes must integrate with. The standing list, in order:
>
> - **CLAUDE.md**: codified conventions (D-N decisions), open FN-AB forward notes, `### Completed` entries.
> - **BUILD_PLAN.md**: confirm step structure (where this step sits, what's pending, what's just landed).
> - **docs/architecture.md**: error envelope shape, RLS posture, AuthContext / JWT structure, anchor dependency mechanics.
> - **docs/architecture_RBAC.md**: Two-layer gate semantics, audience parameter, worked-example format.
>
> These docs are pre-loaded into the implementer's session as project knowledge. The pre-flight directive should NOT read "read CLAUDE.md end-to-end" (effectively a no-op for token spend, since the docs are already in context). The load-bearing behavior is: **surface any contradiction between this prompt and any of these docs before proceeding; do not silently work around. The docs usually win.**
>
> For DDL spot-checks, `docs/schema/current_schema.sql` is large (~1500 lines) and NOT typically pre-loaded. The pre-flight directive for DDL reads should be **section-scoped**, not end-to-end: read the `CREATE TABLE core.<table>` block plus its constraint/FK/index/trigger/policy blocks for each touched table. Example: `grep -A 60 "CREATE TABLE core.tenant_module_access" docs/schema/current_schema.sql` plus separate greps for the table's constraint and policy names.

(The existing "Surface-and-stop" language in the original section is preserved; only the "read end-to-end" wording changes.)

## File-by-file change list

| File | Change | Lines |
|---|---|---|
| WORKFLOW.md | Amendments 1, 2, 4 (text changes in 3 sub-sections) | ~40 net |
| CLAUDE.md | Amendments 1, 3 (text changes in 2 sub-sections) | ~15 net |
| tests/integration/conftest.py | `make_tenant` gains `with_root` kwarg + docstring extension | ~30 net |
| tests/integration/test_module_access_writes_router.py | Delete local `_make_tenant_with_root` helper; update call sites to `make_tenant(..., with_root=True)` | ~-15 net |

## Verification

```
./scripts/check_setup.sh                            # expect 36/36
pytest -q tests/integration/test_module_access_writes_router.py  # expect all green (14 tests)
pytest -q                                            # expect 437 passing, 0 xfailed (unchanged from bfbbe82 baseline)
mypy --strict src/admin_backend tests scripts        # expect clean
```

No new tests added; existing 437 must all still pass.

## Surface-and-stop scenarios

1. `make_tenant` fixture is structured in a way that makes adding a kwarg awkward (e.g., it's a factory-of-factories or uses a class). Surface; do not refactor the fixture shape as part of this commit.
2. Other test files use `_make_tenant_with_root` or an equivalent helper not surfaced by the pre-flight grep. Surface and include in the call-site update.
3. The TENANT-type org_node insert in `with_root=True` has FK or CHECK constraints not anticipated here (e.g., a parent_id requirement). Surface the actual constraint; the helper pattern from `test_module_access_writes_router.py` is the reference shape.
4. Existing CLAUDE.md `### Completed` entries (under `## Current state`) exceed ~200 words, and operator wants a retro-trim of the bottom-N entries bundled in this commit. Surface; operator decides whether to include or defer.

## Report shape

```
Workflow amendments commit. Ready to stage

Files changed:
  WORKFLOW.md +N -M
  CLAUDE.md +N -M
  tests/integration/conftest.py +N -M
  tests/integration/test_module_access_writes_router.py +N -M

Verification:
  check_setup 36/36
  pytest 437/437 passed, 0 xfailed
  mypy strict clean

Amendments applied:
  1. CLAUDE.md current-state lean (WORKFLOW.md + CLAUDE.md)
  2. Appendix A framing-only (WORKFLOW.md)
  3. make_tenant(with_root=True) + helper retirement (conftest.py + test file + CLAUDE.md)
  4. Pre-flight read reframing (WORKFLOW.md)

Surface-and-stop findings: 0 or N (per scenarios above)
```

## Suggested commit message

```
docs(workflow): step-doc-first retro, framing-only Appendix A, with_root fixture, pre-flight reframing

Four amendments rooted in Step 6.15's token-budget post-mortem:

- CLAUDE.md `### Completed` entries cap at 1-2 sentences;
  detail lives in docs/implementation-steps/. WORKFLOW.md Per-step
  outcome + CLAUDE.md Document maintenance amended.
- Impl-prompt Appendix A carries architectural framing prose only;
  code bodies composed fresh against shipped implementation.
  WORKFLOW.md Phase 4 architecture-doc-updates sub-section rewritten.
- tests/integration/conftest.py make_tenant gains with_root: bool =
  False to retire the _make_tenant_with_root helper pattern duplicated
  across test files. CLAUDE.md convention note added.
- WORKFLOW.md pre-flight read directives reframed: docs are pre-loaded
  as project knowledge, so "surface contradictions" replaces "read end
  to end." DDL reads stay section-scoped.

No new pytest tests; 437 passing unchanged. mypy strict clean.
check_setup 36/36.

Workflow-only commit, no WBS, no impl step. Phase 7 N/A.
```
