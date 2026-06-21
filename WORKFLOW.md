# WORKFLOW.md - Feature & Revision Development Process

This document defines the process for adding a new feature or revising an
existing one in this project. Three actors collaborate: **Human**, **Claude
Chat** (claude.ai, this surface), and **Claude Code** (CC, terminal agent).

Chat reasons and plans. Code executes. Human directs, verifies, decides.

---

## How a Session Starts

This workflow is **opt-in per session**. It engages only when the human
signals intent to follow it, typically by attaching this doc and naming
the step. Outside that signal, Chat behaves normally.

Once engaged, the human can open the work in any natural phrasing that
carries two things: the step number (or that it's a revision of one) and
the feature name. Examples that all work:

- "let's start step 6.10, tenant settings endpoint"
- "starting 6.11 today - audit log read endpoints"
- "I want to revise 6.5, dashboard stats needs a per-region breakdown"
- "step 6.10: tenant settings endpoint" (the original tight form is
  still fine)

Chat infers the step number and feature name, confirms in one line, and
enters **Phase 0**. No preamble, no clarifying ceremony beyond that
confirmation.

If the human's opener is ambiguous (no step number, or unclear whether
it's new vs revision), Chat asks one short question before proceeding.

---

## Outcomes & Success Metrics

### Per-step outcome

A single run of this workflow, end to end, produces:

- A feature deployed and verified in cloud (`vX.Y.Z` revision, smoke
  tests green). Single steps may pause at DONE-LOCAL (Phase 5.5) and
  batch into a shared deploy cycle with subsequent steps; the
  end-state remains cloud-verified.
- `architecture.md` (or a new architecture doc) updated to reflect what
  was actually built - not a stale snapshot, not a draft.
- Git history that tells a clean story: sub-step commits, scoped
  messages, each commit independently revertable.
- A saved implementation prompt under `/prompts/` that future steps can
  learn from.
- A thin step doc that records the audit trail: gaps investigated,
  decisions made, what diverged from plan.
- `CLAUDE.md` updated **only if** a new project-wide convention emerged.
- The CLAUDE.md `### Completed` entry (under `## Current state`)
  for a new step is a 1-2 sentence summary at most: step ID,
  feature name, status with commit or revision tag, and a pointer
  to `docs/implementation-steps/step-X_Y-<name>-YYYY-MM-DD.md`.
  Detailed metrics, LD honour records, surface-and-stop findings,
  FN-AB cross-references, and per-resource regression counts live
  in the step doc, not in CLAUDE.md. Convention rationale lands
  in CLAUDE.md only if it survives the existing "Doc Economy"
  test: applies beyond this step, actionable without further
  context, absence would plausibly cause a future bug. Suggested
  entry format:
  ```
  - **Step X.Y[.Z]: <feature>.** <Status>: DONE-LOCAL at
    `<commit>` (or DONE at `vX.Y.Z`). <one-sentence scope
    summary>. Detail:
    `docs/implementation-steps/step-X_Y-<name>-YYYY-MM-DD.md`.
  ```

### Workflow-level success (qualitative, accumulates over time)

- **Cloud-first-time-success.** Features deploy without surprise
  cloud-only bugs because Phase 2 forced a cloud risk review at design
  time. The `fleet-stats` / `governance-stats` class of bug doesn't
  repeat.
- **Decision recoverability.** Six months later, durable docs alone
  (`architecture.md`, `CLAUDE.md`, `BUILD_PLAN.md`, the step doc) are
  enough to understand what was built and why. No reliance on chat
  history.
- **Cold-start cheapness.** A new chat session opens, reads project
  knowledge, and is productive without long recap. The workflow's
  durable artifacts carry the context.
- **Convention compounding.** `CLAUDE.md` grows slowly and deliberately.
  Each addition reflects a real lesson, not speculation. Future steps
  inherit the wins.
- **Prompt-pattern refinement.** Looking at `/prompts/` over time, a
  reader sees prompts getting tighter, not noisier.
- **Architecture stays canonical.** `architecture.md` is the source of
  truth, not a periodically-refreshed approximation.
- **Scope discipline.** Implementation matches the plan, or the
  divergence is named and recorded - never silent.

### What failure looks like

- Cloud-only bug surfaces post-deploy → Phase 2 cloud risk review failed.
- "Why did we do it that way?" with no answer → Phase 7 capture failed.
- A new step relearns an old lesson → `CLAUDE.md` update was missed.
- Step doc bloated with design content → Phase 2 deliverable went to
  the wrong place; should have updated `architecture.md` directly.
- `/prompts/` has the same ambiguity in three prompts → prompt patterns
  aren't being noticed and codified.

---

## Doc Economy

This workflow deliberately resists creating new docs. Each existing doc
has an owner role; design and convention output is routed to the right
existing doc rather than spawning a new one.

| Doc                            | Owns                                              | Growth rule                              |
|--------------------------------|---------------------------------------------------|------------------------------------------|
| `architecture.md`              | Durable design - what exists and how it fits     | Extend sections; new arch doc only for a genuinely new subsystem |
| `CLAUDE.md`                    | Project-wide conventions for both Chat and Code   | Lean. Add only when a real, reusable rule emerges. No retrospectives, no narratives. |
| `BUILD_PLAN.md`                | Step status, deployed versions, sequencing       | One line per step status change. DONE-LOCAL is valid; DONE requires cloud revision. |
| `step-X_Y-<name>-YYYY-MM-DD.md` | Per-step audit trail: gaps, decisions, retro    | Thin. Pointers, not duplicates of design content. |
| `/prompts/step-X_Y-impl-*.md`  | Saved implementation prompts                      | One per implementation; corpus for pattern learning |
| `WORKFLOW.md`                  | This process itself                               | Stable. Revise only when the process changes. |
| `README.md`                    | Project orientation                               | Rarely touched                           |

**Where design content lands (Phase 2 deliverable):**

- **Extension of an existing subsystem** → update the relevant section
  of `architecture.md`. Default case.
- **Genuinely new subsystem** (rare) → new doc, named for the subsystem,
  linked from `architecture.md`.
- **The step doc is not a design document.** It records *that* the
  design was done and *where it lives*, plus working notes - not the
  design itself.
- **Architecture doc updates land at commit time, not at Phase 2
  exit.** Chat produces the new content as a reviewable draft in
  `/mnt/user-data/outputs/` during Phase 2. The approved draft is
  then folded verbatim into the Phase 4 implementation prompt as a
  named Appendix (e.g., "## Appendix A: architecture_RBAC.md content
  to apply at 6.11.2"). CC applies the appendix verbatim to the
  target doc file as part of the Phase 5 commit. Doc and code land
  in one commit. The standalone `/mnt/user-data/outputs/` draft is
  deleted at Phase 4 close — single source of truth is the appendix
  in the impl prompt. Rationale: prevents doc/code drift during
  Phases 3–5; the doc is never in repo describing un-shipped
  behaviour.

**CLAUDE.md discipline:** an addition only earns its place if (a) it
applies beyond this one step, (b) it's actionable - a Chat or Code
agent could follow it without further context, and (c) its absence
would plausibly cause a future bug. Retrospective prose does not belong
here.

---

## Response Calibration

The workflow fails when Chat swings between exhaustive prose and
single-liner terseness without reading the situation. This section
pins down response shape per phase and across phases.

### Cross-phase defaults

- **Lead with the answer.** No "let me think", no preamble, no
  paraphrasing the question back.
- **ASCII before prose.** Diagrams carry the substance; prose is
  caption.
- **Match length to question.** Factual question → sentence. Design
  question → paragraph. Multi-part decision → compact bullets.
- **No manufactured concerns.** If the cloud risk review or a stress
  test finds no real issue, say so plainly. Do not invent risks to
  appear thorough.
- **Prefer trace over explanation.** In triage and verification, cite
  file:line or command output. "Verified: X" beats a confident
  narrative.
- **Distinguish verified from recalled.** "I verified this just now" vs
  "I recall from earlier context" are different claims; the second
  needs verification before being treated as fact.
- **Pause-and-ask at phase transitions.** Chat does not advance phases
  silently. If an exit criterion is unmet, name what's missing and
  stop.
- **Acknowledge corrections silently.** No "you're right, let me try
  again". Just give the corrected response.

### Per-phase response shape

| Phase                 | Default response shape                                                                 |
|-----------------------|----------------------------------------------------------------------------------------|
| 0 Pre-flight          | One-sentence scope confirmation. No preamble.                                          |
| 1 Understand          | Questions one at a time, `Q1/X` numbering. Max one short reason per question if not obvious. Mental model restated in ≤3 sentences before exit. |
| 2 Design              | ASCII first, prose as caption. Cloud risk review is a checklist walk, one line per item. If an item is N/A, say "N/A". |
| 3 Plan                | Numbered list of sub-steps. No narrative wrapper.                                      |
| 4 Prompt              | The implementation prompt specs behavior, not implementation. The chat dialogue around it stays terse. Show the prompt; do not explain it. |
| 5 Execute / 6 Verify  | Log triage mode. Cite file:line or command output. Format risks as `Risk: X. Mitigation: Y.` - one line each. |
| 7 Capture             | Retro in 2 to 3 bullets. CLAUDE.md proposals as one-sentence rules.                    |

### When in doubt

If a question has multiple plausible interpretations, pick the most
likely one, state the assumption in one sentence, and answer. Enumerate
alternatives only if the human pushes back or asks explicitly.

---

## Phase Map

```
┌──────────────────────────────────────────────────────────┐
│ 0. PRE-FLIGHT     scope confirmed vs BUILD_PLAN.md       │
├──────────────────────────────────────────────────────────┤
│ 1. UNDERSTAND     mental model of the feature            │
│    └─ optional:   investigation prompt for CC            │
│    └─ required:   DDL facts from current_schema.sql      │
├──────────────────────────────────────────────────────────┤
│ 2. DESIGN         ASCII first, prose second              │
│                   mandatory checklist:                   │
│                     ✓ data model deltas                  │
│                     ✓ API surface                        │
│                     ✓ test scope (local + cloud)         │
│                     ✓ cloud risk review                  │
│                     ✓ design stress test (close gate)    │
├──────────────────────────────────────────────────────────┤
│ 3. PLAN           WBS with commit boundaries             │
│                   (default: single commit)               │
├──────────────────────────────────────────────────────────┤
│ 4. PROMPT         implementation prompt + exec prompt    │
│                   + prompt stress test (close gate)      │
├──────────────────────────────────────────────────────────┤
│ 5. EXECUTE (CC)   one sub-step → test → commit, loop    │
│                   commit incl. step doc Plan + Retro,    │
│                   CLAUDE.md updates, BUILD_PLAN flip,    │
│                   WORKFLOW.md amendments (A6)            │
├──────────────────────────────────────────────────────────┤
│ 5.5 LOCAL PAUSE   optional: operator may queue more     │
│                   DONE-LOCAL steps before Phase 6        │
├──────────────────────────────────────────────────────────┤
│ 6. VERIFY         local smoke → 12-step deploy → cloud   │
│                   (may verify N steps in one cycle)      │
├──────────────────────────────────────────────────────────┤
│ 7. CAPTURE        CONDITIONAL — post-deploy lessons      │
│                   only (cloud-emergent surprises). Most  │
│                   steps skip Phase 7 entirely (A6).     │
└──────────────────────────────────────────────────────────┘
```

### Escape Hatches (human can invoke any time)

| Phrase                | Effect                                                |
|-----------------------|-------------------------------------------------------|
| `Investigate: <q>`    | Chat writes a read-only CC prompt for fact-finding    |
| `Pause`               | Chat summarizes current state and stops               |
| `Re-scope`            | Step is too big; split into sub-steps and renumber    |
| `Back to phase N`     | Explicit return to an earlier phase                   |
| `Defer`               | Park the open question; Chat notes and proceeds       |

---

## Phase Contracts

Each phase has an entry, work, and exit. Chat does not skip exit criteria
and does not advance phases silently - it states the phase transition.

### Phase 0 - Pre-flight

**Entry:** session trigger received.
**Work:** Chat confirms step number against `BUILD_PLAN.md`, checks
prerequisites are merged, and asks human if this is **new** or **revision**.
For a revision, Chat reads the existing step doc.
**Exit:** one-line scope statement agreed by human.
**Deliverable:** none yet; scope held in chat context.

### Phase 1 - Understand

**Entry:** scope statement agreed.
**Work:**
1. Human describes feature, domain reality, and user interaction paths.
2. Chat asks ≤5 targeted exploratory questions. No exhaustive checklists,
   no justifying every question. If a question's purpose is not obvious,
   one short reason is fine.
3. Human answers, says "not important", or defers.
4. Chat builds the mental model and lists **remaining gaps**.
5. Gaps requiring code knowledge → Chat writes an **investigation-only**
   prompt for CC. Human runs it in CC, pastes the report back.

**Question framing: business first, technical second.**

Chat asks Phase 1 questions in business / real-world workflow language, not in technical model terms (database rows, Cartesian products, discriminated unions, foreign-key shapes). Translate business reality to technical model only after business intent is confirmed.

Good shape: "Does Marcus need different roles at different locations?" or "When admin disables a module, what should happen to the users who had permissions tied to it?"

Bad shape: "Is this a Cartesian product or a discriminated union?" or "Should the disable endpoint cascade-revoke role assignments?"

The bad shape requires the operator to mentally re-derive the underlying business question from the technical framing, doubling cognitive load and risking the operator picking the technically-tidier answer over the business-correct one. The good shape lets the operator answer from product knowledge; the technical model follows once intent is locked.

When a question genuinely has no good business framing (e.g., asking about HTTP verb choice, error-code shape, or test-fixture organisation), it is fine to ask in technical terms directly. The rule applies to questions about product semantics, not implementation choices that are downstream of confirmed intent.

#### DDL fact-gathering — read live, write down

Before declaring Phase 1 complete, for every table this step writes to
(or has constraints against), Chat reads the full table definition in
`docs/schema/current_schema.sql` and captures the constraining facts as
a bulleted sub-section in the step doc's Mental Model.

Required reads per touched table:

- **Column types and defaults** - including enum-typed columns and
  their default values.
- **CHECK constraints** - actual text, not remembered. CHECK
  constraints often encode invariants the app must satisfy on every
  write (pair-consistency, value-range, format).
- **UNIQUE constraints** - exact column set; a missing UNIQUE forces
  app-layer enforcement.
- **FK destinations and cascade behaviour** - including the
  audit-actor pattern: Pattern (a) typed FK to a single users table,
  or Pattern (b) UUID + `actor_user_type_enum` no-FK shape. This
  determines whether multi-audience write endpoints are even
  possible.
- **Indexes** - primary, unique, and supporting indexes the planner
  uses.
- **RLS policies** - whether enabled, force-enabled, and what the
  `USING` and `WITH CHECK` clauses say.
- **Triggers** - `set_updated_at`, audience-enforcement, cascade
  triggers.

Authoritative source: `docs/schema/current_schema.sql`. Mirrored from
production via `pg_dump`; live truth. Read sectionally — `grep` for a
column name misses constraints. Read the full `CREATE TABLE` block plus
the constraints / FK / index / trigger blocks for each touched table.

Out of scope for design reads: `db/raw_ddl/*.sql`. Those are historical
authoring records and drift from live schema as migrations land.

Output goes in the step doc's Mental Model:

```
## DDL facts (Phase 1 — verified against current_schema.sql)

### core.tenants
- Status column: tenant_status_enum, default 'ONBOARDING'
- CHECK ck_tenants_suspended_consistency: SUSPENDED ⇄
  suspended_at NOT NULL AND suspended_by_user_id NOT NULL
- CHECK ck_tenants_number_of_stores_as_of_consistency:
  number_of_stores NOT NULL ⇄ number_of_stores_as_of_date NOT NULL
- FK created_by_user_id → platform_users(id) — Pattern (a)
- No UNIQUE on name (app-layer enforcement required)
- RLS: tenants_tenant_isolation; PLATFORM bypass via
  app.user_type = 'PLATFORM'

### core.tenant_module_access
...
```

These facts feed Phase 2 design directly. A design decision that
contradicts a DDL fact is a STOP signal (route back to Phase 1, or
rescope so the contradiction doesn't matter).

**Exit:** Chat restates the feature in its own words; human confirms; all
gaps resolved or explicitly deferred; DDL facts captured in step doc.
**Deliverable:** `step-X_Y-<name>-YYYY-MM-DD.md` created at repo root
(following existing convention) with a *short* `Mental Model` section:
one paragraph of feature description, bullet list of in-scope behaviors,
explicit non-goals, deferred questions, plus a `DDL facts` sub-section.
This is a meta-record, not a spec.

### Phase 2 - Design

**Entry:** mental model confirmed; DDL facts captured.
**Work:** Chat leads with ASCII diagrams (data model, API surface,
sequence flows). Prose is commentary, not the substance - show, then tell.

Before drafting, Chat names the **design destination**:

- *Default:* a specific section of `architecture.md` to extend or revise.
  Chat states which section by heading.
- *Rare:* a new architecture doc for a genuinely new subsystem. Requires
  human confirmation that the new doc is justified.

The design **must** explicitly address all four:

- **Data model deltas** - new tables, columns, constraints, indexes,
  schema (`core.`, `ops.`, etc.), Alembic migration shape.
- **API surface** - endpoints, request/response schemas, error codes,
  visibility scope (platform vs tenant, RBAC).
- **Test scope** - what gets tested locally, what gets tested in the
  cloud smoke suite, what regressions to add.
- **Cloud risk review** - at minimum, walk through:
  - Raw SQL → is every table schema-qualified?
  - Alembic migration → safe to run via Cloud Run Job? reversible?
  - Env vars → any new ones needed? `--update-env-vars` ready?
  - IAM / Cloud SQL role → does the app role have the perms needed?
  - Search path or other implicit-context dependencies?
  - Image build → any new system deps, new pip packages?

Back-and-forth with human until human confirms design.

#### Phase 2 close — design stress test

Before declaring Phase 2 closed, Chat runs a design stress test against
the DDL facts captured in Phase 1 + the live permissions catalogue.

Stress-test checklist:

- **Catalogue coverage** - every gate tuple in the design (module ×
  resource × action × scope) has a corresponding row in
  `core.permissions`, and the role-grants in `core.role_permissions`
  match the audience the design assumes. If a tuple is missing from
  seed, design either bundles a seed update or descopes the endpoint
  that needs it.
- **DDL satisfaction** - every INSERT/UPDATE path in the design
  satisfies the relevant CHECK / FK / UNIQUE constraints from Phase 1.
  Particular attention: audit-actor FK pattern (Pattern (a) vs (b)) -
  if a TENANT caller would write to a Pattern (a) FK column, the
  design has a bug; revert scope or bundle a migration.
- **Error class taxonomy** - every error class the design needs has a
  clear code (UPPER_SNAKE), HTTP status, public message, and (where
  relevant) details shape. No "we'll figure out the envelope later"
  entries.
- **Locked-decision owners** - each locked decision names the artifact
  that enforces it: a model class, a schema, a handler, a repo
  method, a test. A decision without an owner is a decision that
  drifts.
- **Scope-out triggers** - each scope-out has an explicit future
  trigger: an FN-AB number, a dependent step, a post-X marker.
  "We'll deal with later" is not a trigger.
- **Cross-cutting check** - any architectural convention the step
  introduces (audience kwarg, new error pattern, new cleanup-fixture
  order) has either a CLAUDE.md addition planned, or an explicit
  "deferred until N-th example confirms" note.

Each item passes or surfaces. A failed item routes back to design
discussion — STOP, don't advance to Phase 3.

This stress test is separate from the Phase 4 prompt stress test.
Phase 2 stress-tests the design; Phase 4 stress-tests the prompt
artifact.

**Exit:** human says "design ok" (or equivalent), and the design content
has been written as an approved draft in `/mnt/user-data/outputs/` with
insertion markers naming its destination doc and section. The
architecture doc is the canonical design artifact; the draft is the
approved-but-not-yet-committed form of that artifact. Physical insertion
into the repo happens at the Phase 5 commit boundary, bundled with the
code commit (via the impl prompt's Appendix - see Phase 4).
**Deliverable:** approved draft file at
`/mnt/user-data/outputs/step-X_Y-architecture-draft-YYYY-MM-DD.md`;
step doc gains a one-line `Design` section pointing to the destination
doc section by name and noting "draft pending bundled commit at Phase 5".

### Phase 3 - Plan

**Entry:** design confirmed.
**Work:** Chat proposes the WBS. Each sub-step:

- has a clear scope (one concern)
- is independently testable
- ends at a clean git commit boundary
- is revertable in isolation

Human confirms or proposes alternatives. Agree on the WBS.

#### WBS — default to single commit

Default WBS for a step is a single commit. The whole step ships as
one commit; Phase 5 report and approval happen once.

WBS into multiple commits only when one of these holds:

- **Cloud-side operator action is forced between commits.** E.g., a
  seed update must land in Cloud SQL before the next commit's tests
  can pass.
- **A parallel step is blocked on partial completion.** Another
  developer needs the foundations half merged to start their work.
- **The foundations half exceeds ~500 lines** and the operator wants
  a separate review boundary for it.

If none of these hold, single commit. Multiple commits add an
authorize-pause-report cycle per commit for diminishing benefit.

**Exit:** human confirms WBS.
**Deliverable:** `Implementation Plan` section in step doc.

### Phase 4 - Prompt

**Entry:** WBS confirmed.
**Work:** Chat writes two artifacts.

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
2. **Execution prompt** - the short message pasted into CC. Default form:

   > Read `/prompts/step-X_Y-impl-YYYY-MM-DD.md` and execute the WBS,
   > pausing for confirmation at each commit boundary.

**Execution prompts: describe only Claude Code's actions.**

The Phase 4 deliverable is a pair: an implementation prompt file (which Chat drafts in `/mnt/user-data/outputs/`, the operator drops into `prompts/`) AND an execution prompt (a short message the operator pastes into Claude Code to start the run).

The execution prompt MUST describe only what Claude Code does. The operator's file-handling steps are NOT instructions for Claude Code; phrasing like "Drop X into prompts/" misattributes the operator's action and confuses Claude Code about whether the file should already be there.

Bad shape: "Drop `prompts/step-X.md` into the prompts folder, then read it end-to-end. Run the pre-flight, then execute as a single commit."

Good shape: "Read `prompts/step-X.md` end-to-end. Run the pre-flight items and surface any findings before code. Execute as a single commit; pause for authorisation before staging."

The workflow is: Chat drafts the prompt file → operator drops it into `prompts/` → operator pastes the execution prompt into Claude Code. The execution prompt picks up at "Read", not at "Drop".

#### Draft-time "already built?" check

Before any new symbol is added to the impl prompt's change list —
function, class, type alias, fixture, error class, helper — grep
the codebase to confirm it doesn't already exist:

For each candidate new symbol:
```
grep -rn "def <symbol>\|class <symbol>\|<symbol>\s*=" src/ tests/
```

If found:
- Change "NEW" → "REUSE" in the change list.
- If the prompt assumes a different signature than what exists,
  document the signature mismatch and decide:
  - (a) use the existing signature (preferred — minimizes change)
  - (b) extend the existing function (only if (a) is structurally
    impossible)
  - (c) introduce a new sibling with a different name (only as
    last resort; surface the rationale)

This is a Phase 4 draft-time discipline, NOT a Phase 5 pre-flight
discipline. The pre-flight catches it as a slow path (surfaces
mid-execution); draft-time catches it pre-pre-flight (zero
operator round-trip). The Phase 4-close adversarial-readback pass
is the second-pass net that catches anything missed here.

Findings F1 + F2 at Step 6.10.1 (get_tenant_user_anchor and
TenantUserNotFoundError both already existed; both surfaced at
CC's pre-flight) are the canonical examples this check prevents.

#### Prompt content discipline — spec what, not how

The impl prompt names interfaces, behaviors, error shapes, lifecycle
rules, locked decisions, DDL facts, test coverage, and acceptance
criteria. It does NOT contain full implementations.

Code in the prompt is justified ONLY when:

- **Exact code text is the spec.** Error class code strings, HTTP
  status integers, error class declarations where the 3-5 lines of
  attribute overrides ARE the specification.
- **Literal text lands in a document.** Architecture appendix
  content, OpenAPI summary text, doc snippets that get pasted
  verbatim into a docs/ file.
- **API contract examples.** Short JSON request/response examples
  (≤10 lines each) that clarify the wire shape.
- **Function/method signatures.** The signature IS the interface
  contract. Bodies do NOT belong in the prompt.
- **Pydantic field lists.** The field declaration list IS the schema
  spec - names, types, optional/required, validators by reference
  (not body).

Code that does NOT belong in the prompt:

- SQL bodies — replace with behavior spec ("SELECT-then-INSERT in
  one transaction; populate audit columns from actor_user_id")
- Handler bodies — replace with behavior spec ("on EmptyPatchError
  raise 422; on repo None raise TenantNotFoundError")
- Validator logic bodies — replace with behavior spec ("force-include
  ADMIN; dedupe preserving order")
- Repository method bodies — replace with method signature + behavior
  spec + acceptance test list
- Test method bodies — Code writes its own tests from the test
  catalogue (test names + what each verifies)

If the prompt drafter (Chat) catches itself writing a 50-line SQL
sketch, that is a smell. Replace with a 10-line behavior spec.

#### Quantified gate — code volume in impl prompt

To make the discipline above measurable and not rationalizable, the
impl prompt is subject to a hard code-volume cap.

**The gate**:

```
non_appendix_code_lines ≤ MIN(
    0.20 × (total_prompt_lines − appendix_lines),
    250
)
```

**Calibration history**:
- Initial cap at 30%/400 (set at Step 6.10.1 design time)
- 6.10.1 ran at 37% utilization (129 measured / 347 cap on
  fixed prompt). Loose by ~2x.
- Tightening to 20%/250 still passes 6.10.1 at 52% utilization.
- Next data point (6.10.2 or 6.12) will inform whether to
  tighten further or hold.

The gate is a tool to prevent prompt bloat. Loose calibration
defeats the purpose; tight calibration without data can reject
legitimate prompts. Tighten incrementally based on per-step
measurements.

Where:

- **total_prompt_lines** = `wc -l` of the impl prompt file
- **appendix_lines** = lines in the architecture-doc Appendix section
  (the "## Appendix A:" block onward)
- **non_appendix_code_lines** = sum of lines inside fenced code blocks
  outside the Appendix A section, minus exempted lines

**Exempted lines** (subtracted from the code-lines count):

- Function/method signatures (the `def foo(...)` line and parameter
  continuation lines, but NOT the body)
- Error class declarations (`class Foo(ClientError):` header plus
  3-5 lines of attribute overrides)
- Pydantic field declaration blocks (the `class Schema` line + the
  `field_name: Type = default` lines, but NOT validator bodies)
- API JSON request/response examples ≤10 lines each
- Bash/SQL one-liners in pre-flight or verification harness (commands
  operator/CC runs to verify state, not "code being specified for
  Code to write")

**What counts** (NOT exempted):

- Repository method bodies
- Handler bodies
- Validator logic bodies
- SQL queries longer than 5 lines
- Test method bodies
- Long inline comments inside code blocks

**Iteration:**

- If every step passes well under cap → tighten next iteration
  (15% / 200 lines; eventually 10% / 150 if behavior holds).
- If steps consistently scrape the ceiling → either the exemption
  list is too narrow, or the cap is genuinely too tight for the
  work shape; tune one or the other.
- If a step fails the gate → the prompt has code sprawl; refactor
  before Phase 5. The refactor itself is the value, not the number.

Each step captures the measurement in its Phase 7 retro so
calibration data accumulates.

**Why this gate**: Chat is the prompt drafter; Code is the
implementer. Code in the prompt is duplication of effort if Code
will rewrite it anyway (because the prompt drafter lacks live
access to the codebase). Behavior spec + acceptance gives Code a
direct path to the right implementation using live conventions; a
sketch gives Code a starting point to discard, then write the
real version.

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

#### Architecture doc updates — framing-only Appendix, code composed fresh

When a step requires updates to architecture docs (`architecture.md`,
`architecture_RBAC.md`, or similar):

- **Phase 2 close**: Chat drafts the **architectural framing
  prose** for the doc update: design intent, divergence notes,
  transition matrices, lifecycle diagrams, what-this-demonstrates
  summaries. Captured as a review artifact in
  `/mnt/user-data/outputs/`.
- **Phase 4 draft**: the approved framing prose is folded into
  the impl prompt as Appendix A. **The code body of the worked
  example is NOT included verbatim**; the implementer composes
  it from the shipped implementation at apply time.
- **Phase 5 implementation**: the implementer applies the
  Appendix A framing prose to the target doc file and composes
  any accompanying code block fresh against the live code.
  Surface composed code in the final report for operator review.
- **The appendix remains excluded from the code-volume gate**
  because it is literal doc text, not implementation code.

The Phase 2 review artifact in `/mnt/user-data/outputs/` is
**deleted** at Phase 4 close — the source of truth is the appendix
in the impl prompt, not the standalone draft. This prevents
draft-vs-appendix drift between Phase 2 close and Phase 5 commit.

Doc updates land at commit time with the code that implements the
documented behavior. Never commit an architecture doc update ahead
of the code it describes (see Anti-patterns).

Rationale: verbatim code in Appendix A round-trips through the
wire three times (prompt input, Edit old_string of surrounding
text, Edit new_string), with the code body itself adding zero
design value beyond what live code already conveys. Framing prose
(URL convention rationale, cascade semantics commentary, divergence
notes vs precedents) is what Chat-side design adds; that stays in
Appendix A.

#### Pre-flight discipline — surface contradictions, not re-read

Every impl prompt MUST include a pre-flight directive making the
implementer aware of the project context docs. These carry
load-bearing conventions and current-state assertions the step's
changes must integrate with. The standing list, in order:

- **CLAUDE.md**: codified conventions (D-N decisions), open
  FN-AB forward notes, `### Completed` entries.
- **BUILD_PLAN.md**: confirm step structure (where this step
  sits, what's pending, what's just landed).
- **docs/architecture.md**: error envelope shape, RLS posture,
  AuthContext / JWT structure, anchor dependency mechanics.
- **docs/architecture_RBAC.md**: Two-layer gate semantics,
  audience parameter, worked-example format.

These docs are pre-loaded into the implementer's session as
project knowledge. The pre-flight directive should NOT read "read
CLAUDE.md end-to-end" (effectively a no-op for token spend, since
the docs are already in context). The load-bearing behavior is:
**surface any contradiction between this prompt and any of these
docs before proceeding; do not silently work around. The docs
usually win.**

For DDL spot-checks, `docs/schema/current_schema.sql` is large
(~1500 lines) and NOT typically pre-loaded. The pre-flight
directive for DDL reads should be **section-scoped**, not
end-to-end: read the `CREATE TABLE core.<table>` block plus its
constraint/FK/index/trigger/policy blocks for each touched table.
Example: `grep -A 60 "CREATE TABLE core.tenant_module_access"
docs/schema/current_schema.sql` plus separate greps for the
table's constraint and policy names.

Why standing: at Step 6.10.1 this discipline caught three of four
substantive surface-and-stop findings (F1, F2, F4). The Q7
envelope lock (F3) was caught by the same read; a re-read at draft
time would have caught it pre-execution (see adversarial-readback
sub-section in the Phase 4 close stress test). Step 6.15's
token-budget post-mortem found that the "end-to-end" wording was
the wrong framing — the docs ride into the session free; the
load-bearing behavior is contradiction-surfacing.

#### Phase 4 close — prompt stress test

After the impl prompt is drafted, Chat runs a stress test before
declaring Phase 4 closed. This is mechanical (measurable, not vibes)
and structural (does the prompt have the right sections).

**Measurement — code-volume gate:**

```bash
total_prompt_lines=$(wc -l < <prompt-file>)
appendix_start=$(grep -n '^## Appendix' <prompt-file> | head -1 | cut -d: -f1)
appendix_lines=$((total_prompt_lines - appendix_start + 1))
non_appendix=$((total_prompt_lines - appendix_lines))
cap=$(python3 -c "print(min(int(0.20 * $non_appendix), 250))")
```

Chat computes the actual `non_appendix_code_lines` (minus exemptions)
and compares to `cap`. Over → refactor before Phase 5. Surface the
measurement in the Phase 4 close report:

```
Prompt code-volume gate:
  Total lines:                  1200
  Appendix lines:                250
  Non-appendix lines:            950
  Cap (MIN(20%, 250)):           190
  Measured code (post-exempt):   270
  Gate: PASS
```

**Structure conformance — checklist:**

- Standing discipline section (code sketches, missing files,
  documentation writing, commit shape)
- Step ID + Scope in + Scope out + deferred-items-with-triggers
- Locked decisions numbered, each with named owner
- Pre-flight items including DDL spot-check against Phase 1 DDL facts
- File-by-file change list per commit with NEW / MODIFY / REGEN marker
- Verification harness per commit (pytest + mypy + check_setup +
  EXPLAIN ANALYZE on new query patterns)
- Per-resource regression checkpoint with baseline-capture in
  pre-flight
- Testing and regression discipline section with three sub-sections
  (load-bearing, deliberately not added, regression risk surface)
- Surface-and-stop scenarios numbered, #0 reserved for missing files
- Report shape per commit
- Coordination section (unblocks + post-step + future references)
- Appendix A if architecture doc updates are bundled

Failing items get fixed; the prompt doesn't ship to Phase 5 with
structural gaps.

**Sanity check — design contradictions:**

- Every gate tuple in the impl prompt traces back to a Phase 1
  catalogue check.
- Every INSERT/UPDATE path traces back to a Phase 1 DDL fact (CHECK
  / FK / UNIQUE compliance).
- Every error class declaration matches the Phase 2 error taxonomy.
- No new locked decisions appear in the prompt that weren't in the
  Phase 2 design.

A contradiction is a STOP — fix in the prompt OR route back to Phase
2 to update the design.

**Verification harness: scope and contents.**

The bullet in the structure-conformance checklist above ("Verification harness per commit") is intentionally generic. Two related disciplines govern what goes into a specific commit's harness: mypy scope, and whether runtime verification applies at all.

*Mypy scope.* The canonical mypy gate is `mypy --strict src/admin_backend` (narrower scope; matches what `scripts/check_setup.sh` actually runs). This is the scope tracked as the gating signal for type-check cleanliness.

**Note on broader-scope mypy.** Running `mypy --strict` against `tests` and `scripts` reports ~625 pre-existing errors as of commit `d7456ad`. This is known latent typing debt unrelated to any one step, captured for a future dedicated cleanup commit. Prompt verification harnesses should NOT include `tests scripts` in the mypy scope; doing so surfaces the same baseline noise as a "finding" on every step, which is wasted attention. The `check_setup.sh` narrower scope is the canonical gate.

**Verification scope by commit type.**

The verification harness in an implementation prompt must match what the commit actually touches. Copying the feature-step verification block into every prompt regardless of commit type is wasted Claude Code attention and dilutes the signal of "verification clean".

**Feature commits (code + tests + docs).** Full harness: `pytest`, `mypy --strict src/admin_backend`, `./scripts/check_setup.sh`, smoke (`smoke_curl.sh` and/or `test_endpoints.sh`). Every check is load-bearing because the commit touches things each check verifies.

**Documentation-only commits (no code, no tests, no schema).** Skip runtime verification. Verify the docs themselves: cross-reference checks (do the amendment-target headings exist), markdown structure (no unclosed fences), convention consistency (FN-AB heading shape, em-dash audit), and that no source file was accidentally touched (`git diff --stat -- '*.py'` returns empty).

**Workflow-only commits (WORKFLOW.md / CLAUDE.md / BUILD_PLAN.md changes).** Same as documentation-only.

**Mixed commits (e.g., fixture changes + workflow docs at 485d123).** Subset of the feature harness limited to what's actually touched. The 485d123 commit ran pytest because fixtures changed; it did not need smoke because no endpoints changed.

The principle: every check in a verification harness should have a plausible failure mode tied to what the commit actually changed. If a check cannot fail because nothing it touches was modified, drop it from the harness.

**Adversarial readback:**

Read the prompt as if you were CC (the implementer), looking for
the failure modes that have surfaced in prior steps:

1. **Function/class names that might already exist.** For every new
   symbol added to the change list (function, class, type alias,
   fixture, error class), grep the codebase:
   ```
   grep -rn "def <symbol>\|class <symbol>\|<symbol> =" src/ tests/
   ```
   If found: change "NEW" to "REUSE" or "MODIFY EXISTING" in the
   change list. Document the signature mismatch if the prompt
   assumes a different shape than what exists. (See "Draft-time
   'already built?' check" — this is the draft-time discipline that
   the check codifies; adversarial readback is the second-pass net.)

2. **New contracts vs existing locks.** For every new contract this
   prompt introduces — new error class, new response field, new
   envelope shape, new wire format — grep for existing locks that
   govern it:
   ```
   grep -rn "details\|envelope\|response.*shape" docs/ CLAUDE.md
   grep -n "^### Q[0-9]\|D-[0-9]" CLAUDE.md
   ```
   If a Q-locked decision or D-N convention governs the area,
   verify the new contract conforms. Q7 envelope lock missed at
   Step 6.10.1 draft is the canonical recurrence to prevent.

3. **Test count drift.** If the prompt mentions a test count
   anywhere (e.g., "~33 tests"), search the prompt for all
   mentions and enumerate against the actual catalogue. Drift
   between "summary number" and "enumerated catalogue" surfaces as
   test bloat or test gaps at execution.

4. **Worked example fidelity.** Every method call in a worked
   example must trace to either an existing function in the
   codebase OR a function specified in this prompt's change list.
   Invented helpers (e.g., `.from_row(user_row)` at Step 6.10.1
   draft) surface as CC questions.

5. **Order-of-checks bugs in Appendix A.** FastAPI dependency
   resolution order is: middleware → path params → body + Pydantic
   → custom Depends() chain (auth, session, anchor_dep, gate) →
   handler body. Any worked example showing body validation AFTER
   gate is wrong (recurrence class; D3 at 6.11.2 and F10 at
   6.10.1).

6. **Cleanup fixture names.** Don't name a fixture the operator
   won't immediately recognize. Describe what the fixture does;
   let CC pick the name per project convention. Naming an
   invented fixture surfaces as a pre-flight stop.

Outcome: produce a numbered list of findings (F1, F2, …). For
each: severity (major / medium / minor), proposed fix, and
decision (apply / downgrade to surface-and-stop / informational
only). Apply major findings before declaring Phase 4 exit.

Stress test passes if (a) all four sub-sections pass and (b) zero
major findings remain unfixed.

Phase 4 closes only when all four sub-sections pass: measurement,
structure, sanity, adversarial readback.

**Exit:** human saves the implementation prompt to `/prompts/`; the
Phase 2 outputs-directory draft is deleted (its content is now in the
impl prompt's Appendix).
**Deliverable:** `/prompts/step-X_Y-impl-YYYY-MM-DD.md` and an inline
execution prompt in chat for the human to copy.

### Phase 5 - Execute (Claude Code)

**Entry:** prompt saved.
**Work:** Human pastes the execution prompt into CC. CC works one
sub-step at a time. After each sub-step (or for single-commit WBS,
after the whole step):

- CC runs local tests
- Human inspects output
- CC commits with the agreed message
- Surprises, blockers, or scope shifts → human pastes CC output back to
  Chat for advice

If scope shifts materially, return to **Phase 2** or **Phase 3**. Do not
let scope drift silently inside execution.

**Exit:** all sub-steps merged locally; full local test suite green.
**Deliverable:** code commits on the working branch.

The step is now **DONE-LOCAL**. Operator chooses between Phase 5.5
(pause and queue another step) or Phase 6 (deploy now).

#### Phase 5 commit scope — retro-derived edits included (A6)

Starting with the step following Step 6.10.1, the Phase 5 staging
commit includes retro-derived doc edits by default. Specifically:

- **Step doc Plan + Retro sections filled** from staging reality.
  (Previously: filled separately at Phase 7.)
- **CLAUDE.md convention additions** if any surfaced during the step.
  (Previously: appended at Phase 7.)
- **WORKFLOW.md amendments** if any accumulated during the step.
  (Previously: applied at Phase 7.)
- **BUILD_PLAN.md** status flip to DONE-LOCAL + new sub-step entries
  if the step split into sub-steps.
- **Forward notes** (FN-AB-NN) appended to CLAUDE.md.

All of the above land in the same Phase 5 commit as the code,
tests, and smoke. The commit message has a structure that
separates code-change context from doc-change context for
readability:

```
Step X.Y.Z: <feature> + tests + smoke + docs + retro

<code/feature description>

<doc updates summary — architecture_RBAC.md, endpoints/, CLAUDE.md,
 BUILD_PLAN.md, WORKFLOW.md (if amended)>

<retro highlights — what worked, lessons captured>
```

**Why A6**: at Step 6.10.1, all five WORKFLOW.md amendments
accumulated during the step were derivable by Phase 5 staging.
Phase 7 added zero new lessons beyond the meta-observation that
A6 is correct. Folding into Phase 5 saves a commit, prevents the
"did I forget the retro?" drift, and makes the audit trail one
commit per step in the common case.

**Exception**: if cloud-emergent lessons surface during Phase 6
(deploy surprise, IAM gotcha, production performance vs EXPLAIN
ANALYZE divergence, smoke surface bug), those land in a
post-Phase-6 conditional Phase 7 pass — see Phase 7 below.

**Steps that bypass A6**: a step with NO retro-derived edits to
record (no new CLAUDE.md conventions, no WORKFLOW.md amendments,
nothing surfaced) can ship Phase 5 with the standard Plan + Retro
content in the step doc and nothing else. The "Retro" section is
allowed to be brief — three bullets noting "uneventful;
locked-decision honor record clean; no new conventions" is a
valid retro for a routine step.

#### Operator-facing response to CC

When the workflow produces text that the operator pastes into
Claude Code (CC) — typically responses to CC's pre-flight reports,
staging reports, or surface-and-stop questions — that text MUST
be presented in a single-click copy box via the `message_compose_v1`
tool. NOT inline prose.

Why: operator-to-CC copy-paste is friction-sensitive. Inline
prose requires text selection then copy; a copy box has a button.
At Step 6.10.1, the operator surfaced this friction explicitly.

How:

```
message_compose_v1(
  kind="other",
  summary_title="Response to CC — <action>",
  variants=[{
    "label": "<one-line label, e.g. 'Authorize commit'>",
    "body": "<the full response text>"
  }]
)
```

Single variant unless multiple strategic approaches are genuinely
on the table (rare; most responses are "yes proceed" with
acknowledgments).

Exception: one-line responses ("yes", "stop", "wait") can stay
inline. The copy-box overhead isn't worth it for under ~50 chars.

Apply: any time response text > 50 chars is destined for CC, wrap
in message_compose_v1.

#### Bug-fix commits — codify and prevent

When a commit's sole purpose is fixing a cloud-emergent bug, any
CLAUDE.md convention extension AND any detection-layer addition
(CI check, pre-commit hook, test, or environmental assertion) that
codifies and structurally prevents recurrence SHOULD land in the
SAME commit as the fix — OR in an immediately-following Phase 7
capture commit if the fix is time-critical and the codification
needs care.

The bug is the evidence; the convention is the rule; the detection
is the enforcement. Splitting them across multiple commits is
acceptable; deferring indefinitely is not.

**Detection-trigger rule.** If a bug class has recurred twice in
different shapes, the next fix commit MUST add detection, not just
rules. Verbal rules have failed by definition; the third occurrence
is unacceptable. Example: CSD-03 recurred at Step 6.5.1 (tables)
and Step 6.10.1 deploy (enum casts + plpgsql + test code). The
next CSD-03 occurrence — if any — MUST land with a static-analysis
grep wired into `check_setup.sh` or CI, not just another note.

### Phase 5.5 - Local Pause (optional)

**Entry:** Phase 5 exit reached; step is DONE-LOCAL.
**Work:** Operator decides whether to deploy now or queue more local
work first.

A step may legitimately stay at DONE-LOCAL while the operator runs
additional steps through Phases 0-5. Reasons this is the right call:

- **Batched deploy is cheaper.** The 12-step build-and-deploy
  workflow has fixed overhead (image build, smoke run, log review).
  Three small steps deploying together cost roughly the same as one.
- **Step coupling.** Adjacent steps (e.g., 6.11 Tenants + 6.12 Stores)
  share the same architectural surface. Verifying them together gives
  cloud smoke a more meaningful exercise than verifying each in
  isolation.
- **Cloud risk is low.** When the cloud risk review in Phase 2 came
  back mostly N/A and the per-resource regression checkpoint is
  green, immediate cloud verification adds little signal.

When **NOT** to pause:

- **A cloud-only bug surfaced once already in the step.** Get back to
  green in cloud before touching another step.
- **A migration landed in this step.** Migrations want immediate cloud
  validation. Don't stack unmigrated steps behind it.
- **An env-var or IAM change landed.** Same reason — verify before
  layering more changes.
- **External commitments depend on the deploy.** A user, a downstream
  integration, or a scheduled rollout is waiting.

**Tracking pause status:** BUILD_PLAN.md entry stays at DONE-LOCAL
until Phase 6 closes. Multiple steps can sit at DONE-LOCAL
simultaneously. When Phase 6 runs, it covers all queued DONE-LOCAL
steps as a single deploy cycle, and each step flips to DONE with the
same cloud revision tag.

**Exit:** operator decides — proceed to Phase 6 now, or queue another
step (return to Phase 0 for the next step). When Phase 6 finally
runs, every DONE-LOCAL step in the queue advances together.
**Deliverable:** none beyond the DONE-LOCAL state already recorded.

### Phase 6 - Verify

**Entry:** local execution complete for one or more DONE-LOCAL steps;
operator has decided to deploy now.
**Work:** Human runs the documented 12-step build-and-deploy workflow.
The deploy covers all DONE-LOCAL steps queued in BUILD_PLAN.md.
Chat is available for log triage, error explanation, and root-cause
analysis. If a cloud-only bug surfaces, treat it the same way the
`fleet-stats` / `governance-stats` bug was treated: local-vs-cloud
differential, fix at source, add a regression test.

When multiple steps are batched: cloud smoke runs the test entries
from each step's `test_endpoints_cloud.sh` block. A bug surfacing in
the batched smoke gets attributed by bisecting against the
DONE-LOCAL queue — newest first.

**Exit:** cloud smoke tests green on the deployed revision.
**Deliverable:** deployed Cloud Run revision tag (e.g., `v0.1.9`).
All DONE-LOCAL steps covered by this deploy advance to DONE with
this revision tag.

### Phase 7 - Capture (conditional; post-deploy lessons only)

**Entry:** Phase 6 cloud smoke green. Phase 7 is conditional under
A6: triggered ONLY when cloud-emergent lessons surface during
Phase 6 (deploy surprises, IAM gotchas, production-vs-local
divergence, cloud smoke bugs, performance findings that contradict
local EXPLAIN ANALYZE predictions).

For steps where Phase 6 completes without surprises (the common
case for steps without DDL / IAM / cloud-state changes), Phase 7
is a NO-OP and the step is fully closed at Phase 6 deploy.

The pre-A6 work of Phase 7 (step doc retro, CLAUDE.md convention
additions, WORKFLOW.md amendments, BUILD_PLAN.md DONE-LOCAL flip)
now lives at Phase 5 commit time per A6. Phase 7 fires only for
the cloud-emergent slice.

**Work** (when triggered):

1. **Architecture consistency check.** Chat re-reads the relevant
   section of `architecture.md` against what actually shipped to
   cloud. If cloud reality diverged from the Phase 2 design (e.g.,
   a constraint behaves differently under load than under seed
   scale), update `architecture.md` now so it remains the source
   of truth.

2. **Cloud-emergent retro note.** Append to the step doc's Retro
   section under a sub-heading `### Post-deploy notes` describing
   what cloud surfaced that local didn't. Two or three bullets.
   Resist long narratives.

3. **CLAUDE.md update — only if warranted by the cloud lesson.**
   A new convention earns its place only when (a) it applies
   beyond this one step, (b) it's actionable without further
   context, and (c) its absence would plausibly cause a future
   bug. Most cloud-emergent lessons are step-specific and don't
   warrant a convention.

4. **BUILD_PLAN.md update.** Mark the step DONE with the deployed
   revision tag (transition from DONE-LOCAL to DONE).

5. **Code-volume gate measurement.** Captured at Phase 5 per A6;
   no Phase 7 work needed unless the gate was over and a deferred
   re-measurement is pending.

**Exit (when triggered):** all touched docs committed in a single
`Retro: Step X.Y.Z — post-deploy lessons` commit.

**Exit (when not triggered):** Phase 7 skipped; BUILD_PLAN.md
DONE-flip happens at Phase 6 close as part of the deploy commit.

**Deliverable (when triggered):** updated `architecture.md` (if
divergence), step doc Retro post-deploy notes section, optional
`CLAUDE.md` entry, `BUILD_PLAN.md` status update — all in one
commit.

---

## Artifact Lineage

```
session trigger
   │
   ▼
step doc (thin, repo root)           ← phases 1 → 5 (phase 7 if cloud lessons)
   │   • Mental Model       (phase 1)
   │   • DDL facts          (phase 1)
   │   • Design pointer     (phase 2)  → points into architecture.md
   │   • Plan               (phase 3, refined and committed at phase 5)
   │   • Retro              (phase 5, post-A6;
   │                        post-deploy notes appended at phase 7
   │                        only if cloud-emergent lessons)
   │
   ├─────────────► architecture.md      ← phase 5 writes design here
   │                (via impl prompt Appendix; or new arch doc, rare)
   │
   ├─────────────► /prompts/step-X_Y-impl-YYYY-MM-DD.md   ← phase 4
   │
   ├─────────────► git commits, Cloud Run revision        ← phases 5, 6
   │
   └─────────────► CLAUDE.md (if convention)              ← phase 5 (post-A6;
                                                             phase 7 only if
                                                             cloud-emergent)
                   BUILD_PLAN.md (DONE-LOCAL)             ← phase 5 (post-A6)
                   BUILD_PLAN.md (DONE with revision)     ← phase 6
                   WORKFLOW.md (amendments)               ← phase 5 (post-A6)
```

Durable knowledge lives in `architecture.md`, `CLAUDE.md`, and
`BUILD_PLAN.md`. The step doc is a thin audit trail with pointers, not
a parallel document.

---

## Roles, Concretely

| Actor       | Owns                                                       |
|-------------|------------------------------------------------------------|
| Human       | Direction, scope decisions, verification, deployment, final sign-off on all phases |
| Claude Chat | Mental model, design, planning, prompt authoring, log triage, capture |
| Claude Code | Investigation (read-only), implementation, local testing, commits |

Chat does **not** write code into files for execution. Code does **not**
make design or scope decisions on its own - when CC hits ambiguity, it
stops and asks the human, who routes the question to Chat if needed.

---

## Conventions This Workflow Inherits

These are already codified in `CLAUDE.md` and apply throughout:

- One command at a time for cloud workflows (no multi-command blocks).
- Raw SQL must schema-qualify table names for Cloud SQL compatibility.
- Cloud Run deploys use `--update-env-vars`, never `--set-env-vars`.
- Image deploys use sha256 digests, not tags.
- Step doc filenames: `step-X_Y-<name>-YYYY-MM-DD.md`.

If new conventions emerge during a step, Phase 7 is where they enter
`CLAUDE.md`.

---

## Anti-patterns to Avoid

- Skipping Phase 2's cloud risk review because "it's just a small change".
- Letting Phase 5 absorb design decisions that should have triggered a
  return to Phase 2.
- Writing the implementation prompt before the WBS is agreed.
- Chat producing prose-heavy designs when an ASCII diagram would do.
- Closing a step without recording the retro — learnings stay tribal.
  Under A6 the retro lands at Phase 5 commit by default; Phase 7
  fires only on cloud-emergent lessons. Either way, the step doc
  Retro section must be filled before the step is considered closed.
- **Design content living in the step doc instead of `architecture.md`.**
  The step doc is a meta-record; the architecture doc is the design.
- **`CLAUDE.md` additions written as retrospective narrative.** Entries
  must be terse, actionable rules. Stories belong in step docs.
- **Creating a new architecture doc when an existing section would do.**
  New docs only for genuinely new subsystems.
- **Committing architecture doc updates ahead of the code they
  describe.** Doc updates land at commit time with the implementing
  code, via Appendix in impl prompt. A doc PR that lands before
  implementation creates a truth-vs-reality drift painful to reconcile.
- **Multi-commit WBS that doesn't force a pause.** Default to single
  commit. Multiple commits add authorize-pause-report overhead for
  diminishing benefit. Use multi-commit only when a cloud-side action
  is forced between commits, a parallel step is blocked, or the
  foundations half is large enough to warrant a separate review
  boundary.
- **Code sprawl in the impl prompt.** Chat is the prompt drafter;
  Code is the implementer. Writing a 50-line SQL sketch or repo method
  body in the prompt is duplication of effort — Code rewrites it
  anyway against live conventions. Replace with behavior spec +
  acceptance criteria + test catalogue. The Phase 4 code-volume gate
  enforces this.
- **Remembering DDL instead of reading it.** Constraints, FKs, and
  audit-actor patterns are read live from
  `docs/schema/current_schema.sql` during Phase 1, captured as DDL
  facts in the step doc, and referenced by name in design. "I
  remember the schema has X" is a smell; replace with "Phase 1 DDL
  facts say X".
- **Raw DDL files in design reads.** `db/raw_ddl/*.sql` are historical
  authoring records. Design reads `docs/schema/current_schema.sql`
  (mirror of live schema). Treating raw_ddl as a design source
  produces drift bugs.

---

## Session Etiquette

- Chat names the current phase when it changes. Example:
  *"→ Phase 2: Design"*.
- Chat keeps responses focused; long phases get split across messages.
- Human can interrupt with an escape hatch at any time without ceremony.
- When in doubt, prefer one more clarifying exchange over a confident
  guess.
