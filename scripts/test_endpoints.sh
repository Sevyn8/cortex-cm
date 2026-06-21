#!/usr/bin/env bash
# scripts/test_endpoints.sh — curl harness for the admin-backend API.
#
# Usage (from project root, with server already running on :8000):
#   ./scripts/test_endpoints.sh
#
# Optional positional override of the four test users (P1 P2 T1 T2):
#   ./scripts/test_endpoints.sh anjali@ithina.ai devon@ithina.ai \
#       marcus.t@bucees.com a.kowalski@zabka.pl
#
# Optional env override of base URL:
#   BASE_URL=http://localhost:9000 ./scripts/test_endpoints.sh
#
# What this does:
#   - Verifies server health, mints 4 JWTs (2 PLATFORM, 2 TENANT in different
#     tenants), discovers fixture IDs, runs every API endpoint with sensible
#     variations, prints a pass/fail summary.
#   - Saves every response body to scripts/test_endpoints/results/<timestamp>/
#     for post-run debugging.
#   - Refreshes docs/endpoints/openapi.json as a side effect of Phase 1.
#
# What this does NOT do:
#   - Does NOT start the server. Run uvicorn separately first.
#   - Does NOT mint JWTs from scratch — calls scripts/jwt/generate.sh.
#   - Does NOT POST/PATCH/DELETE — v0 is read-only.
#   - Does NOT do deep response-body assertions; status codes only.
#     Bodies are saved on disk so a developer can `jq` over them after a run.
#
# Exit codes:
#   0  — all calls returned the expected status.
#   1  — at least one call mismatched, or pre-flight bailed.
#
# History:
#   - Initial: Steps 3.3, 5.1, 5.2 endpoints (tenants, *_users, lookups).
#   - Step 6.1: added 4 RBAC endpoints (roles, role-permissions sub-resource,
#     permissions catalogue, permission-matrix).

set -uo pipefail
# Deliberately NOT `set -e`: individual curl calls are allowed to return
# non-200; the harness captures status codes and continues so the summary
# is complete. Setup-phase failures bail explicitly via `die`.

# === Configuration ===========================================================
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
UNKNOWN_UUID="00000000-0000-0000-0000-000000000000"

START_TIME="$(date +%s)"

# === Color setup ============================================================
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_BLU=$'\033[34m'; C_DIM=$'\033[2m';  C_RST=$'\033[0m'
else
    C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_DIM=""; C_RST=""
fi

# === Counters / state =======================================================
ok_count=0
fail_count=0
total_count=0
declare -a FAILURES=()
seq=0  # call sequence — used in result filenames

# === Helpers ================================================================
die() { echo "${C_RED}ERROR:${C_RST} $*" >&2; exit 1; }
warn() { echo "${C_YEL}⚠${C_RST}  $*"; }
section() { echo; echo "${C_BLU}━━━ $* ━━━${C_RST}"; }

# email_to_token_name <email>
# Mirrors scripts/jwt/generate.sh's filename derivation EXACTLY:
#   sed 's/@.*//' | sed 's/[^a-zA-Z0-9_-]/-/g'
# Examples:
#   anjali@ithina.ai     → anjali
#   marcus.t@bucees.com  → marcus-t
#   a.kowalski@zabka.pl  → a-kowalski
#   o'brien@bucees.com   → o-brien   (apostrophe is non-alnum)
email_to_token_name() {
    local email="$1"
    local localpart="${email%%@*}"
    echo "$localpart" | sed 's/[^a-zA-Z0-9_-]/-/g'
}

# req <label> <expected_status> <jwt_path_or_empty> <method> <url>
# Performs the curl call, captures status + body, compares against expected.
# JWT is read from disk per-call so that a re-mint mid-run is honoured.
#
# Output format:
#   [✓ 200] GET /api/v1/tenants  — anjali_P__tenants__list
#         ↳ {"items":[{...}],"pagination":{...}}                  (compact one-liner, truncated 300)
#
#   [✗ 400, expected 200] GET /api/v1/platform-users?sort=nope  — anjali_P__plat_users__sort_email
#         ↳ {
#         ↳   "code": "INVALID_SORT_KEY",
#         ↳   ...
#         ↳ }
#         (saved: scripts/test_endpoints/results/.../020__...json)
#
# Successes get a compact one-liner so the eye can scan the matrix.
# Failures get pretty multi-line (capped at 30 lines) so the error envelope
# is immediately legible. Full body always saved to disk regardless.
req() {
    local label="$1"
    local expected="$2"
    local jwt_path="$3"
    local method="$4"
    local url="$5"

    seq=$((seq + 1))
    total_count=$((total_count + 1))
    local outfile
    outfile=$(printf "%s/%03d__%s.json" "$RESULTS_DIR" "$seq" "$label")

    local -a headers=("-H" "Accept: application/json")
    if [[ -n "$jwt_path" ]]; then
        local jwt
        jwt="$(cat "$jwt_path")"
        headers+=("-H" "Authorization: Bearer ${jwt}")
    fi

    local status
    status=$(curl -s -o "$outfile" -w "%{http_code}" \
        -X "$method" "${headers[@]}" "$url" 2>/dev/null || echo "000")

    # Path-only display: strip the configured BASE_URL prefix for brevity.
    local display_url="${url#"$BASE_URL"}"

    if [[ "$status" == "$expected" ]]; then
        echo "  ${C_GRN}[✓ ${status}]${C_RST} ${method} ${display_url}  ${C_DIM}— ${label}${C_RST}"
        ok_count=$((ok_count + 1))
        # Compact one-liner; jq -c collapses to single line. Truncate at 300
        # chars for terminal sanity. Fall back to cat for non-JSON bodies.
        local body_line
        body_line=$(jq -c '.' "$outfile" 2>/dev/null || cat "$outfile")
        if [[ ${#body_line} -gt 300 ]]; then
            body_line="${body_line:0:300}…"
        fi
        echo "        ${C_DIM}↳ ${body_line}${C_RST}"
    else
        echo "  ${C_RED}[✗ ${status}, expected ${expected}]${C_RST} ${method} ${display_url}  ${C_DIM}— ${label}${C_RST}"
        fail_count=$((fail_count + 1))
        FAILURES+=("${label} (got ${status}, expected ${expected}) → ${outfile}")
        # Pretty multi-line for failures; cap at 30 lines so an enormous
        # 5xx HTML page can't drown the summary. Full body always on disk.
        local body_lines body_count
        body_lines=$(jq '.' "$outfile" 2>/dev/null || cat "$outfile")
        body_count=$(printf '%s\n' "$body_lines" | wc -l)
        if [[ "$body_count" -gt 30 ]]; then
            printf '%s\n' "$body_lines" | head -30 | sed 's/^/        ↳ /'
            echo "        ${C_DIM}↳ ... (${body_count} lines total; see file)${C_RST}"
        else
            printf '%s\n' "$body_lines" | sed 's/^/        ↳ /'
        fi
        echo "        ${C_DIM}(saved: ${outfile})${C_RST}"
    fi
}

# email_exists_in_db <email1> <email2> ...
# Phase 0 pre-check: verify all 4 emails resolve to a row in either
# platform_users or tenant_users on the local DB. Single Python boot
# for all emails — fast and surfaces missing seed before JWT minting.
# Emails passed via TEST_HARNESS_EMAILS env (NUL-separated) so we don't
# splice shell-quoted strings into a Python literal.
email_exists_in_db() {
    local IFS=$'\n'
    TEST_HARNESS_EMAILS="$*" uv run python <<'PYEOF' 2>&1
import asyncio, os, sys
from admin_backend.config import get_settings
from admin_backend.db.engine import create_engine
from sqlalchemy import text

emails = [e for e in os.environ.get("TEST_HARNESS_EMAILS", "").split() if e]

async def main() -> int:
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.user_type', 'PLATFORM', false)")
            )
            missing = []
            for email in emails:
                r = await conn.execute(
                    text(
                        "SELECT 1 FROM platform_users WHERE email = :e "
                        "UNION ALL "
                        "SELECT 1 FROM tenant_users WHERE email = :e"
                    ),
                    {"e": email},
                )
                if r.first() is None:
                    missing.append(email)
            if missing:
                print(f"MISSING:{','.join(missing)}")
                return 1
            print("OK")
            return 0
    finally:
        await engine.dispose()

sys.exit(asyncio.run(main()))
PYEOF
}

# === Phase 0 — Pre-flight ===================================================
section "Phase 0 — pre-flight"

command -v jq   >/dev/null 2>&1 || die "jq not on PATH"
command -v curl >/dev/null 2>&1 || die "curl not on PATH"
[[ -x "$JWT_GEN" ]] || die "JWT_GEN not executable: ${JWT_GEN}"

# Server health (no JWT, public path).
health_status=$(curl -s -o /dev/null -w "%{http_code}" "${API}/health" 2>/dev/null || echo "000")
if [[ "$health_status" != "200" ]]; then
    die "server not reachable at ${API}/health (got ${health_status}). \
Start it first: \`uv run uvicorn admin_backend.main:app --host 0.0.0.0 --port 8000\`"
fi
echo "  ${C_GRN}✓${C_RST} server healthy at ${BASE_URL}"

# Email-existence pre-check — fail fast if seed data is missing.
echo "  · checking 4 emails exist in DB..."
db_check_output=$(email_exists_in_db \
    "$PLATFORM_EMAIL_1" "$PLATFORM_EMAIL_2" \
    "$TENANT_EMAIL_1"   "$TENANT_EMAIL_2")
if echo "$db_check_output" | grep -q "^MISSING:"; then
    missing=$(echo "$db_check_output" | grep "^MISSING:" | sed 's/^MISSING://')
    die "seed data missing — these emails do not resolve to a platform_users \
or tenant_users row: ${missing}. Re-seed: \`uv run python -m \
scripts.seed_dev_data --reset\`"
fi
echo "  ${C_GRN}✓${C_RST} 4 emails confirmed in DB"

# Warn (not bail) if both TENANT defaults are in the same tenant — discovery
# in Phase 3 surfaces this with the actual tenant_ids, but a quick warning
# now sets expectations.
if [[ "$TENANT_EMAIL_1" == "$TENANT_EMAIL_2" ]]; then
    warn "TENANT_EMAIL_1 and TENANT_EMAIL_2 are the same address; \
cross-tenant probes will be meaningless"
fi

mkdir -p "$RESULTS_DIR"
mkdir -p "$(dirname "$OPENAPI_DEST")"
echo "  ${C_GRN}✓${C_RST} results dir: ${RESULTS_DIR}"

# === Phase 1 — Save OpenAPI =================================================
section "Phase 1 — save OpenAPI spec"

# Fetch the spec and pretty-print through jq so the committed file stays
# diff-friendly. FastAPI emits compact JSON; without re-formatting we'd
# trash the file's git history on every run.
if ! curl -sf "${API}/openapi.json" | jq . > "${OPENAPI_DEST}.tmp"; then
    rm -f "${OPENAPI_DEST}.tmp"
    die "failed to fetch ${API}/openapi.json (server returned non-2xx) \
or jq failed to parse it"
fi
mv "${OPENAPI_DEST}.tmp" "$OPENAPI_DEST"
path_count=$(jq '.paths | length' < "$OPENAPI_DEST")
echo "  ${C_GRN}✓${C_RST} saved ${OPENAPI_DEST} (${path_count} paths)"
jq -r '.paths | keys[] | "    - " + .' < "$OPENAPI_DEST"

# === Phase 2 — Generate 4 JWTs ==============================================
section "Phase 2 — mint 4 JWTs"

mint_jwt() {
    local email="$1"
    if ! "$JWT_GEN" "$email" >/dev/null 2>&1; then
        echo "  ${C_RED}✗${C_RST} $JWT_GEN failed for ${email}; re-running with output:" >&2
        "$JWT_GEN" "$email" >&2 || true
        die "JWT mint failed for ${email}"
    fi
    local prefix
    prefix=$(email_to_token_name "$email")
    local path="${JWT_DIR}/${prefix}.jwt"
    [[ -s "$path" ]] || die "JWT file empty or missing after mint: ${path}"
    echo "$path"
}

P1_JWT=$(mint_jwt "$PLATFORM_EMAIL_1")
echo "  ${C_GRN}✓${C_RST} P1 ${PLATFORM_EMAIL_1} → ${P1_JWT}"
P2_JWT=$(mint_jwt "$PLATFORM_EMAIL_2")
echo "  ${C_GRN}✓${C_RST} P2 ${PLATFORM_EMAIL_2} → ${P2_JWT}"
T1_JWT=$(mint_jwt "$TENANT_EMAIL_1")
echo "  ${C_GRN}✓${C_RST} T1 ${TENANT_EMAIL_1} → ${T1_JWT}"
T2_JWT=$(mint_jwt "$TENANT_EMAIL_2")
echo "  ${C_GRN}✓${C_RST} T2 ${TENANT_EMAIL_2} → ${T2_JWT}"

# === Phase 3 — Discover fixture IDs (PLATFORM_1) ============================
section "Phase 3 — discover fixture IDs"

# PLATFORM sees all rows via D-29 (unconditional OR-branch on the 4 NOT NULL
# tenant_id tables). Use P1 to enumerate so cross-tenant references resolve.
setup_tenants="${RESULTS_DIR}/000__setup__tenants.json"
setup_tenant_users="${RESULTS_DIR}/000__setup__tenant_users.json"
setup_platform_users="${RESULTS_DIR}/000__setup__platform_users.json"
setup_roles="${RESULTS_DIR}/000__setup__roles.json"

curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/tenants?limit=100" -o "$setup_tenants"
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/tenant-users?limit=200" -o "$setup_tenant_users"
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/platform-users?limit=10" -o "$setup_platform_users"
# Step 6.1: also fetch roles list to discover a sample role_id for E3.
# Pick a TENANT-audience role (Owner) so both PLATFORM and TENANT callers
# can reference it; PLATFORM-audience roles would 404 for TENANT callers.
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/roles" -o "$setup_roles"

T1_TENANT_ID=$(jq -r --arg e "$TENANT_EMAIL_1" \
    '.items[] | select(.email==$e) | .tenant_id' \
    < "$setup_tenant_users" | head -1)
T1_USER_ID=$(jq -r --arg e "$TENANT_EMAIL_1" \
    '.items[] | select(.email==$e) | .id' \
    < "$setup_tenant_users" | head -1)
T2_TENANT_ID=$(jq -r --arg e "$TENANT_EMAIL_2" \
    '.items[] | select(.email==$e) | .tenant_id' \
    < "$setup_tenant_users" | head -1)
T2_USER_ID=$(jq -r --arg e "$TENANT_EMAIL_2" \
    '.items[] | select(.email==$e) | .id' \
    < "$setup_tenant_users" | head -1)
ANY_PLATFORM_USER_ID=$(jq -r '.items[0].id' < "$setup_platform_users")

# Step 6.1: discover role IDs for both audiences.
# OWNER is a TENANT-audience role (visible to all callers).
# SUPER_ADMIN is a PLATFORM-audience role (visible only to PLATFORM callers;
#   TENANT callers requesting it MUST 404 per RP3 invariant).
TENANT_ROLE_ID=$(jq -r '.tenant_roles.items[] | select(.code=="OWNER") | .id' \
    < "$setup_roles" | head -1)
PLATFORM_ROLE_ID=$(jq -r '.platform_roles.items[] | select(.code=="SUPER_ADMIN") | .id' \
    < "$setup_roles" | head -1)

for var in T1_TENANT_ID T1_USER_ID T2_TENANT_ID T2_USER_ID ANY_PLATFORM_USER_ID \
           TENANT_ROLE_ID PLATFORM_ROLE_ID; do
    val="${!var}"
    if [[ -z "$val" || "$val" == "null" ]]; then
        die "fixture discovery: ${var} resolved to empty/null. Setup files saved \
under ${RESULTS_DIR}/000__setup__*.json — inspect."
    fi
done
echo "  ${C_GRN}✓${C_RST} T1 (${TENANT_EMAIL_1}) tenant=${T1_TENANT_ID} user=${T1_USER_ID}"
echo "  ${C_GRN}✓${C_RST} T2 (${TENANT_EMAIL_2}) tenant=${T2_TENANT_ID} user=${T2_USER_ID}"
echo "  ${C_GRN}✓${C_RST} sample platform user id=${ANY_PLATFORM_USER_ID}"
echo "  ${C_GRN}✓${C_RST} OWNER role (TENANT) id=${TENANT_ROLE_ID}"
echo "  ${C_GRN}✓${C_RST} SUPER_ADMIN role (PLATFORM) id=${PLATFORM_ROLE_ID}"

if [[ "$T1_TENANT_ID" == "$T2_TENANT_ID" ]]; then
    warn "T1 and T2 are in the same tenant (${T1_TENANT_ID}). Cross-tenant \
probe rows will not assert real isolation; the rest of the matrix still runs."
fi

# === Phase 4 — Run the matrix ===============================================
#
# OWN/OTHER substitution per caller — read this before the matrix runs:
#
#   For PLATFORM callers (P1, P2):
#     OWN_TENANT_ID  = T1_TENANT_ID  (PLATFORM has no "own" tenant; we just
#     OWN_USER_ID    = T1_USER_ID     point at T1's values for symmetry).
#     OTHER_TENANT_ID = T2_TENANT_ID  Cross-tenant detail rows expect 200
#     OTHER_USER_ID   = T2_USER_ID    because PLATFORM sees both tenants
#                                     under D-29's OR-branch — the "OTHER"
#                                     concept is moot, but the matrix still
#                                     runs the call so we exercise the URL.
#
#   For TENANT_1 caller (T1):
#     OWN_TENANT_ID   = T1_TENANT_ID
#     OWN_USER_ID     = T1_USER_ID
#     OTHER_TENANT_ID = T2_TENANT_ID
#     OTHER_USER_ID   = T2_USER_ID
#     Cross-tenant detail rows expect 404 (RLS-as-404 per D-17).
#     Filter rows with ?tenant_id=OTHER_TENANT_ID expect 200 with empty
#     items (RLS intersects with the explicit filter to empty, NOT 403).
#
#   For TENANT_2 caller (T2):
#     OWN_TENANT_ID   = T2_TENANT_ID
#     OWN_USER_ID     = T2_USER_ID
#     OTHER_TENANT_ID = T1_TENANT_ID
#     OTHER_USER_ID   = T1_USER_ID
#     Same expectations as T1 with the tenant pair swapped.
#
# RBAC AUDIENCE-FILTER (Step 6.1):
#   /roles, /permission-matrix, and /roles/{id}/permissions apply an
#   APP-LAYER audience filter when the caller is TENANT — only roles
#   with audience='TENANT' are visible. PLATFORM callers see both
#   audiences. The matrix exercises this with two role-detail probes
#   per caller: one against TENANT_ROLE_ID (always 200) and one against
#   PLATFORM_ROLE_ID (200 for PLATFORM caller, 404 for TENANT caller).

section "Phase 4 — run matrix"

# ---- Public / no-auth (run once, not per user) ----
section "Public + no-auth"
req "public__health"        200 ""        GET "${API}/health"
req "public__ready"         200 ""        GET "${API}/ready"
req "public__openapi"       200 ""        GET "${API}/openapi.json"
req "noauth__tenants_401"   401 ""        GET "${API}/tenants"

# ---- Per-user matrix runner ----
# Args: caller_label  jwt_path  caller_kind  own_tenant  own_user  other_tenant  other_user
#   caller_kind: "PLATFORM" or "TENANT"
run_matrix_for_caller() {
    local label="$1" jwt="$2" kind="$3"
    local own_tenant="$4" own_user="$5" other_tenant="$6" other_user="$7"

    section "${kind} caller — ${label}  (own_tenant=${own_tenant})"

    # --- /lookups (multi-user-type; T_other moot for batch endpoints) ---
    req "${label}__lookups__all" 200 "$jwt" GET \
        "${API}/lookups?lists=tenant_tier,tenant_region,tenant_status,tenant_industry,module_code,country"
    req "${label}__lookups__empty_param" 200 "$jwt" GET "${API}/lookups?lists="
    req "${label}__lookups__unknown_list" 200 "$jwt" GET "${API}/lookups?lists=does_not_exist"

    # --- /tenants list + filters + stats ---
    # NOTE: /tenants list does NOT take `sort`; only search/tier/limit/offset.
    # Drop-in correction relative to the original prompt's matrix.
    req "${label}__tenants__list"          200 "$jwt" GET "${API}/tenants"
    req "${label}__tenants__list_limit2"   200 "$jwt" GET "${API}/tenants?limit=2&offset=0"
    req "${label}__tenants__list_offset2"  200 "$jwt" GET "${API}/tenants?limit=2&offset=2"
    req "${label}__tenants__search_buc"    200 "$jwt" GET "${API}/tenants?search=Buc"
    req "${label}__tenants__tier_smb"      200 "$jwt" GET "${API}/tenants?tier=SMB"
    req "${label}__tenants__stats"         200 "$jwt" GET "${API}/tenants/stats"
    # detail_own: PLATFORM 200, TENANT 200 (own tenant)
    req "${label}__tenants__detail_own"    200 "$jwt" GET "${API}/tenants/${own_tenant}"
    # detail_cross: PLATFORM 200 (sees all), TENANT 404 (D-17 RLS-as-404)
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__tenants__detail_cross" 200 "$jwt" GET "${API}/tenants/${other_tenant}"
    else
        req "${label}__tenants__detail_cross" 404 "$jwt" GET "${API}/tenants/${other_tenant}"
    fi
    req "${label}__tenants__detail_unknown" 404 "$jwt" GET "${API}/tenants/${UNKNOWN_UUID}"

    # --- /platform-users (PLATFORM-only gate; TENANT gets 403 PERMISSION_DENIED) ---
    # The gate runs BEFORE sort validation, so even ?sort=nope returns 403 for TENANT.
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__plat_users__list"           200 "$jwt" GET "${API}/platform-users"
        req "${label}__plat_users__list_active"    200 "$jwt" GET "${API}/platform-users?status=ACTIVE"
        req "${label}__plat_users__search"         200 "$jwt" GET "${API}/platform-users?search=an"
        req "${label}__plat_users__sort_email"     200 "$jwt" GET "${API}/platform-users?sort=email_asc"
        req "${label}__plat_users__pagination"     200 "$jwt" GET "${API}/platform-users?limit=2&offset=0"
        req "${label}__plat_users__invalid_sort"   400 "$jwt" GET "${API}/platform-users?sort=nope"
        req "${label}__plat_users__detail"         200 "$jwt" GET "${API}/platform-users/${ANY_PLATFORM_USER_ID}"
        req "${label}__plat_users__detail_unknown" 404 "$jwt" GET "${API}/platform-users/${UNKNOWN_UUID}"
    else
        req "${label}__plat_users__list"           403 "$jwt" GET "${API}/platform-users"
        req "${label}__plat_users__list_active"    403 "$jwt" GET "${API}/platform-users?status=ACTIVE"
        req "${label}__plat_users__search"         403 "$jwt" GET "${API}/platform-users?search=an"
        req "${label}__plat_users__sort_email"     403 "$jwt" GET "${API}/platform-users?sort=email_asc"
        req "${label}__plat_users__pagination"     403 "$jwt" GET "${API}/platform-users?limit=2&offset=0"
        # gate-before-validation: ?sort=nope still returns 403 for TENANT, not 400.
        req "${label}__plat_users__invalid_sort"   403 "$jwt" GET "${API}/platform-users?sort=nope"
        req "${label}__plat_users__detail"         403 "$jwt" GET "${API}/platform-users/${ANY_PLATFORM_USER_ID}"
        req "${label}__plat_users__detail_unknown" 403 "$jwt" GET "${API}/platform-users/${UNKNOWN_UUID}"
    fi

    # --- /tenant-users (multi-user-type; RLS scopes per D-29 / FN-AB-14) ---
    req "${label}__tu__list"                200 "$jwt" GET "${API}/tenant-users"
    req "${label}__tu__list_active"         200 "$jwt" GET "${API}/tenant-users?status=ACTIVE"
    req "${label}__tu__search"              200 "$jwt" GET "${API}/tenant-users?search=a"
    req "${label}__tu__sort_email"          200 "$jwt" GET "${API}/tenant-users?sort=email_asc"
    req "${label}__tu__pagination"          200 "$jwt" GET "${API}/tenant-users?limit=2&offset=0"
    req "${label}__tu__invalid_sort"        400 "$jwt" GET "${API}/tenant-users?sort=nope"
    req "${label}__tu__filter_own_tenant"   200 "$jwt" GET "${API}/tenant-users?tenant_id=${own_tenant}"
    # filter_other_tenant: 200 with possibly-empty items (RLS intersects filter).
    req "${label}__tu__filter_other_tenant" 200 "$jwt" GET "${API}/tenant-users?tenant_id=${other_tenant}"
    req "${label}__tu__detail_own"          200 "$jwt" GET "${API}/tenant-users/${own_user}"
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__tu__detail_cross"    200 "$jwt" GET "${API}/tenant-users/${other_user}"
    else
        req "${label}__tu__detail_cross"    404 "$jwt" GET "${API}/tenant-users/${other_user}"
    fi
    req "${label}__tu__detail_unknown"      404 "$jwt" GET "${API}/tenant-users/${UNKNOWN_UUID}"

    # --- /roles list (Step 6.1: multi-user-type with app-layer audience filter) ---
    # E1: pre-grouped response {platform_roles, tenant_roles}. TENANT JWT
    # gets platform_roles.total=0; PLATFORM gets all 15 roles.
    req "${label}__roles__list"             200 "$jwt" GET "${API}/roles"
    req "${label}__roles__filter_status"    200 "$jwt" GET "${API}/roles?status=ACTIVE"
    req "${label}__roles__filter_q"         200 "$jwt" GET "${API}/roles?q=manager"
    req "${label}__roles__filter_is_system" 200 "$jwt" GET "${API}/roles?is_system=true"
    req "${label}__roles__sort_name"        200 "$jwt" GET "${API}/roles?sort=name_asc"
    req "${label}__roles__pagination"       200 "$jwt" GET "${API}/roles?limit=5&offset=0"
    req "${label}__roles__invalid_sort"     400 "$jwt" GET "${API}/roles?sort=nope"

    # --- /roles/{id}/permissions (Step 6.1: E3, parent-echo response shape) ---
    # E3: TENANT-audience role visible to all callers.
    req "${label}__role_perms__tenant_role" 200 "$jwt" GET "${API}/roles/${TENANT_ROLE_ID}/permissions"
    # E3: PLATFORM-audience role; TENANT caller MUST 404 (RP3 invariant —
    # audience filter applied at the role lookup, not just the list).
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__role_perms__platform_role" 200 "$jwt" GET "${API}/roles/${PLATFORM_ROLE_ID}/permissions"
    else
        req "${label}__role_perms__platform_role" 404 "$jwt" GET "${API}/roles/${PLATFORM_ROLE_ID}/permissions"
    fi
    req "${label}__role_perms__unknown"     404 "$jwt" GET "${API}/roles/${UNKNOWN_UUID}/permissions"

    # --- /roles/{id} (Step 6.18.2: E7, self-contained role detail) ---
    # Same audience-gate as E3: TENANT-audience role visible to all
    # callers; PLATFORM-audience role yields 404 for TENANT callers
    # (LD5 RLS-as-404 per D-17).
    req "${label}__role_detail__tenant_role"     200 "$jwt" GET "${API}/roles/${TENANT_ROLE_ID}"
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__role_detail__platform_role" 200 "$jwt" GET "${API}/roles/${PLATFORM_ROLE_ID}"
    else
        req "${label}__role_detail__platform_role" 404 "$jwt" GET "${API}/roles/${PLATFORM_ROLE_ID}"
    fi
    req "${label}__role_detail__unknown"         404 "$jwt" GET "${API}/roles/${UNKNOWN_UUID}"

    # --- /permissions catalogue (Step 6.1: E2, no audience filter) ---
    # Both PLATFORM and TENANT see the full catalogue (catalogue is
    # reference data; the matrix UI needs every row regardless of who
    # can be assigned them).
    req "${label}__perms__list"             200 "$jwt" GET "${API}/permissions"
    req "${label}__perms__filter_module"    200 "$jwt" GET "${API}/permissions?module=ADMIN"
    req "${label}__perms__filter_scope"     200 "$jwt" GET "${API}/permissions?scope=TENANT"
    req "${label}__perms__sort_code"        200 "$jwt" GET "${API}/permissions?sort=code_asc"
    req "${label}__perms__pagination"       200 "$jwt" GET "${API}/permissions?limit=10&offset=0"
    req "${label}__perms__invalid_sort"     400 "$jwt" GET "${API}/permissions?sort=nope"

    # --- /permission-matrix (Step 6.1: E6, render-ready grid) ---
    # E6: TENANT caller's response has fewer columns than PLATFORM (audience
    # filter on the roles[] array). Body assertion would verify col count
    # but smoke just confirms 200; full assertions are in pytest.
    req "${label}__matrix"                  200 "$jwt" GET "${API}/permission-matrix"

# --- /module-access (Step 6.7: multi-user-type with RLS scoping) ---
    # /modules: 6 cards under PLATFORM, 6 cards collapsed under TENANT (all
    # total_active_trial_tenants=1, enabled_count is 0 or 1 per card).
    # /matrix: N rows under PLATFORM, exactly 1 row under TENANT (own only).
    req "${label}__ma__modules"                200 "$jwt" GET "${API}/module-access/modules"
    req "${label}__ma__matrix"                 200 "$jwt" GET "${API}/module-access/matrix"
    req "${label}__ma__matrix_limit"           200 "$jwt" GET "${API}/module-access/matrix?limit=2&offset=0"
    req "${label}__ma__matrix_sort_name"       200 "$jwt" GET "${API}/module-access/matrix?sort=name_asc"
    req "${label}__ma__matrix_sort_tier_desc"  200 "$jwt" GET "${API}/module-access/matrix?sort=tier_desc"
    req "${label}__ma__matrix_filter_tier"     200 "$jwt" GET "${API}/module-access/matrix?tier=ENTERPRISE"
    req "${label}__ma__matrix_filter_status"   200 "$jwt" GET "${API}/module-access/matrix?status=ACTIVE"
    req "${label}__ma__matrix_search"          200 "$jwt" GET "${API}/module-access/matrix?q=buc"
    req "${label}__ma__matrix_invalid_sort"    400 "$jwt" GET "${API}/module-access/matrix?sort=nope"

# --- /role-assignments (Step 6.8.3: grouped envelope, multi-user-type) ---
    # PLATFORM JWT sees both blocks populated; TENANT JWT sees only
    # tenant_assignments (platform-side short-circuited at the router
    # — security-load-bearing per locked decision 12). Both blocks
    # carry their own {items, pagination}. Filters apply per-block:
    # platform_user_id only narrows platform-side; tenant_user_id /
    # org_node_id / tenant_id only narrow tenant-side.
    req "${label}__ra__list"                   200 "$jwt" GET "${API}/role-assignments"
    req "${label}__ra__list_limit"             200 "$jwt" GET "${API}/role-assignments?limit=5"
    req "${label}__ra__filter_status_active"   200 "$jwt" GET "${API}/role-assignments?status=ACTIVE"
    req "${label}__ra__invalid_sort"           400 "$jwt" GET "${API}/role-assignments?sort=nope"

    # --- /me/* (Step 6.9.2: multi-user-type; caller-state endpoints) ---
    # /me/permissions: caller's full grant set; always an array.
    # /me/can-do: server-authoritative single-permission check. The probed
    # tuple (ADMIN.USERS.VIEW.TENANT) exists in seed and is held by both
    # SUPER_ADMIN (PLATFORM) and OWNER (TENANT) roles — but allowed=true
    # vs false depends on the specific caller's role assignments; smoke
    # only asserts 200. The `allowed` boolean is verified in pytest.
    req "${label}__me__permissions"            200 "$jwt" GET "${API}/me/permissions"
    req "${label}__me__can_do"                 200 "$jwt" GET "${API}/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=TENANT"

    # --- /stores (Step 6.17.2: multi-user-type with RLS scoping) ---
    # PLATFORM sees all rows via D-29 OR-branch; TENANT sees own-tenant
    # only (RLS). Both callers reach the gate via ADMIN.STORES.VIEW.TENANT:
    # SUPER_ADMIN cascades from .GLOBAL, OWNER holds .TENANT directly per
    # Step 6.17.1's catalogue extension. Detail on UNKNOWN_UUID surfaces
    # as 404 STORE_NOT_FOUND from the anchor dep miss (RLS-as-404 per
    # D-17 / F-THREADING-4 — anchor dep raises rather than returning
    # None, which would short-circuit has_permission's cascade).
    req "${label}__stores__list"               200 "$jwt" GET "${API}/stores"
    req "${label}__stores__detail_unknown"     404 "$jwt" GET "${API}/stores/${UNKNOWN_UUID}"

    # --- /audit/activities (Step 6.16.3: multi-user-type with RLS scoping) ---
    # PLATFORM sees merged UNION across both audit tables; TENANT sees
    # only own-tenant rows (tenant_activity_audit_logs, RLS-scoped).
    # Both callers gated on ADMIN.AUDIT_LOG.VIEW.TENANT: SUPER_ADMIN +
    # PLATFORM_ADMIN + SUPPORT_ADMIN cascade from .VIEW.GLOBAL; tenant
    # roles with .VIEW.TENANT (OWNER, etc.) pass via direct grant.
    # Cursor pagination (newest first); limit + cursor + filter
    # parameters; malformed cursor surfaces as 422 INVALID_CURSOR.
    # Detail on UNKNOWN_UUID returns 404 AUDIT_EVENT_NOT_FOUND.
    req "${label}__audit__list"                200 "$jwt" GET "${API}/audit/activities?limit=5"
    req "${label}__audit__list_status_filter"  200 "$jwt" GET "${API}/audit/activities?limit=5&status=SUCCESS"
    req "${label}__audit__malformed_cursor"    422 "$jwt" GET "${API}/audit/activities?cursor=not-valid-json"
    req "${label}__audit__detail_unknown"      404 "$jwt" GET "${API}/audit/activities/${UNKNOWN_UUID}"
}

# Each PLATFORM caller gets T1's values as "own" and T2's as "other"; the
# OTHER concept is moot for PLATFORM but the matrix still hits the URL.
P1_PREFIX=$(email_to_token_name "$PLATFORM_EMAIL_1")
P2_PREFIX=$(email_to_token_name "$PLATFORM_EMAIL_2")
T1_PREFIX=$(email_to_token_name "$TENANT_EMAIL_1")
T2_PREFIX=$(email_to_token_name "$TENANT_EMAIL_2")

run_matrix_for_caller "${P1_PREFIX}_P" "$P1_JWT" PLATFORM \
    "$T1_TENANT_ID" "$T1_USER_ID" "$T2_TENANT_ID" "$T2_USER_ID"
run_matrix_for_caller "${P2_PREFIX}_P" "$P2_JWT" PLATFORM \
    "$T1_TENANT_ID" "$T1_USER_ID" "$T2_TENANT_ID" "$T2_USER_ID"
run_matrix_for_caller "${T1_PREFIX}_T" "$T1_JWT" TENANT \
    "$T1_TENANT_ID" "$T1_USER_ID" "$T2_TENANT_ID" "$T2_USER_ID"
run_matrix_for_caller "${T2_PREFIX}_T" "$T2_JWT" TENANT \
    "$T2_TENANT_ID" "$T2_USER_ID" "$T1_TENANT_ID" "$T1_USER_ID"

# === Phase 4b — Step 6.11.2 tenants write flow ==============================
# 5 outside-matrix entries: POST + PATCH + /suspend + /activate happy path
# (PLATFORM-1 caller), plus 1 TENANT audience-deny on POST. The matrix runs
# read-only; the write flow runs ONCE so it doesn't multiply into 4-caller
# duplicates. Names are UUID-suffixed for re-run safety; each run leaks one
# tenant in ACTIVE state (operator cleanup if running repeatedly).
#
# These calls use inline curl rather than the ``req`` helper because the
# helper accepts a JWT FILE PATH and no body-bearing args; the write flow
# needs ``-d '<json>'`` for POST/PATCH bodies. Status counters incremented
# inline so the summary still reflects the writes.

section "Step 6.11.2 — tenants write flow"

WRITE_SUFFIX="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
WRITE_NAME="te-${WRITE_SUFFIX}"
P1_JWT_VALUE="$(cat "$P1_JWT")"
T1_JWT_VALUE="$(cat "$T1_JWT")"

# Inline shim for body-bearing calls. Increments counters; mirrors req's
# success / failure formatting.
write_req() {
    local label="$1" expected="$2" jwt="$3" method="$4" url="$5"
    local body="${6:-}"

    seq=$((seq + 1))
    total_count=$((total_count + 1))
    local outfile
    outfile=$(printf "%s/%03d__%s.json" "$RESULTS_DIR" "$seq" "$label")

    local -a curl_args=(
        -s -o "$outfile" -w "%{http_code}"
        -X "$method"
        -H "Accept: application/json"
        -H "Authorization: Bearer ${jwt}"
    )
    if [[ -n "$body" ]]; then
        curl_args+=(-H "Content-Type: application/json" -d "$body")
    fi
    curl_args+=("$url")

    local status
    status="$(curl "${curl_args[@]}" 2>/dev/null || echo "000")"
    local display_url="${url#"$BASE_URL"}"

    if [[ "$status" == "$expected" ]]; then
        echo "  ${C_GRN}[✓ ${status}]${C_RST} ${method} ${display_url}  ${C_DIM}— ${label}${C_RST}"
        ok_count=$((ok_count + 1))
    else
        echo "  ${C_RED}[✗ ${status}, expected ${expected}]${C_RST} ${method} ${display_url}  ${C_DIM}— ${label}${C_RST}"
        fail_count=$((fail_count + 1))
        FAILURES+=("${label} (got ${status}, expected ${expected}) → ${outfile}")
        jq '.' "$outfile" 2>/dev/null | head -10 | sed 's/^/        ↳ /' || cat "$outfile"
    fi
}

CREATE_BODY="$(cat <<EOF
{
  "name": "${WRITE_NAME}",
  "region": "US",
  "tier": "ENTERPRISE",
  "industry": "GROCERY",
  "country": "United States",
  "primary_contact_name": "Test Endpoint Operator",
  "contact_email": "te-${WRITE_SUFFIX}@test.example.com",
  "number_of_stores": 5,
  "number_of_stores_as_of_date": "2026-01-01"
}
EOF
)"

write_req "write_flow__create" 201 "$P1_JWT_VALUE" POST "${API}/tenants" "$CREATE_BODY"

# Re-read the just-created tenant id from the saved create response.
CREATE_OUTFILE=$(printf "%s/%03d__write_flow__create.json" "$RESULTS_DIR" "$seq")
WRITE_TENANT_ID="$(jq -r '.id // empty' < "$CREATE_OUTFILE" 2>/dev/null || echo "")"

if [[ -n "$WRITE_TENANT_ID" ]]; then
    # Step 6.20.1: POST -> GET roundtrip. Pre-fix the GET returned 404
    # because POST did not provision a tenant-root org_node.
    write_req "write_flow__post_get_roundtrip" 200 "$P1_JWT_VALUE" GET \
        "${API}/tenants/${WRITE_TENANT_ID}" ""
    write_req "write_flow__patch"    200 "$P1_JWT_VALUE" PATCH \
        "${API}/tenants/${WRITE_TENANT_ID}" \
        '{"primary_contact_name":"TE patched"}'
    write_req "write_flow__suspend"  200 "$P1_JWT_VALUE" POST \
        "${API}/tenants/${WRITE_TENANT_ID}/suspend" ""
    write_req "write_flow__activate" 200 "$P1_JWT_VALUE" POST \
        "${API}/tenants/${WRITE_TENANT_ID}/activate" ""
fi

write_req "write_flow__audience_deny" 403 "$T1_JWT_VALUE" POST "${API}/tenants" \
    '{"name":"tenant-jwt-should-not-land","region":"US","tier":"ENTERPRISE","industry":"GROCERY","country":"United States","primary_contact_name":"X","contact_email":"x@test.example.com","number_of_stores":1,"number_of_stores_as_of_date":"2026-01-01"}'

# === Phase 4c — Step 6.10.1 tenant-users write flow =========================
# 5 outside-matrix entries: POST + PATCH + /suspend (409) + /activate (409)
# happy / expected-409 path under PLATFORM-1 caller, plus 1 TENANT self-edit
# deny. Suspend/activate against the freshly-created INVITED user are
# expected to return 409 INVALID_STATE_TRANSITION: the 200 happy paths need
# an ACTIVE user, which the smoke chain can't promote without DB access
# (covered by integration tests S1/S2/A1/A2). The endpoints still fire end
# to end — gate, anchor, repo, transition matrix.
#
# Names UUID-suffixed for re-run safety; each run creates one tenant_user
# left in INVITED state (operator cleanup if running repeatedly).

section "Step 6.10.1 — tenant-users write flow"

# Resolve a TENANT_ID + TENANT-audience ROLE_ID for the create body. Use
# T1_TENANT_ID (already captured) and look up the OWNER role from the
# seeded catalogue (deterministic across local + cloud).
TU_OWNER_ROLE_ID="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
    "${API}/roles?limit=50" 2>/dev/null \
    | jq -r '.tenant_roles.items[] | select(.code == "OWNER") | .id' \
    | head -n1)"

if [[ -z "$TU_OWNER_ROLE_ID" || "$TU_OWNER_ROLE_ID" == "null" ]]; then
    warn "Could not resolve OWNER role_id; tenant-users write flow skipped"
else
    # Step 6.14: resolve two distinct anchor org_nodes from the
    # tenant's org-tree. ``tree[0].id`` (HQ-level) and
    # ``tree[0].children[0].id`` (REGION-level descendant) give two
    # acceptable anchors.
    TU_TREE_RESP="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
        "${API}/tenants/${T1_TENANT_ID}/org-tree" 2>/dev/null)"
    TU_ANCHOR_A="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tree[0].id // empty')"
    TU_ANCHOR_B="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tree[0].children[0].id // .tree[0].id // empty')"

    # Step 6.21.1: assert the new top-level fields land in the same
    # response. ``tenant_root_id`` is the org_nodes.id of the
    # tenant-root (distinct from tenant_id); frontend uses it as
    # ``parent_id`` on POST /org-tree under the synthesized TENANT row.
    TU_TENANT_ROOT_ID="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tenant_root_id // empty')"
    TU_TENANT_ROOT_CODE="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tenant_root_code // empty')"
    TU_TENANT_ROOT_PATH="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tenant_root_path // empty')"
    total_count=$((total_count + 1))
    if [[ -n "$TU_TENANT_ROOT_ID" \
          && "$TU_TENANT_ROOT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ \
          && -n "$TU_TENANT_ROOT_CODE" \
          && -n "$TU_TENANT_ROOT_PATH" ]]; then
        echo "  ${C_GRN}[✓ 200]${C_RST} GET /tenants/{id}/org-tree carries tenant_root_id/code/path  ${C_DIM}— org_tree__tenant_root_fields${C_RST}"
        ok_count=$((ok_count + 1))
    else
        echo "  ${C_RED}[✗ id=${TU_TENANT_ROOT_ID:-null} code=${TU_TENANT_ROOT_CODE:-null} path=${TU_TENANT_ROOT_PATH:-null}]${C_RST} GET /org-tree missing tenant_root_* fields  ${C_DIM}— org_tree__tenant_root_fields${C_RST}"
        fail_count=$((fail_count + 1))
        FAILURES+=("org_tree__tenant_root_fields (id=${TU_TENANT_ROOT_ID:-null})")
    fi

    if [[ -z "$TU_ANCHOR_A" ]]; then
        warn "Could not resolve org_node anchor; tenant-users write flow skipped"
    else
        TU_SUFFIX="$(uuidgen 2>/dev/null \
            || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
        TU_CREATE_BODY="$(cat <<EOF
{
  "tenant_id": "${T1_TENANT_ID}",
  "email": "te-tu-${TU_SUFFIX}@test.example.com",
  "full_name": "TE TU ${TU_SUFFIX}",
  "roles": [{"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${TU_ANCHOR_A}"}]
}
EOF
)"
        write_req "tu_flow__create" 201 "$P1_JWT_VALUE" POST \
            "${API}/tenant-users" "$TU_CREATE_BODY"

        TU_CREATE_OUTFILE=$(printf "%s/%03d__tu_flow__create.json" \
            "$RESULTS_DIR" "$seq")
        WRITE_TU_ID="$(jq -r '.id // empty' < "$TU_CREATE_OUTFILE" 2>/dev/null || echo "")"

        if [[ -n "$WRITE_TU_ID" ]]; then
            write_req "tu_flow__patch" 200 "$P1_JWT_VALUE" PATCH \
                "${API}/tenant-users/${WRITE_TU_ID}" \
                '{"full_name":"TE TU patched"}'
            # INVITED -> SUSPENDED structurally rejected by
            # ck_tenant_users_auth0_sub_consistency; mapped to 409 by
            # the app layer. The 200 happy path is covered by tests.
            write_req "tu_flow__suspend_invited_409" 409 "$P1_JWT_VALUE" POST \
                "${API}/tenant-users/${WRITE_TU_ID}/suspend" ""
            # INVITED -> ACTIVE is the Auth0 invite-accept flow
            # (Stage 3); /activate refuses to take that path; 409.
            write_req "tu_flow__activate_invited_409" 409 "$P1_JWT_VALUE" POST \
                "${API}/tenant-users/${WRITE_TU_ID}/activate" ""
        fi

        # Step 6.14 additions: multi-anchor POST, diff-replace PATCH,
        # invalid_org_node POST. Each across SUPER_ADMIN (P1, expected
        # happy), PLATFORM_ADMIN (P2 — passes via cascade), OWNER
        # (T1, own tenant — happy), ADMIN (T2 — 403 PERMISSION_DENIED;
        # ADMIN role doesn't hold ADMIN.USERS.CONFIGURE.TENANT).
        #
        # We exercise SUPER_ADMIN happy + ADMIN 403 here; the other
        # callers' behavior on this gate is already covered by the
        # in-matrix /tenant-users entries above plus the integration
        # test suite.

        # 1. Multi-anchor POST (SUPER_ADMIN). 2 distinct anchors and
        #    2 distinct roles (the OWNER role twice would still be
        #    valid Pattern B, but two roles makes the create's
        #    diff-replace output more verifiable).
        TU14_SUFFIX="$(uuidgen 2>/dev/null \
            || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
        TU14_ROLE2="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
            "${API}/roles?limit=50" 2>/dev/null \
            | jq -r '.tenant_roles.items[] | select(.code != "OWNER") | .id' \
            | head -n1)"
        [[ -z "$TU14_ROLE2" || "$TU14_ROLE2" == "null" ]] && TU14_ROLE2="$TU_OWNER_ROLE_ID"

        TU14_CREATE_BODY="$(cat <<EOF
{
  "tenant_id": "${T1_TENANT_ID}",
  "email": "te-tu14-${TU14_SUFFIX}@test.example.com",
  "full_name": "TE TU14 ${TU14_SUFFIX}",
  "roles": [
    {"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${TU_ANCHOR_A}"},
    {"role_id": "${TU14_ROLE2}", "org_node_id": "${TU_ANCHOR_B}"}
  ]
}
EOF
)"
        write_req "tu14_flow__post_multi" 201 "$P1_JWT_VALUE" POST \
            "${API}/tenant-users" "$TU14_CREATE_BODY"
        TU14_OUTFILE=$(printf "%s/%03d__tu14_flow__post_multi.json" \
            "$RESULTS_DIR" "$seq")
        TU14_USER_ID="$(jq -r '.id // empty' < "$TU14_OUTFILE" 2>/dev/null || echo "")"

        if [[ -n "$TU14_USER_ID" ]]; then
            # 2. Diff-replace PATCH: keep (OWNER, A); replace
            #    (ROLE2, B) with (OWNER, B). 1 unchanged + 1 revoke +
            #    1 grant.
            write_req "tu14_flow__patch_diff" 200 "$P1_JWT_VALUE" PATCH \
                "${API}/tenant-users/${TU14_USER_ID}" \
                "$(cat <<EOF
{
  "roles": [
    {"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${TU_ANCHOR_A}"},
    {"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${TU_ANCHOR_B}"}
  ]
}
EOF
)"
        fi

        # 3. POST with bogus org_node_id -> 422 INVALID_ORG_NODE.
        MISSING_ANCHOR="$(uuidgen 2>/dev/null \
            || python3 -c 'import uuid;print(uuid.uuid4())')"
        write_req "tu14_flow__post_invalid_org_node" 422 "$P1_JWT_VALUE" POST \
            "${API}/tenant-users" \
            "$(cat <<EOF
{
  "tenant_id": "${T1_TENANT_ID}",
  "email": "te-tu14-bad-${TU14_SUFFIX}@test.example.com",
  "full_name": "TE TU14 Bad",
  "roles": [{"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${MISSING_ANCHOR}"}]
}
EOF
)"

        # 4. ADMIN role doesn't hold ADMIN.USERS.CONFIGURE.TENANT ->
        #    403 PERMISSION_DENIED on POST. T2_JWT is the ADMIN-role
        #    TENANT JWT (see top-of-script JWT minting).
        if [[ -n "${T2_JWT_VALUE:-}" ]]; then
            write_req "tu14_flow__post_admin_role_denied" 403 \
                "$T2_JWT_VALUE" POST "${API}/tenant-users" \
                "$(cat <<EOF
{
  "tenant_id": "${T2_TENANT_ID:-${T1_TENANT_ID}}",
  "email": "te-tu14-admin-${TU14_SUFFIX}@test.example.com",
  "full_name": "TE TU14 ADMIN Caller",
  "roles": [{"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${TU_ANCHOR_A}"}]
}
EOF
)"
        fi
    fi
fi

# TENANT-1 OWNER patching self -> 403 SELF_EDIT_FORBIDDEN. T1_USER_ID is
# already T1_JWT's user_id by construction (captured at JWT mint time).
write_req "tu_flow__self_edit_deny" 403 "$T1_JWT_VALUE" PATCH \
    "${API}/tenant-users/${T1_USER_ID}" \
    '{"full_name":"Trying To Edit Self"}'

# === Phase 4d — Step 6.15 module-access write flow ==========================
# Idempotent enable/disable surface, PLATFORM-only. 6 outside-matrix
# entries:
#
#   1. enable upsert (missing -> 200 + new row)
#   2. enable no-op (ENABLED -> ENABLED, 200, row unchanged)
#   3. disable flip (ENABLED -> DISABLED, 200)
#   4. disable no-op (DISABLED -> DISABLED, 200)
#   5. disable on a different missing module (404 MODULE_ACCESS_NOT_FOUND)
#   6. PLATFORM_ADMIN refusal under T1 (TENANT) JWT -> 403
#      PLATFORM_AUDIENCE_REQUIRED
#
# Re-run safety: each run leaks one DISABLED row per script invocation.
# (tenant_id, module) is the uniqueness arbiter, so re-running mutates
# the same row pair rather than accumulating.

section "Step 6.15 — module-access write flow"

# Pick a seeded tenant with 2+ unused module codes AND a tenant-root
# org_node. T1_TENANT_ID is the TENANT JWT's tenant; we want PLATFORM
# operations against a tenant whose anchor reachability is confirmed.
# Buc-ee's has 5/5 modules; the loop steps past it to the next.
MA_TENANT_ID=""
MA_MODULE=""
MA_OTHER_MODULE=""

MA_TENANT_IDS="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
    "${API}/tenants?limit=50" 2>/dev/null \
    | jq -r '.items[].id' 2>/dev/null)"

while IFS= read -r tid; do
    [[ -z "$tid" ]] && continue
    ANCHOR_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${P1_JWT_VALUE}" \
        "${API}/tenants/${tid}/org-tree" 2>/dev/null)"
    [[ "$ANCHOR_STATUS" == "200" ]] || continue

    EXISTING="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
        "${API}/tenants/${tid}" 2>/dev/null \
        | jq -r '.modules[].code' 2>/dev/null | sort -u)"
    CAND1=""
    CAND2=""
    for c in GOAL_CONSOLE PROMOTIONS_ASSISTANT PERISHABLES_ASSISTANT PRICING_OS; do
        if ! grep -qxF "$c" <<< "$EXISTING"; then
            if [[ -z "$CAND1" ]]; then CAND1="$c"
            elif [[ -z "$CAND2" ]]; then CAND2="$c"; break
            fi
        fi
    done

    if [[ -n "$CAND1" && -n "$CAND2" ]]; then
        MA_TENANT_ID="$tid"
        MA_MODULE="$CAND1"
        MA_OTHER_MODULE="$CAND2"
        break
    fi
done <<< "$MA_TENANT_IDS"

if [[ -z "$MA_TENANT_ID" ]]; then
    warn "module-access write flow skipped — no tenant with 2+ unused modules"
else
    MA_ENABLE_URL="${API}/module-access/${MA_TENANT_ID}/${MA_MODULE}/enable"
    MA_DISABLE_URL="${API}/module-access/${MA_TENANT_ID}/${MA_MODULE}/disable"
    MA_MISSING_DISABLE_URL="${API}/module-access/${MA_TENANT_ID}/${MA_OTHER_MODULE}/disable"

    write_req "ma_flow__enable_upsert"     200 "$P1_JWT_VALUE" POST "$MA_ENABLE_URL"  ""
    write_req "ma_flow__enable_noop"       200 "$P1_JWT_VALUE" POST "$MA_ENABLE_URL"  ""
    write_req "ma_flow__disable_flip"      200 "$P1_JWT_VALUE" POST "$MA_DISABLE_URL" ""
    write_req "ma_flow__disable_noop"      200 "$P1_JWT_VALUE" POST "$MA_DISABLE_URL" ""
    write_req "ma_flow__disable_missing"   404 "$P1_JWT_VALUE" POST "$MA_MISSING_DISABLE_URL" ""

    # TENANT audience-deny — target T1's OWN tenant so the anchor dep
    # resolves; Layer 1 audience check then fires inside the gate body.
    write_req "ma_flow__tenant_audience_deny" 403 "$T1_JWT_VALUE" POST \
        "${API}/module-access/${T1_TENANT_ID}/${MA_MODULE}/enable" ""
fi

# === Phase 4e — Step 6.13 org-tree write flow ===============================
# Multi-audience write surface. Five outside-matrix entries under
# PLATFORM-1 caller: add STORE, rename, reparent, cascade-order reject
# (REGION under STORE), duplicate-code reject. Plus a TENANT-side denial:
# random TENANT JWT (no ORG_NODES grant) gets 403 PERMISSION_DENIED at
# Layer 2.
#
# Names UUID-suffixed for re-run safety; each run leaks one STORE
# org_node (operator cleanup if running repeatedly).

section "Step 6.13 — org-tree write flow"

# Reuse the same seed-tenant resolution as Phase 4d. The tenant must
# have a visible org-tree (read returns non-empty tree[]); we use
# tree[0].id as PARENT_A and tree[1] / tree[0].children[0] as PARENT_B.
OT_TENANT_ID="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
    "${API}/tenants?limit=50" 2>/dev/null \
    | jq -r '.items[] | select(.name == "Buc-ee'\''s") | .id' \
    | head -n1)"
if [[ -z "$OT_TENANT_ID" || "$OT_TENANT_ID" == "null" ]]; then
    OT_TENANT_ID="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
        "${API}/tenants?limit=1" 2>/dev/null \
        | jq -r '.items[0].id')"
fi

OT_TREE_JSON="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
    "${API}/tenants/${OT_TENANT_ID}/org-tree" 2>/dev/null)"
OT_PARENT_A="$(printf '%s' "$OT_TREE_JSON" | jq -r '.tree[0].id // empty')"
OT_PARENT_B="$(printf '%s' "$OT_TREE_JSON" | jq -r '.tree[1].id // .tree[0].children[0].id // empty')"

if [[ -z "$OT_TENANT_ID" || -z "$OT_PARENT_A" ]]; then
    warn "org-tree write flow skipped — seed resolution incomplete"
else
    OT_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    OT_CODE="te-ot-${OT_SUFFIX:0:8}"

    write_req "ot_flow__add_store" 201 "$P1_JWT_VALUE" POST \
        "${API}/tenants/${OT_TENANT_ID}/org-tree" \
        "{\"parent_id\":\"${OT_PARENT_A}\",\"node_type\":\"STORE\",\"code\":\"${OT_CODE}\",\"name\":\"TE OT Store\"}"

    OT_ADD_OUT=$(printf "%s/%03d__ot_flow__add_store.json" "$RESULTS_DIR" "$seq")
    OT_NEW_ID="$(jq -r '.id // empty' < "$OT_ADD_OUT" 2>/dev/null || echo "")"

    if [[ -n "$OT_NEW_ID" ]]; then
        write_req "ot_flow__rename" 200 "$P1_JWT_VALUE" PATCH \
            "${API}/tenants/${OT_TENANT_ID}/org-tree/${OT_NEW_ID}" \
            '{"name":"TE OT Renamed"}'

        if [[ -n "$OT_PARENT_B" && "$OT_PARENT_B" != "$OT_PARENT_A" ]]; then
            write_req "ot_flow__reparent" 200 "$P1_JWT_VALUE" PATCH \
                "${API}/tenants/${OT_TENANT_ID}/org-tree/${OT_NEW_ID}" \
                "{\"parent_id\":\"${OT_PARENT_B}\"}"
        fi

        # Cascade-order reject: REGION under STORE.
        write_req "ot_flow__cascade_reject" 422 "$P1_JWT_VALUE" POST \
            "${API}/tenants/${OT_TENANT_ID}/org-tree" \
            "{\"parent_id\":\"${OT_NEW_ID}\",\"node_type\":\"REGION\",\"code\":\"te-rev-${OT_SUFFIX:0:8}\",\"name\":\"rev\"}"

        # Duplicate code.
        write_req "ot_flow__duplicate_code" 409 "$P1_JWT_VALUE" POST \
            "${API}/tenants/${OT_TENANT_ID}/org-tree" \
            "{\"parent_id\":\"${OT_PARENT_A}\",\"node_type\":\"STORE\",\"code\":\"${OT_CODE}\",\"name\":\"dup\"}"
    fi

    # TENANT JWT (random user, no grant) -> 403 PERMISSION_DENIED.
    # Anchor dep resolves on T1's own tenant root.
    write_req "ot_flow__tenant_no_grant_deny" 403 "$T1_JWT_VALUE" POST \
        "${API}/tenants/${T1_TENANT_ID}/org-tree" \
        "{\"parent_id\":\"${T1_TENANT_ID}\",\"node_type\":\"STORE\",\"code\":\"te-deny-${OT_SUFFIX:0:8}\",\"name\":\"x\"}"
fi

# === Phase 4f — Step 6.17.3 stores write flow ===============================
# Three outside-matrix entries under PLATFORM-1: POST create (UUID-suffixed
# name + store_code), PATCH rename, and a TENANT-side denial (random TENANT
# JWT with no STORES.CONFIGURE grant -> 403 PERMISSION_DENIED).
#
# Re-uses OT_TENANT_ID resolved in Phase 4e. UUID-suffixed identifiers so
# re-runs don't 409 DUPLICATE_STORE_CODE. Each run leaks one store
# (operator cleanup if running repeatedly).

section "Step 6.17.3 — stores write flow"

if [[ -z "$OT_TENANT_ID" || "$OT_TENANT_ID" == "null" ]]; then
    warn "stores write flow skipped — no resolvable tenant_id"
else
    ST_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    ST_SUFFIX="${ST_SUFFIX:0:8}"
    ST_NAME="te-store-${ST_SUFFIX}"
    ST_CODE="TE-${ST_SUFFIX}"

    # Step 6.21.2: POST /stores requires parent_org_node_id. Use the
    # tenant_root_id surfaced by /org-tree (Step 6.21.1 field).
    OT_TENANT_ROOT_ID="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
        "${API}/tenants/${OT_TENANT_ID}/org-tree" 2>/dev/null \
        | jq -r '.tenant_root_id // empty')"
    write_req "store_flow__create" 201 "$P1_JWT_VALUE" POST \
        "${API}/stores" \
        "{\"tenant_id\":\"${OT_TENANT_ID}\",\"parent_org_node_id\":\"${OT_TENANT_ROOT_ID}\",\"name\":\"${ST_NAME}\",\"country\":\"United States\",\"timezone\":\"America/New_York\",\"currency\":\"USD\",\"store_code\":\"${ST_CODE}\",\"tax_treatment\":\"EXCLUSIVE\"}"

    ST_CREATE_OUT=$(printf "%s/%03d__store_flow__create.json" "$RESULTS_DIR" "$seq")
    ST_NEW_ID="$(jq -r '.id // empty' < "$ST_CREATE_OUT" 2>/dev/null || echo "")"

    if [[ -n "$ST_NEW_ID" ]]; then
        write_req "store_flow__patch" 200 "$P1_JWT_VALUE" PATCH \
            "${API}/stores/${ST_NEW_ID}" \
            '{"name":"te-store-renamed"}'
    fi

    # TENANT OWNER (T1 = Marcus.t / Buc-ee's per default args) happy
    # path — multi-audience contract end-to-end. OWNER holds
    # ADMIN.STORES.CONFIGURE.TENANT per the Step 6.17.1 seed; the gate
    # admits and 201 fires. The TENANT-no-grants deny case is covered
    # by integration test RC7 (script-level no-grants test would need
    # a synthetic TENANT user, out of scope for the shell harness).
    ST_OWNER_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    ST_OWNER_SUFFIX="${ST_OWNER_SUFFIX:0:8}"
    # Step 6.21.2: T1 (Buc-ee's) needs its tenant_root_id too.
    T1_TENANT_ROOT_ID="$(curl -s -H "Authorization: Bearer ${T1_JWT_VALUE}" \
        "${API}/tenants/${T1_TENANT_ID}/org-tree" 2>/dev/null \
        | jq -r '.tenant_root_id // empty')"
    write_req "store_flow__tenant_owner_create" 201 "$T1_JWT_VALUE" POST \
        "${API}/stores" \
        "{\"tenant_id\":\"${T1_TENANT_ID}\",\"parent_org_node_id\":\"${T1_TENANT_ROOT_ID}\",\"name\":\"owner-${ST_OWNER_SUFFIX}\",\"country\":\"United States\",\"timezone\":\"America/New_York\",\"currency\":\"USD\",\"store_code\":\"ON-${ST_OWNER_SUFFIX}\",\"tax_treatment\":\"EXCLUSIVE\"}"
fi

# === Phase 4g — Step 6.17.4 stores set-status flow ==========================
# Two outside-matrix entries reusing ST_NEW_ID from Phase 4f (a freshly-
# created store sitting in ACTIVE per the DDL default). Order is
# "rejected first, happy second" so the source state stays ACTIVE
# until the happy transition flips it to INACTIVE.

section "Step 6.17.4 — stores set-status flow"

if [[ -z "${ST_NEW_ID:-}" || "$ST_NEW_ID" == "null" ]]; then
    warn "set-status flow skipped — no ST_NEW_ID from Phase 4f"
else
    write_req "set_status__rejected_active_to_opening" 409 "$P1_JWT_VALUE" POST \
        "${API}/stores/${ST_NEW_ID}/set-status" \
        '{"target_status":"OPENING"}'

    write_req "set_status__happy_active_to_inactive" 200 "$P1_JWT_VALUE" POST \
        "${API}/stores/${ST_NEW_ID}/set-status" \
        '{"target_status":"INACTIVE"}'
fi

# === Phase 4h — Step 6.18.3 PATCH /roles/{role_id} ==========================
# 5 outside-matrix entries: happy path (PLATFORM-1 caller, non-SUPER_ADMIN
# PLATFORM role), TENANT audience deny (T1), unknown role 404,
# SUPER_ADMIN protected 409, forbidden field 422. The matrix's role-detail
# GETs run in Phase 4; the PATCH probes here run once outside the matrix
# (PATCH mutates state; running it 4x per caller would mutate 4x).

section "Step 6.18.3 — roles PATCH flow"

# Resolve PLATFORM_ADMIN id (non-SUPER_ADMIN, holds PATCH-able shape).
PLATFORM_ADMIN_ROLE_ID=$(jq -r '.platform_roles.items[] | select(.code=="PLATFORM_ADMIN") | .id' \
    < "$setup_roles" | head -1)

if [[ -z "$PLATFORM_ADMIN_ROLE_ID" || "$PLATFORM_ADMIN_ROLE_ID" == "null" ]]; then
    warn "roles PATCH flow skipped — PLATFORM_ADMIN id not resolvable"
else
    # Happy path: SUPER_ADMIN (P1) PATCHes PLATFORM_ADMIN description.
    write_req "role_patch__happy" 200 "$P1_JWT_VALUE" PATCH \
        "${API}/roles/${PLATFORM_ADMIN_ROLE_ID}" \
        '{"description":"test-endpoint patched description"}'

    # Forbidden field rejected at Pydantic layer (extra='forbid').
    write_req "role_patch__forbidden_status" 422 "$P1_JWT_VALUE" PATCH \
        "${API}/roles/${PLATFORM_ADMIN_ROLE_ID}" \
        '{"status":"INACTIVE"}'

    # TENANT JWT refused at Layer 1 audience gate.
    write_req "role_patch__tenant_audience_deny" 403 "$T1_JWT_VALUE" PATCH \
        "${API}/roles/${PLATFORM_ADMIN_ROLE_ID}" \
        '{"name":"tenant-jwt-should-not-land"}'
fi

# Unknown role id -> 404.
write_req "role_patch__unknown_404" 404 "$P1_JWT_VALUE" PATCH \
    "${API}/roles/00000000-0000-0000-0000-000000000000" \
    '{"name":"unreachable"}'

# SUPER_ADMIN protected -> 409 SUPER_ADMIN_PROTECTED.
write_req "role_patch__super_admin_protected" 409 "$P1_JWT_VALUE" PATCH \
    "${API}/roles/${PLATFORM_ROLE_ID}" \
    '{"name":"super-admin-should-not-edit"}'

# === Phase 4i — Step 6.20.2 /me/can-do target_anchor validation =============
# Single outside-matrix entry. Pre-fix, GET /me/can-do with a non-ltree
# target_anchor (e.g., UUID with hyphens) returned 500 because psycopg's
# ltree CAST raised SyntaxError that bubbled to the generic 500 envelope.
# Post-fix, the Pydantic Query pattern validator rejects at 422 BEFORE
# the gate dependency runs. The check is JWT-type-agnostic (Pydantic
# fires before auth dispatch); one outside-matrix entry is sufficient.
#
# Cloud incident: v0.1.17, revision admin-backend-00018-46f.

section "Step 6.20.2 — /me/can-do ltree validation"

req "me_can_do__ltree_validation_422" 422 "$P1_JWT_VALUE" GET \
    "${API}/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=GLOBAL&target_anchor=019df261-b87c-7d3e-ab9e-dcf26259cec6"

# === Phase 5 — Summary ======================================================
END_TIME="$(date +%s)"
RUNTIME=$((END_TIME - START_TIME))

section "Summary"
echo "  Total calls:  ${total_count}"
echo "  Passed:       ${C_GRN}${ok_count}${C_RST}"
if [[ "$fail_count" -gt 0 ]]; then
    echo "  Failed:       ${C_RED}${fail_count}${C_RST}"
else
    echo "  Failed:       ${fail_count}"
fi
echo "  Results saved to: ${RESULTS_DIR}/"
echo "  Runtime:      ${RUNTIME}s"

if [[ "$RUNTIME" -gt 30 ]]; then
    warn "Runtime exceeded 30s threshold (${RUNTIME}s); investigate \
(slow server? slow DB? large response bodies?)."
fi

if [[ "$fail_count" -gt 0 ]]; then
    echo
    echo "${C_RED}Failures:${C_RST}"
    for f in "${FAILURES[@]}"; do
        echo "  - ${f}"
    done
    exit 1
fi

echo
echo "${C_GRN}All ${total_count} calls passed.${C_RST}"
exit 0
