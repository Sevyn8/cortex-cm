# Prompt — Tooling: API endpoint test harness (curl)

> Generated 2026-05-04.
> Paste this entire block into a fresh Claude Code session to build the test harness.
> A single bash script that drastically reduces the manual-curl effort of verifying every API endpoint after a server change. Generates 4 JWTs (2 PLATFORM, 2 TENANT), runs every endpoint with sensible variations (search, filter, sort, pagination, invalid-sort, unknown-id, cross-tenant probe, no-auth, tier-gate violations), checks each against an expected status, and prints a ✓/✗ summary. Tooling, not production code — does NOT add to BUILD_PLAN, does NOT touch the alembic chain, does NOT need new pytest coverage.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 5.2 (Tenant Users resource) is at or near HEAD. Note the actual HEAD; the prompt's endpoint matrix below is calibrated to Step 5.2's surface. If HEAD has moved past 5.2 with a new resource (e.g. Step 4.5 stores router landed first), STOP and surface — the matrix needs the new resource added before the script is useful, otherwise it gives false confidence by silently skipping the new endpoints.
3. Read `CLAUDE.md` fully. Focus on:
   - **D-17** — RLS-filtered rows surface as 404, not 403. Load-bearing for the cross-tenant probe expectations in this script.
   - **D-29** — PLATFORM RLS visibility (unconditional OR-branch). Load-bearing for why PLATFORM gets 200 on every tenant detail and TENANT gets 404 on cross-tenant.
   - **"Note on the v0 auth model"** — PLATFORM-only gate on `/platform-users` (403 `PLATFORM_ACCESS_REQUIRED`); multi-user-type endpoints scope by RLS.
4. Read `scripts/jwt/generate.sh` end-to-end. The new script invokes this for JWT minting; never reimplement it. Note especially:
   - The token filename derivation: `EMAIL_PREFIX=$(echo "$EMAIL" | sed 's/@.*//' | sed 's/[^a-zA-Z0-9_-]/-/g')`. The new script's `email_to_token_name` helper MUST produce the same string for any email, not a narrower rule. Mirror the regex verbatim.
   - The output path: `scripts/jwt/tokens/${EMAIL_PREFIX}.jwt`.
   - The script bails (`exit 1`) when the email isn't found; the new script must surface that failure as a fatal error with the email in the message, not a silent JWT file of length 0.
5. Read `docs/endpoints/openapi.json` (if present) — note the current path inventory (`jq -r '.paths | keys[]'`). The matrix below should match this list 1:1 minus the public paths. If a path is in the spec but not in the matrix, surface it under "Stop and ask if".
6. Read `docs/endpoints/tenants.md`, `docs/endpoints/platform-users.md`, `docs/endpoints/tenant-users.md` (whichever exist). Confirm the query-param names the matrix below uses (`search`, `status`, `sort`, `limit`, `offset`, `tenant_id`) match the actual implementations. **Do not silently substitute** — if `sort` is named `order_by` somewhere, surface; we'll align.
7. Check whether `scripts/jwt/tokens/` is gitignored. If not, add a one-line `.gitignore` rule (`scripts/jwt/tokens/`) in this commit — JWTs are short-lived dev tokens but committing them is still wrong-shaped.
8. Read this prompt fully.

---

## Task ID and intent

**Tooling** — `scripts/test_endpoints.sh`. Single deliverable: one bash script that, given a running local server, runs the full curl matrix across all current endpoints for 4 users and prints a pass/fail summary with response bodies saved on disk for debugging.

Concrete deliverables:

1. **`scripts/test_endpoints.sh`** — the script itself, executable.
2. **`.gitignore` update** — `scripts/jwt/tokens/` if not already ignored. (One line, possibly already present.)
3. **`scripts/test_endpoints/results/`** — runtime output directory (not committed; the script creates it). Add to `.gitignore`.
4. **No new tests, no new migration, no CLAUDE.md / BUILD_PLAN.md / architecture.md updates.** This is dev tooling; the BUILD_PLAN is for production code progression.

CLAUDE_CODE task. Pure bash. No Python, no Pydantic, no SQLAlchemy. Calls existing tooling (`generate.sh`, `curl`, `jq`). Does NOT start the server.

---

## Source-of-truth specification

### File 1: `scripts/test_endpoints.sh` — new

#### Invocation contract

```bash
# From admin-backend project root, with server already running on :8000:
./scripts/test_endpoints.sh

# Optional positional override of the four emails (P1 P2 T1 T2):
./scripts/test_endpoints.sh anjali@ithina.ai devon@ithina.ai \
    marcus.t@bucees.com a.kowalski@zabka.pl

# Optional env override of base URL:
BASE_URL=http://localhost:9000 ./scripts/test_endpoints.sh
```

Defaults:

| Slot | Email | User type | Tenant |
|---|---|---|---|
| `PLATFORM_EMAIL_1` | `anjali@ithina.ai` | PLATFORM | — |
| `PLATFORM_EMAIL_2` | `devon@ithina.ai` | PLATFORM | — |
| `TENANT_EMAIL_1` | `marcus.t@bucees.com` | TENANT | Buc-ee's |
| `TENANT_EMAIL_2` | `a.kowalski@zabka.pl` | TENANT | Żabka |

The two TENANT defaults are deliberately in **different tenants** so cross-tenant probes are meaningful. If a caller overrides T1 and T2 with two users from the same tenant, the script must warn (not bail); cross-tenant probe expectations would be wrong but the rest of the matrix still runs.

#### Top-level shape

```bash
#!/usr/bin/env bash
# scripts/test_endpoints.sh — curl harness for the admin-backend API.
# (Header docstring matching the comment style of generate.sh: usage, contract,
# what it deliberately does NOT do, e.g. start the server.)

set -uo pipefail
# Deliberately NOT set -e: individual curl calls are allowed to return non-200;
# we capture status codes and continue so the summary is complete. Setup-phase
# failures (server down, JWT-gen failed, fixture discovery returned null) DO
# bail via explicit `die` calls.

# === Configuration ============================================================
BASE_URL="${BASE_URL:-http://localhost:8000}"
API="${BASE_URL}/api/v1"
PLATFORM_EMAIL_1="${1:-anjali@ithina.ai}"
PLATFORM_EMAIL_2="${2:-devon@ithina.ai}"
TENANT_EMAIL_1="${3:-marcus.t@bucees.com}"
TENANT_EMAIL_2="${4:-a.kowalski@zabka.pl}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RESULTS_DIR="scripts/test_endpoints/results/${TIMESTAMP}"
JWT_GEN="./scripts/jwt/generate.sh"
JWT_DIR="scripts/jwt/tokens"
OPENAPI_DEST="docs/endpoints/openapi.json"

# === Helpers ==================================================================
# email_to_token_name <email>  — must mirror generate.sh's regex exactly:
#   sed 's/@.*//' | sed 's/[^a-zA-Z0-9_-]/-/g'
# anjali@ithina.ai     → anjali
# marcus.t@bucees.com  → marcus-t
# o'brien@bucees.com   → o-brien   (NOT o'brien — apostrophe is non-alnum)
email_to_token_name() {
    local email="$1"
    local localpart="${email%%@*}"
    echo "$localpart" | sed 's/[^a-zA-Z0-9_-]/-/g'
}

# req <label> <expected_status> <jwt_path_or_empty> <method> <url>
#   - Saves response body to ${RESULTS_DIR}/NNN__<label>.json
#   - Prints   [✓ 200] label                 on match
#   - Prints   [✗ 403, expected 200] label   on mismatch (with file path)
#   - Increments ok / fail counters
#   - Records failures in FAILURES array for end-of-run dump
req() { ... }

die() { echo "${C_RED}ERROR:${C_RST} $*" >&2; exit 1; }
section() { echo; echo "${C_BLU}━━━ $* ━━━${C_RST}"; }
```

#### Execution phases (in order)

**Phase 0 — Pre-flight.** Verify `jq` and `curl` exist on PATH. Verify `JWT_GEN` is executable. Curl `${API}/health` and bail with a hint if not 200 ("Start it first: `uv run uvicorn admin_backend.main:app --host 0.0.0.0 --port 8000`"). `mkdir -p` the results dir and `dirname "$OPENAPI_DEST"`.

**Phase 1 — Save OpenAPI.** `curl -sf "${API}/openapi.json" -o "$OPENAPI_DEST"`. Print path count via `jq '.paths | length'` and the path list. If the curl fails, bail.

**Phase 2 — Generate 4 JWTs.** For each of the 4 emails, invoke `$JWT_GEN <email>`. If it fails, re-run without redirect so the underlying error surfaces, then `die`. Build `P1_JWT`, `P2_JWT`, `T1_JWT`, `T2_JWT` paths (NOT contents — `req` reads the file each call so a regenerated JWT mid-run still works).

**Phase 3 — Discover fixture IDs (PLATFORM_1 caller).** PLATFORM sees all rows via D-29. Save discovery responses to `${RESULTS_DIR}/000__setup__*.json`:

```bash
# All tenants (used for cross-tenant probes)
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/tenants?limit=100" -o "${RESULTS_DIR}/000__setup__tenants.json"

# All tenant-users — used to derive T1's and T2's tenant_id and user_id
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/tenant-users?limit=200" -o "${RESULTS_DIR}/000__setup__tenant_users.json"

# Sample platform-user id
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/platform-users?limit=10" -o "${RESULTS_DIR}/000__setup__platform_users.json"

# Extract via jq (match by email — robust against UUID drift across reseeds):
T1_TENANT_ID=$(jq -r --arg e "$TENANT_EMAIL_1" \
    '.items[] | select(.email==$e) | .tenant_id' \
    < "${RESULTS_DIR}/000__setup__tenant_users.json" | head -1)
T1_USER_ID=$(jq -r --arg e "$TENANT_EMAIL_1" \
    '.items[] | select(.email==$e) | .id' \
    < "${RESULTS_DIR}/000__setup__tenant_users.json" | head -1)
# (same pattern for T2_TENANT_ID, T2_USER_ID)
ANY_PLATFORM_USER_ID=$(jq -r '.items[0].id' < "${RESULTS_DIR}/000__setup__platform_users.json")
UNKNOWN_UUID="00000000-0000-0000-0000-000000000000"
```

Bail with a clear message if any of `T1_TENANT_ID`, `T2_TENANT_ID`, `T1_USER_ID`, `T2_USER_ID`, `ANY_PLATFORM_USER_ID` is empty or `null`. **Warn (not bail)** if `T1_TENANT_ID == T2_TENANT_ID`: cross-tenant probes will assert the wrong thing.

**Phase 4 — Run the matrix.** See "The matrix" section below.

**Phase 5 — Summary.** Print total / passed / failed counts. If any failed, dump the `FAILURES` array (each entry is `label (got X, expected Y) → outfile`). Exit 1 if any failed; 0 otherwise.

#### The matrix

Expected status codes encode the v0 auth model. The matrix below is the source of truth for the script. When a new endpoint lands, a new row block is added; when an auth rule changes, an existing column changes.

**Public (no JWT) — run once, not per user:**

| Call | Method + URL | Expected |
|---|---|---|
| `public__health` | `GET /api/v1/health` | 200 |
| `public__ready` | `GET /api/v1/ready` | 200 |
| `public__openapi` | `GET /api/v1/openapi.json` | 200 |
| `noauth__tenants_401` | `GET /api/v1/tenants` (no Authorization header) | 401 |

**Per-user — both PLATFORM users and both TENANT users.** "Expected" columns: `P` = PLATFORM caller; `T_own` = TENANT caller asking about own tenant; `T_other` = TENANT caller asking about the other tenant.

| Label | Method + URL | P | T_own | T_other |
|---|---|---|---|---|
| `lookups__all` | `GET /lookups?lists=tenant_tier,tenant_region,tenant_status,tenant_industry,module_code,country` | 200 | 200 | — |
| `lookups__empty_param` | `GET /lookups?lists=` | 200 | 200 | — |
| `lookups__unknown_list` | `GET /lookups?lists=does_not_exist` | 200 | 200 | — |
| `tenants__list` | `GET /tenants` | 200 | 200 | — |
| `tenants__list_limit2` | `GET /tenants?limit=2&offset=0` | 200 | 200 | — |
| `tenants__list_offset2` | `GET /tenants?limit=2&offset=2` | 200 | 200 | — |
| `tenants__search_buc` | `GET /tenants?search=Buc` | 200 | 200 | — |
| `tenants__sort_name_asc` | `GET /tenants?sort=name` | 200 | 200 | — |
| `tenants__sort_name_desc` | `GET /tenants?sort=-name` | 200 | 200 | — |
| `tenants__invalid_sort` | `GET /tenants?sort=fake_column` | 400 | 400 | — |
| `tenants__stats` | `GET /tenants/stats` | 200 | 200 | — |
| `tenants__detail_own` | `GET /tenants/${OWN_TENANT_ID}` | 200 | 200 | — |
| `tenants__detail_cross` | `GET /tenants/${OTHER_TENANT_ID}` | 200 | — | **404** |
| `tenants__detail_unknown` | `GET /tenants/${UNKNOWN_UUID}` | 404 | 404 | — |
| `plat_users__list` | `GET /platform-users` | 200 | **403** | — |
| `plat_users__list_active` | `GET /platform-users?status=ACTIVE` | 200 | **403** | — |
| `plat_users__search` | `GET /platform-users?search=an` | 200 | **403** | — |
| `plat_users__sort_email` | `GET /platform-users?sort=email` | 200 | **403** | — |
| `plat_users__pagination` | `GET /platform-users?limit=2&offset=0` | 200 | **403** | — |
| `plat_users__invalid_sort` | `GET /platform-users?sort=nope` | 400 | **403** | — |
| `plat_users__detail` | `GET /platform-users/${ANY_PLATFORM_USER_ID}` | 200 | **403** | — |
| `plat_users__detail_unknown` | `GET /platform-users/${UNKNOWN_UUID}` | 404 | **403** | — |
| `tu__list` | `GET /tenant-users` | 200 | 200 | — |
| `tu__list_active` | `GET /tenant-users?status=ACTIVE` | 200 | 200 | — |
| `tu__search` | `GET /tenant-users?search=a` | 200 | 200 | — |
| `tu__sort_email` | `GET /tenant-users?sort=email` | 200 | 200 | — |
| `tu__pagination` | `GET /tenant-users?limit=2&offset=0` | 200 | 200 | — |
| `tu__invalid_sort` | `GET /tenant-users?sort=nope` | 400 | 400 | — |
| `tu__filter_own_tenant` | `GET /tenant-users?tenant_id=${OWN_TENANT_ID}` | 200 | 200 | — |
| `tu__filter_other_tenant` | `GET /tenant-users?tenant_id=${OTHER_TENANT_ID}` | 200 | 200 (empty result via RLS) | — |
| `tu__detail_own` | `GET /tenant-users/${OWN_USER_ID}` | 200 | 200 | — |
| `tu__detail_cross` | `GET /tenant-users/${OTHER_USER_ID}` | 200 | — | **404** |
| `tu__detail_unknown` | `GET /tenant-users/${UNKNOWN_UUID}` | 404 | 404 | — |

Two non-obvious rows worth flagging:

- `plat_users__invalid_sort` for TENANT caller is **403** not 400. The `_require_platform_auth` gate runs before sort validation; the gate fires first.
- `tu__filter_other_tenant` for TENANT caller is **200 with empty `items`**, not 404. RLS intersects the explicit `tenant_id=` filter with the session GUC (`app.tenant_id`) — non-matching values produce an empty list, not a permission error. This is correct behaviour and the script asserts it.

For the **PLATFORM** runs, `T_other` is moot (PLATFORM has no "other" tenant); just point both tenants/users at the discovered values and expect 200 on detail-anything.

#### Output format

Per call:

```
  [✓ 200] anjali_P__tenants__list
  [✗ 403, expected 200] devon_P__tenants__detail_T2  (scripts/test_endpoints/results/20260504_142312/045__devon_P__tenants__detail_T2.json)
```

Section headers (color blue):

```
━━━ TENANT user 1 — marcus.t@bucees.com (tenant=972a8469-...) ━━━
```

End-of-run summary:

```
━━━ Summary ━━━
  Total calls:  124
  Passed:       122
  Failed:       2
  Results saved to: scripts/test_endpoints/results/20260504_142312/

Failures:
  - devon_P__tenants__detail_T2 (got 403, expected 200) → scripts/test_endpoints/results/20260504_142312/045__devon_P__tenants__detail_T2.json
  - marcus-t_T__tu__detail_cross (got 200, expected 404) → scripts/test_endpoints/results/20260504_142312/098__marcus-t_T__tu__detail_cross.json
```

Color: green ✓ on success, red ✗ on failure, dim grey for the file path. Detect tty (`if [[ -t 1 ]]`); fall back to no color when piped to a file.

Exit code: 0 on all-green, 1 if any call failed.

### File 2: `.gitignore` update

Append (or confirm present):

```
scripts/jwt/tokens/
scripts/test_endpoints/results/
```

---

## Verification harness

Run all four; all must be green.

```bash
# 1. Syntax check (no execution)
bash -n scripts/test_endpoints.sh

# 2. ShellCheck (if available — not a hard dep, but if installed it should pass)
shellcheck scripts/test_endpoints.sh || true   # advisory only

# 3. Live run end-to-end. Server must be up first:
#    uv run uvicorn admin_backend.main:app --host 0.0.0.0 --port 8000
./scripts/test_endpoints.sh
echo "Exit code: $?"   # expect 0

# 4. Verify the OpenAPI artifact landed
jq '.paths | keys' < docs/endpoints/openapi.json
# expect: ["/api/v1/health", "/api/v1/lookups", "/api/v1/openapi.json", ...
#          "/api/v1/platform-users", "/api/v1/platform-users/{user_id}",
#          "/api/v1/ready", "/api/v1/tenant-users", "/api/v1/tenant-users/{user_id}",
#          "/api/v1/tenants", "/api/v1/tenants/stats", "/api/v1/tenants/{tenant_id}"]
```

Expected total call count at the time of writing: in the range of 110–130 (4 public + (lookups 3 + tenants 13 + plat_users 8 + tenant_users 13) × 4 users ≈ 152, give or take some PLATFORM-only-applicable rows). Don't hardcode the count; print whatever the run produces.

If a leg is not green, **report rather than commit**.

---

## Scope out

- **OpenAPI-driven endpoint discovery.** Considered and rejected. Knowing what variations to test (which query params have meaningful values, which IDs are cross-tenant) requires endpoint-specific knowledge that doesn't live in the OpenAPI spec. The matrix is the maintenance surface; new endpoints add a row block. Reconsider if endpoints land at a rate that makes manually-maintained matrix entries onerous (probably not before v1).
- **Negative-body assertions** ("the 404 response must include `code: TENANT_NOT_FOUND`"). Status code matching is sufficient signal for a smoke harness; deep body assertions belong in pytest integration tests, which already cover this. The script saves bodies to disk so the developer can `jq` over them post-run if needed.
- **Concurrent execution.** All curls are sequential. With ~130 calls at <100ms each, total runtime is around 10–20 seconds — fine. Parallelising would complicate output ordering and offer little.
- **Credential rotation / JWT TTL.** `generate.sh` mints a fresh JWT every run; no caching needed. JWT TTL is whatever `make_test_jwt` defaults to (likely 24h+ for dev).
- **CI integration.** Out of scope. The script is for local verification; once a CI environment exists with a running server fixture, an additional Makefile target or workflow YAML can wrap it.
- **Adding to `BUILD_PLAN.md` / `CLAUDE.md`.** Tooling, not a build step. No doc updates.
- **Writes/POST/PATCH/DELETE.** v0 is read-only.

---

## Stop and ask if

- **HEAD has moved past Step 5.2.** A new resource (e.g. Step 4.5 stores router) means the matrix is missing rows. Surface what's new and either add to the matrix in this same commit or note explicitly that the script will skip the new endpoint until the next tooling refresh. Skipping silently is worse than incomplete coverage that's documented.
- **A path in `docs/endpoints/openapi.json` is not represented in the matrix.** Same as above — list the missing paths, propose additions.
- **Query-param naming drift.** If the actual implementation uses `?q=` instead of `?search=`, or `?order_by=` instead of `?sort=`, surface; we'll align the matrix not the implementation.
- **`./scripts/jwt/generate.sh` doesn't exist or doesn't take a single email argument.** The whole Phase 2 design depends on this. Surface.
- **Both TENANT defaults end up in the same tenant after seed.** Different rows, same `tenant_id`. The cross-tenant probe rows would then assert the wrong outcome. The script should warn at runtime; if you hit this during the verification run, surface so we can pick a different default for one of them.
- **`/scripts/jwt/tokens/` is already gitignored elsewhere.** Don't double-add the rule.
- **`docs/endpoints/openapi.json` is already gitignored** (it would be unusual — it's an artifact frontend codegen depends on per Step 5.2 — but worth checking before this script overwrites it on every run). If gitignored, surface; the convention may have shifted.

---

## Acceptance criteria

- `scripts/test_endpoints.sh` exists, executable (`chmod +x`), passes `bash -n`.
- Run with the server up exits 0.
- Run with the server down exits non-zero with a clear "Start it first: ..." message; does not produce a half-written results dir.
- All four JWT files land in `scripts/jwt/tokens/` after a clean run.
- `docs/endpoints/openapi.json` is refreshed and contains all expected paths.
- `scripts/test_endpoints/results/<timestamp>/` exists, contains one JSON file per call (count matches printed total), plus three `000__setup__*.json` discovery files.
- Output uses ANSI colors when stdout is a tty; plain text when piped (`./scripts/test_endpoints.sh > out.txt` produces a clean grep-able log).
- A deliberately-broken-server scenario surfaces as failed `req` calls with the saved 5xx body, not as a script crash.
- Override via positional args works: `./scripts/test_endpoints.sh kira@ithina.ai devon@ithina.ai marcus.t@bucees.com a.kowalski@zabka.pl` runs without modification.
- Override of two TENANT users to the same tenant produces a runtime warning (yellow ⚠) but does not bail.
- `.gitignore` excludes `scripts/jwt/tokens/` and `scripts/test_endpoints/results/`.
- No new entries in `CLAUDE.md`, `BUILD_PLAN.md`, `architecture.md`, `docs/endpoints/*.md`, no new pytest, no new alembic migration. Tooling is tooling.

---

## Report (BEFORE proposing commit)

Five bundles per the convention, adapted for tooling:

1. **Code:** `scripts/test_endpoints.sh` line count; `.gitignore` diff; the live-run output (full stdout from a clean run with the server up, redacted of any UUIDs if you prefer).
2. **CLAUDE.md updates:** "no change" — tooling, not a build step.
3. **BUILD_PLAN.md updates:** "no change" — tooling, not a build step.
4. **architecture.md updates:** "no change" — no architectural surface.
5. **OpenAPI snapshot:** `docs/endpoints/openapi.json` regenerated by the run itself (a side effect of Phase 1). Note in the report whether `git diff docs/endpoints/openapi.json` shows changes — if yes, that's an unrelated drift surfaced by this work; flag it for the user to review separately.
6. **Prompt file:** `prompts/tooling-api-test-harness-2026-05-04.md` confirmed in commit set.

Plus: `bash -n` clean; `shellcheck` clean (advisory); total call count from the run (`Total calls: NNN`); pass/fail counts; runtime in seconds.

If any leg of the verification harness is not green, **report rather than commit**.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
