# Workflow amendments: Phase 1 questioning, execution-prompt rule, mypy scope, conftest note, verification-by-commit-type

## Scope

One focused commit. Five amendments bundled, all rooted in operator corrections accumulated through Steps 6.15 and 6.14 plus this commit's own drafting (the verification-by-commit-type lesson surfaced mid-prompt):

1. **Phase 1 questioning discipline.** Chat starts questions in business / real-world workflow language, not technical model terms. Translation to technical model happens after business intent is confirmed.
2. **Execution-prompt drafting rule.** Execution prompts addressed to Claude Code describe only what Claude Code does, not operator file-handling steps.
3. **mypy strict scope alignment.** Document the known latent debt (~625 pre-existing errors in `tests/` + `scripts/`) and align prompt verification harness wording with `check_setup.sh`'s actual narrower scope (`src/admin_backend` only), so the discrepancy stops surfacing as a per-step finding.
4. **conftest.py `make_tenant` mixed-style note.** One-line FN-AB-46 so future operators know the ORM main path + raw `text()` `with_root=True` branch asymmetry is deliberate, with a clear future trigger to revisit.
5. **Verification harness must match commit type.** Feature commits get pytest + mypy + check_setup + smoke. Doc-only commits get docs-shape verification (cross-references, markdown structure, em-dash audit). Workflow-only commits with NO code touched skip runtime verification entirely. Copying the feature-step verification block into a doc-only prompt is wasted Claude Code attention and dilutes the signal of "verification clean".

No new WBS, no Phase 0-6 ceremony. Treat as a focused docs commit. Single commit; report-before-commit gate applies.

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 36/36 PASS.
2. Confirm HEAD is `d7456ad` (Step 6.14) on origin/main after the recent push. Tree clean except for the two pre-existing untracked prompt files (`prompts/retro-step-6_10_1-2026-05-15-v2.md`, `prompts/workflow-amendments-2026-05-16.md`) which are also pre-existing state and out of scope for this commit.
3. Confirm FN-AB-45 exists (added in commit `d7456ad`) and FN-AB-46 is the next available number.
4. Confirm `check_setup.sh` currently runs `mypy --strict src/admin_backend` (the narrower scope). Capture the exact line; this amendment aligns prompt language with it.

## Amendments

### Amendment 1: Phase 1 questioning discipline (business language first)

**WORKFLOW.md change.** Under `### Phase 1 - Understand` (line near 253), add a new subsection before the existing question-formulation guidance. Heading: `**Question framing: business first, technical second.**`

Body text:

> Chat asks Phase 1 questions in business / real-world workflow language, not in technical model terms (database rows, Cartesian products, discriminated unions, foreign-key shapes). Translate business reality to technical model only after business intent is confirmed.
>
> Good shape: "Does Marcus need different roles at different locations?" or "When admin disables a module, what should happen to the users who had permissions tied to it?"
>
> Bad shape: "Is this a Cartesian product or a discriminated union?" or "Should the disable endpoint cascade-revoke role assignments?"
>
> The bad shape requires the operator to mentally re-derive the underlying business question from the technical framing, doubling cognitive load and risking the operator picking the technically-tidier answer over the business-correct one. The good shape lets the operator answer from product knowledge; the technical model follows once intent is locked.
>
> When a question genuinely has no good business framing (e.g., asking about HTTP verb choice, error-code shape, or test-fixture organisation), it is fine to ask in technical terms directly. The rule applies to questions about product semantics, not implementation choices that are downstream of confirmed intent.

### Amendment 2: Execution-prompt drafting rule

**WORKFLOW.md change.** Under `## Phase 4 - Prompt` (find the existing prompt-shape conventions), add a subsection: `**Execution prompts: describe only Claude Code's actions.**`

Body text:

> The Phase 4 deliverable is a pair: an implementation prompt file (which Chat drafts in `/mnt/user-data/outputs/`, the operator drops into `prompts/`) AND an execution prompt (a short message the operator pastes into Claude Code to start the run).
>
> The execution prompt MUST describe only what Claude Code does. The operator's file-handling steps are NOT instructions for Claude Code; phrasing like "Drop X into prompts/" misattributes the operator's action and confuses Claude Code about whether the file should already be there.
>
> Bad shape: "Drop `prompts/step-X.md` into the prompts folder, then read it end-to-end. Run the pre-flight, then execute as a single commit."
>
> Good shape: "Read `prompts/step-X.md` end-to-end. Run the pre-flight items and surface any findings before code. Execute as a single commit; pause for authorisation before staging."
>
> The workflow is: Chat drafts the prompt file → operator drops it into `prompts/` → operator pastes the execution prompt into Claude Code. The execution prompt picks up at "Read", not at "Drop".

### Amendment 3: mypy strict scope alignment + latent typing debt note

**WORKFLOW.md change.** Under the verification harness section (around line 727, "Verification harness per commit"), update the mypy line.

Replace existing mypy reference with:

> - `mypy --strict src/admin_backend` (narrower scope; matches `check_setup.sh`). This is the scope tracked as the gating signal for type-check cleanliness.

If the existing text mentions `mypy --strict src/admin_backend tests scripts` (the broader scope), replace it with the narrower scope above and add the following note as a sibling bullet:

> **Note on broader-scope mypy.** Running `mypy --strict` against `tests` and `scripts` reports ~625 pre-existing errors as of `d7456ad`. This is known latent typing debt unrelated to any one step, captured for a future dedicated cleanup commit. Prompt verification harnesses should NOT include `tests scripts` in the mypy scope; doing so surfaces the same baseline noise as a "finding" on every step, which is wasted attention. The `check_setup.sh` narrower scope is the canonical gate.

**CLAUDE.md change.** Under "Code conventions" section, add a one-line entry near existing typing-related notes:

> **Note on mypy strict scope.** `check_setup.sh` runs `mypy --strict src/admin_backend` only (73 source files; gates clean). Broader scopes (`tests`, `scripts`) carry ~625 pre-existing errors as of `d7456ad`; tracked as latent debt for a dedicated cleanup commit. Step-level verification harnesses use the narrower scope to avoid surfacing the same noise per-step.

### Amendment 4: FN-AB-46 conftest.py make_tenant mixed-style note

**CLAUDE.md change.** Add new FN-AB section after FN-AB-45:

```
### FN-AB-46 — conftest.py make_tenant mixed-style (ORM main, raw text() with_root branch)

`tests/integration/conftest.py::make_tenant` was promoted to support `with_root: bool = False` at commit `485d123` (Step 6.15 retro). The main fixture path uses ORM (`session.add()` + `flush` + `refresh`); the `with_root=True` branch uses raw `text()` SQL for the `org_node` insert, mirroring `make_org_node`'s established raw-SQL pattern.

The asymmetry is deliberate: introducing a new ORM mapping path for the OrgNode insert was out of scope for the workflow-only commit at `485d123`. Promoting the OrgNode insert to ORM uniformly across the fixture set is the cleaner long-term shape.

Future trigger to revisit: any next step needing another auxiliary root-style insert (e.g., a fixture variant for nested org-node depth, or a sibling fixture for similar parent-anchored entity creation). At that point, promote the OrgNode insert path to ORM uniformly across `make_tenant`, `make_org_node`, and any future fixture sharing the pattern.

Resolution criterion: arrival of the second use case OR a dedicated test-fixture-cleanup commit. Not urgent.
```

### Amendment 5: Verification harness must match commit type

**WORKFLOW.md change.** Under the verification harness section (where Amendment 3 also lands), add a new subsection: `**Verification scope by commit type.**`

Body text:

> The verification harness in an implementation prompt must match what the commit actually touches. Copying the feature-step verification block into every prompt regardless of commit type is wasted Claude Code attention and dilutes the signal of "verification clean".
>
> **Feature commits (code + tests + docs).** Full harness: `pytest`, `mypy --strict src/admin_backend`, `./scripts/check_setup.sh`, smoke (`smoke_curl.sh` and/or `test_endpoints.sh`). Every check is load-bearing because the commit touches things each check verifies.
>
> **Documentation-only commits (no code, no tests, no schema).** Skip runtime verification. Verify the docs themselves: cross-reference checks (do the amendment-target headings exist), markdown structure (no unclosed fences), convention consistency (FN-AB heading shape, em-dash audit), and that no source file was accidentally touched (`git diff --stat -- '*.py'` returns empty).
>
> **Workflow-only commits (WORKFLOW.md / CLAUDE.md / BUILD_PLAN.md changes).** Same as documentation-only.
>
> **Mixed commits (e.g., fixture changes + workflow docs at 485d123).** Subset of the feature harness limited to what's actually touched. The 485d123 commit ran pytest because fixtures changed; it did not need smoke because no endpoints changed.
>
> The principle: every check in a verification harness should have a plausible failure mode tied to what the commit actually changed. If a check cannot fail because nothing it touches was modified, drop it from the harness.

## File-by-file change list

| File | Change | Lines |
|---|---|---|
| WORKFLOW.md | Amendments 1, 2, 3, 5 (4 sub-sections updated/added) | ~75 net |
| CLAUDE.md | Amendment 3 conventions note + Amendment 4 FN-AB-46 | ~20 net |

No code changes. No new tests. No fixtures touched. Documentation-only commit.

## Verification

This is a documentation-only commit. No code, tests, schemas, or fixtures change. Runtime verification (pytest, mypy, check_setup) adds no signal here and is skipped. Verify the docs themselves:

```
# 1. Cross-reference check: the amendment targets exist at the lines this prompt expects
grep -n "^### Phase 1 - Understand" WORKFLOW.md       # expect a match
grep -n "^## Phase 4 - Prompt" WORKFLOW.md            # expect a match

# 2. FN-AB consistency: new entry follows the existing shape "### FN-AB-N — Title"
grep -c "^### FN-AB-[0-9]\+ — " CLAUDE.md             # expect old count + 1

# 3. Markdown structure: no orphan fences in non-Appendix sections
awk '/^```/ { count++ } END { if (count % 2 != 0) print "UNCLOSED FENCE" }' WORKFLOW.md
awk '/^```/ { count++ } END { if (count % 2 != 0) print "UNCLOSED FENCE" }' CLAUDE.md

# 4. Em-dash audit: only allowed inside ### FN-AB-N — Title headings (existing CLAUDE.md convention)
grep -n "—" WORKFLOW.md                                # expect 0 matches (no em-dashes in WORKFLOW.md ever)
grep -n "—" CLAUDE.md | grep -v "^[0-9]\+:### FN-AB-"  # expect 0 matches outside FN-AB headings
```

If all four checks pass, the docs amendments are well-formed and consistent with existing conventions.

## Surface-and-stop scenarios

1. WORKFLOW.md heading text for `### Phase 1 - Understand` or `## Phase 4 - Prompt` has been edited between this prompt's drafting and execution. Confirm exact heading text via grep before applying the amendment; surface if it has shifted.
2. The verification harness section in WORKFLOW.md does NOT mention mypy at all (i.e., the broader-scope phrasing is absent and amendment 3's "replace" target doesn't exist). In that case, treat amendment 3 as additive: add the narrower-scope mypy line plus the latent-debt note as new content.
3. CLAUDE.md has been edited between drafting and execution such that FN-AB-46 is no longer the next available number. Use whatever the next available number is and surface the change.

## Report shape

```
Workflow amendments commit. Ready to stage.

Files changed:
  WORKFLOW.md +N -M
  CLAUDE.md +N -M

Verification (docs-only):
  Cross-references resolved (Phase 1 / Phase 4 headings located)
  FN-AB-46 follows existing FN-AB heading shape
  Markdown structure: no unclosed fences
  Em-dash audit: clean (FN-AB heading exception preserved)

Amendments applied:
  1. Phase 1 questioning discipline (WORKFLOW.md Phase 1 - Understand)
  2. Execution-prompt drafting rule (WORKFLOW.md Phase 4 - Prompt)
  3. mypy strict scope alignment (WORKFLOW.md verification harness + CLAUDE.md code conventions)
  4. FN-AB-46 conftest.py make_tenant mixed-style note (CLAUDE.md)
  5. Verification harness must match commit type (WORKFLOW.md verification harness)

Surface-and-stop findings: 0 or N per the scenarios above
```

## Suggested commit message

```
docs(workflow): business-first Phase 1, execution-prompt rule, mypy scope, verification-by-commit-type, FN-AB-46

Five amendments rooted in operator corrections accumulated during
Steps 6.15 and 6.14 plus this commit's own drafting:

- Phase 1 questioning discipline: Chat starts in business / real
  world workflow language, not technical model terms. Translation
  to technical model happens after business intent is confirmed.
  WORKFLOW.md Phase 1 - Understand subsection added.
- Execution-prompt drafting rule: execution prompts to Claude Code
  describe only Claude Code's actions, not the operator's file
  handling. WORKFLOW.md Phase 4 - Prompt subsection added.
- mypy strict scope alignment: verification harness language now
  uses the narrower src/admin_backend scope that check_setup.sh
  actually runs. The broader scope (tests + scripts) carries 625
  pre-existing errors as latent debt; tracked separately.
  WORKFLOW.md verification harness + CLAUDE.md code conventions.
- FN-AB-46 conftest.py make_tenant mixed-style: ORM main path +
  raw text() with_root branch is deliberate; promotion to uniform
  ORM is future cleanup work triggered by next similar need.
- Verification harness must match commit type: feature commits get
  pytest + mypy + check_setup + smoke; doc/workflow-only commits
  get docs-shape verification; mixed commits get the subset that
  matches what changed. Every check must have a plausible failure
  mode tied to what the commit actually changed.

No code changes. Documentation-only commit. Cross-references and
markdown structure verified.

Workflow-only commit, no WBS, no impl step. Phase 7 N/A.
```
