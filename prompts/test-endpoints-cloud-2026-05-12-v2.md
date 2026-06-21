# Prompt — test_endpoints_cloud.sh: cloud-targeted variant (v2)

## Goal

Create `scripts/test_endpoints_cloud.sh`, a cloud-targeted variant of
`scripts/test_endpoints_max_view.sh`. **Same matrix, same assertions,
same output style** — only the JWT loading flow, fixture discovery
source, side-effect file paths, and pre-flight checks differ.

This is **not a rewrite of the matrix.** Phase 4 (the matrix itself)
must be byte-identical in code to `test_endpoints_max_view.sh`
(commit `1df5cf3`) **except** for the four JWT path variable
declarations (`P1_JWT_PATH`, `P2_JWT_PATH`, `T1_JWT_PATH`,
`T2_JWT_PATH`) which point at different files. **No variable
renames in Phase 4. No comment changes. No whitespace changes. No
"clarity" refactoring.** The differences are concentrated in Phases
0-3 (setup) and the configuration block at the top of the file.

## Pre-conditions for running the script (operator-side)

Before invoking `scripts/test_endpoints_cloud.sh` as part of
verification, confirm:

1. Cloud service is healthy:
   `curl -s https://admin-backend-f2qhpcdeba-el.a.run.app/api/v1/health`
   returns 200 with the expected version
2. The 9 cloud JWT files exist:
   `ls scripts/jwt/tokens/cloud/*-cloud-150d.jwt` returns 9 lines.
   **If missing, do NOT mint them from inside this script.** They
   are minted by the operator via a separate one-time process (see
   commit `d689fb3` context and the JWT minting conversation that
   produced them). The cloud variant is a CONSUMER of these tokens,
   not a producer

The cloud script will bail loudly in Phase 0 if either condition fails.

## Pre-flight reading

Read these before proposing the file:

- `scripts/test_endpoints_max_view.sh` (current canonical local
  variant, post-commit `1df5cf3`) — this is the matrix to clone.
  Phase 4 byte-identity required (see above for exact constraints)
- `scripts/smoke_curl.sh` — the lightweight cloud-aware smoke. Its
  JWT-loading pattern via `PJWT_FILE` / `TJWT_FILE` env vars is the
  right precedent for accepting pre-minted JWTs without runtime
  minting
- `scripts/jwt/tokens/cloud/` — directory containing the 9 pre-minted
  150-day JWTs. Naming convention: `<email-prefix>-cloud-150d.jwt`.
  **The cloud script must not modify, mint, delete, or refresh these
  files.** It only reads them
- `docs/endpoints/openapi.json` — the local OpenAPI snapshot. **The
  cloud script MUST NOT overwrite this file.** Use
  `/tmp/openapi-cloud-${TIMESTAMP}.json` for the cloud variant's
  Phase 1 output
- `prompts/test-endpoints-gap-fill-2026-05-12-v2.md` — the gap-fill
  prompt that landed via commit `1df5cf3`. Context on which 5
  endpoints were added and (importantly) the status-code
  verification discipline that applies uniformly to all 307 matrix
  cells

After reading, restate scope and the four structural differences
(JWT loading, fixture discovery flow, side-effect paths, pre-flight
checks) before writing code.

## Naming and variable conventions

A note on what looks weird but is intentional:

The local script's `req()` function accepts a JWT **file path** as
its third argument and `cat`s the contents inside the function. The
variables used in Phase 4 (`$P1_JWT`, `$P2_JWT`, `$T1_JWT`, `$T2_JWT`)
hold file paths, not JWT contents. The naming is misleading — the
variables are paths.

The cloud variant inherits this convention (because Phase 4 is
byte-identical). Phase 2 in the cloud variant assigns the cloud JWT
paths to the same variable names:

```bash
P1_JWT="$P1_JWT_PATH"   # P1_JWT holds a file path; req() reads it
P2_JWT="$P2_JWT_PATH"
T1_JWT="$T1_JWT_PATH"
T2_JWT="$T2_JWT_PATH"
```

**Do not "fix" this naming.** Keeping `$P1_JWT` as the variable name
in Phase 4 is what makes the matrix byte-identical. Refactoring it
to `$P1_JWT_PATH` everywhere is forbidden by the byte-identity
constraint.

## Differences from test_endpoints_max_view.sh (the only changes)

### 1. Configuration block (top of script)

| Local script | Cloud script |
|---|---|
| `BASE_URL="${BASE_URL:-http://localhost:8000}"` | `BASE_URL` is REQUIRED positional arg (no default; print usage and bail if missing) |
| `RESULTS_DIR="scripts/test_endpoints/results/${TIMESTAMP}"` | `RESULTS_DIR="scripts/test_endpoints/results-cloud/${TIMESTAMP}"` |
| `OPENAPI_DEST="docs/endpoints/openapi.json"` | `OPENAPI_DEST="/tmp/openapi-cloud-${TIMESTAMP}.json"` |
| `JWT_GEN="./scripts/jwt/generate.sh"` | **Removed.** Cloud variant doesn't mint JWTs |
| `JWT_DIR="scripts/jwt/tokens"` | `JWT_DIR="scripts/jwt/tokens/cloud"` |
| 4 positional email args | **Removed.** Cloud script takes BASE_URL only as positional. Hardcoded JWT-to-caller mapping below |

Hardcoded JWT-to-caller mapping (visible at top of file):

```bash
P1_JWT_PATH="${P1_JWT_PATH:-${JWT_DIR}/anjali-cloud-150d.jwt}"
P2_JWT_PATH="${P2_JWT_PATH:-${JWT_DIR}/devon-cloud-150d.jwt}"
T1_JWT_PATH="${T1_JWT_PATH:-${JWT_DIR}/marcus-t-cloud-150d.jwt}"
T2_JWT_PATH="${T2_JWT_PATH:-${JWT_DIR}/a-kowalski-cloud-150d.jwt}"
```

The `${P1_JWT_PATH:-...}` pattern allows ad-hoc swaps:
`P1_JWT_PATH=path/to/other.jwt ./scripts/test_endpoints_cloud.sh ...`

Choice rationale (comment in the script):
- Anjali (P1) and Devon (P2) — two distinct PLATFORM identities
- Marcus (T1, Buc-ee's) and Anna (T2, Żabka Group) — two TENANT
  callers in DIFFERENT tenants. Cross-tenant probes are meaningful

### 2. Phase 0 — Pre-flight

**Drop entirely:**
- The `email_exists_in_db` function (queries local DB; wrong substrate)

**Keep:**
- `jq` and `curl` on PATH check (unchanged)

**Replace server-health check:**
- Hit `${API}/health`, expect 200. Bail with "is the cloud service
  up at ${BASE_URL}?" on failure
- **Add `/ready` check:** hit `${API}/ready`, parse JSON, expect
  `.db == "ok"`. If not, bail — cloud DB is the substrate for all
  downstream

**Add new pre-flight: JWT file presence.**
For each of `P1_JWT_PATH` through `T2_JWT_PATH`:
- `[[ -s "$path" ]]` (file exists and non-empty)
- Bail on missing with the exact message:
  `"Cloud JWT missing: ${path}. The cloud variant does NOT mint JWTs;
  see commit d689fb3 context for the minting flow."`

**Do NOT add a JWT-expiry check.** 150-day window is comfortable; if
a JWT expires, matrix cells will fail with 401 and the operator will
know immediately.

### 3. Phase 1 — Save OpenAPI

Functionally unchanged: fetch `${API}/openapi.json`, pretty-print
through `jq`, save. Only the destination differs:

| Local | Cloud |
|---|---|
| `docs/endpoints/openapi.json` | `/tmp/openapi-cloud-${TIMESTAMP}.json` |

Print the path count and first-30 paths line as the local does.

### 4. Phase 2 — JWT loading (BIG STRUCTURAL CHANGE)

**Replace ALL of Phase 2.**

The local script's Phase 2 mints 4 JWTs by calling
`scripts/jwt/generate.sh`. The cloud script **does not mint**. It
loads pre-minted file paths into the `$P1_JWT` through `$T2_JWT`
variables (which hold paths, per the naming convention note above).

New Phase 2 body:

```bash
section "Phase 2 — load 4 cloud JWTs"

P1_JWT="$P1_JWT_PATH"
P2_JWT="$P2_JWT_PATH"
T1_JWT="$T1_JWT_PATH"
T2_JWT="$T2_JWT_PATH"

# Per-file existence already verified in Phase 0; just log here.
echo "  ${C_GRN}✓${C_RST} P1 PLATFORM → $P1_JWT"
echo "  ${C_GRN}✓${C_RST} P2 PLATFORM → $P2_JWT"
echo "  ${C_GRN}✓${C_RST} T1 TENANT   → $T1_JWT"
echo "  ${C_GRN}✓${C_RST} T2 TENANT   → $T2_JWT"
```

### 5. Phase 3 — Fixture discovery (mostly cloud-friendly already)

Phase 3 in the local script queries the API itself, not local DB,
so it works against cloud as-is. **No structural change needed in
the discovery queries.**

**Two small changes** because Phase 2 removed the email positional args:

(a) Add near the top of the file alongside the JWT path declarations:

```bash
# These emails must correspond to the TENANT JWT users above (T1/T2).
# Phase 3 uses them for fixture discovery (matching tenant_user records
# in cloud's response).
TENANT_EMAIL_1="marcus.t@bucees.com"  # T1
TENANT_EMAIL_2="a.kowalski@zabka.pl"  # T2
```

(b) Replace any references to `$PLATFORM_EMAIL_1` / `$PLATFORM_EMAIL_2`
that might exist in Phase 3 — verify by grepping the local script. If
they appear and aren't actually used (Phase 3 currently uses
`ANY_PLATFORM_USER_ID` from the API response, not from email matching),
remove the references cleanly. If they ARE used, surface and ask.

### 6. Phase 4 — Matrix (UNCHANGED, byte-identical)

Same 307 cells, same expected statuses, same per-caller iteration.

**Do not add, remove, modify, rename, refactor, or otherwise change
any cell or variable in Phase 4.** Including:
- No variable renames (Phase 4 must use `$P1_JWT`, `$P2_JWT`,
  `$T1_JWT`, `$T2_JWT` as the local script does)
- No comment edits
- No whitespace changes
- No "clarity" wrappings or extractions

Verification step after porting:

```bash
# Extract Phase 4 from each script: section "Phase 4 — run matrix"
# to the start of the summary block (look for the section "Summary"
# or "=== Summary ===" marker).
# Diff the two extracted regions. Expected: ZERO differences.
diff <(awk '/section "Phase 4/,/=== Summary/' scripts/test_endpoints_max_view.sh) \
     <(awk '/section "Phase 4/,/=== Summary/' scripts/test_endpoints_cloud.sh)
```

If the diff is non-empty, Phase 4 is NOT byte-identical. Surface what
differs and either remove the deviation or explain why it was required.

### 7. Summary block

Functionally identical to local. Outputs matrix totals, counts,
failures. No cloud-specific changes needed.

## Scope out

- **`CLAUDE.md` and `BUILD_PLAN.md` updates.** Same exclusion as
  the gap-fill prompt: the operator has structural revisions to
  those files in flight. Do NOT propose edits to either file. Do
  NOT add either file to the commit's `git add` list. The operator
  handles them separately
- Deep response-body assertions. Status-codes-only is the harness
  convention; do not bolt on body shape validation
- New helper functions unless strictly needed
- Reordering or refactoring existing cells in Phase 4 (byte-identity
  required)
- Updating `scripts/smoke_curl.sh` — separate concern
- Updating `scripts/test_endpoints_max_view.sh` — the local variant
  stays unchanged
- Updating `scripts/test_endpoints.sh` — older sibling, untouched
- Updating `docs/endpoints/openapi.json` — explicitly forbidden by
  Phase 1 redirection
- A separate "cloud JWT minter" CLI. The minting happens outside
  this script (operator workflow, commit `d689fb3` context)
- Refreshing the 9 cloud JWTs. They're 150-day expiry, well within
  window. Refresh is a separate operator workflow when expiry
  approaches

## Stop and ask if

1. The local script's `req()` function does NOT take a path (i.e.,
   it expects JWT contents directly). Affects Phase 4 variable
   semantics. Surface and ask
2. Phase 4 in the local script references any local-specific
   construct (DB query, file path under
   `scripts/test_endpoints/results/`, env-var-from-local-DB, etc.)
   that doesn't translate to cloud. Surface the specific lines
3. Fixture discovery in Phase 3 returns ambiguous matches for
   `marcus.t@bucees.com` or `a.kowalski@zabka.pl` (the email
   resolves to multiple tenant_users in cloud). Cloud data was
   verified earlier today as 1:1, but surface if different
4. Phase 4 byte-identity diff (Section 6's verification step)
   produces ANY non-empty output. Stop. Either fix the deviation
   or report it as a required deviation with explicit justification
5. The `make_test_jwt()` function signature has changed since the
   JWTs were minted (check by reading `src/admin_backend/auth/testing.py`).
   If the kwargs the JWTs were minted with don't match what the
   function currently accepts, surface — the JWTs may not validate
   against the deployed service

## Acceptance criteria

1. New file at `scripts/test_endpoints_cloud.sh`, executable
   (`chmod +x`), within ±10% of `test_endpoints_max_view.sh`'s
   line count
2. Phase 4 `awk`-extracted diff against
   `test_endpoints_max_view.sh` produces zero output
3. Bash syntax check passes: `bash -n scripts/test_endpoints_cloud.sh`
   exits 0 with no parser errors
4. Static review of Phases 0-3 against this prompt's spec: every
   bullet in sections 1-5 of "Differences from
   test_endpoints_max_view.sh" is satisfied (cite the line range in
   the new script for each)

**This step does NOT execute the cloud script.** The operator runs
the script manually against cloud after committing and verifies
end-to-end behavior on their own. Claude Code's job is to deliver
a script that is structurally correct, statically valid, and
satisfies the byte-identity constraint on Phase 4. **No `curl` to
cloud, no `gcloud` calls, no execution of `./scripts/test_endpoints_cloud.sh`,
no JWT validation probes against the deployed service.**

If Claude Code needs to verify a runtime concern (e.g., "does
`make_test_jwt` exist in the codebase"), use static checks: grep
the source, read the function signature, etc. Do not call out to
the cloud service.

## Report before commit

1. Files created with line counts (new script + this prompt file)
2. Phase-by-phase delta from `test_endpoints_max_view.sh`:
   - Phase 0: what was dropped, added, kept (one paragraph)
   - Phase 1: redirect destination
   - Phase 2: load mechanism
   - Phase 3: email-var changes
   - **Phase 4: byte-identity verification result.** Paste the
     output of the `awk`-extracted diff. Expected: zero lines
3. Bash syntax check: `bash -n scripts/test_endpoints_cloud.sh`
   output (expected: silent, exit 0)
4. Static-review evidence: for each major spec bullet in sections
   1-5 of "Differences from test_endpoints_max_view.sh", cite the
   line range in the new script that satisfies it
5. `git diff --stat` showing only `scripts/test_endpoints_cloud.sh`
   and (optionally) `prompts/test-endpoints-cloud-2026-05-12-v2.md`
   if you bundle the prompt. Zero unintended changes elsewhere

**Do NOT include in the report:**
- Output of running the script against cloud
- Results from `results-cloud/<timestamp>/`
- Cell pass/fail counts
- Any curl probes against the cloud service

The operator runs the script manually after the commit lands and
verifies end-to-end behavior separately.

Wait for explicit operator authorisation before committing.

## After committing

Propose the commit per CLAUDE.md "After completing a task" pattern.
The prompt file (`prompts/test-endpoints-cloud-2026-05-12-v2.md`)
lands alongside the script per project convention. The 9 cloud JWT
files are gitignored and intentionally NOT in the commit set.

```
scripts: cloud-targeted test_endpoints variant

scripts/test_endpoints_cloud.sh: new file. Cloud-aware sibling of
test_endpoints_max_view.sh. Phase 4 byte-identical to local (verified
via awk-extracted diff); Phases 0-3 adapted for cloud substrate:

- Phase 0: drops email_exists_in_db local-DB check; adds JWT-file
  presence check and /ready db:ok check
- Phase 1: writes /tmp/openapi-cloud-<ts>.json; docs/endpoints/openapi.json
  untouched
- Phase 2: loads 4 pre-minted 150-day JWTs from scripts/jwt/tokens/cloud/
  (gitignored). No runtime minting in the cloud script
- Phase 3: hardcoded email-to-JWT pairing (marcus-t/a-kowalski) for
  fixture-discovery; PLATFORM email vars dropped where unused
- Phase 4: byte-identical to local (verified)

bash -n syntax check clean. Script structure-only verified by Claude
Code; end-to-end cloud verification by operator runs the script
separately.

The 9 cloud JWTs were minted via inline make_test_jwt() in commit
d689fb3's context. They are gitignored. Refresh is a separate
operator workflow when 150-day expiry approaches.

prompts/test-endpoints-cloud-2026-05-12-v2.md: prompt that drove
this work.
```

## Next

After this lands, the test_endpoints harness is fully cloud-ready
and matches local 1:1 in cell count and coverage. Future endpoint
additions go to test_endpoints_max_view.sh first; the cloud variant
is then re-synced (Phase 4 byte-identity makes re-syncs trivial).

No additional prompts in the test_endpoints chain. The
frontend-debugging concern (separate track) is the next thing
worth attention.
