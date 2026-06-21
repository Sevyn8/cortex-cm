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
BODY_PREVIEW_MAX="${BODY_PREVIEW_MAX:-1200}"

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
        # Compact one-liner; jq -c collapses to single line. Truncate at
        # BODY_PREVIEW_MAX chars for terminal sanity (override via env).
        # Fall back to cat for non-JSON bodies.
        local body_line
        body_line=$(jq -c '.' "$outfile" 2>/dev/null || cat "$outfile")
        if [[ ${#body_line} -gt $BODY_PREVIEW_MAX ]]; then
            body_line="${body_line:0:$BODY_PREVIEW_MAX}…"
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

# Step 5.3: discover an HQ-level org-node per tenant for the children endpoint.
# We fetch each tenant's org-tree with P1 (PLATFORM sees all under D-29) and
# pick .tree[0].id — the first top-level node. node_type doesn't matter for
# status-code coverage; we just need a real node id rooted in each tenant so
# the children endpoint's tenant_id filter resolves correctly.
setup_t1_tree="${RESULTS_DIR}/000__setup__t1_org_tree.json"
setup_t2_tree="${RESULTS_DIR}/000__setup__t2_org_tree.json"
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/tenants/${T1_TENANT_ID}/org-tree" -o "$setup_t1_tree"
curl -s -H "Authorization: Bearer $(cat "$P1_JWT")" \
    "${API}/tenants/${T2_TENANT_ID}/org-tree" -o "$setup_t2_tree"
T1_HQ_NODE_ID=$(jq -r '.tree[0].id // empty' < "$setup_t1_tree")
T2_HQ_NODE_ID=$(jq -r '.tree[0].id // empty' < "$setup_t2_tree")
if [[ -z "$T1_HQ_NODE_ID" || "$T1_HQ_NODE_ID" == "null" ]]; then
    die "fixture discovery: org-tree returned empty .tree for T1 tenant \
${T1_TENANT_ID} — seed first"
fi
if [[ -z "$T2_HQ_NODE_ID" || "$T2_HQ_NODE_ID" == "null" ]]; then
    die "fixture discovery: org-tree returned empty .tree for T2 tenant \
${T2_TENANT_ID} — seed first"
fi

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
echo "  ${C_GRN}✓${C_RST} T1 HQ org-node id=${T1_HQ_NODE_ID}"
echo "  ${C_GRN}✓${C_RST} T2 HQ org-node id=${T2_HQ_NODE_ID}"

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
req "public__health"            200 ""        GET "${API}/health"
req "public__ready"             200 ""        GET "${API}/ready"
req "public__openapi"           200 ""        GET "${API}/openapi.json"
req "noauth__tenants_401"       401 ""        GET "${API}/tenants"
# Step 5.3 / 6.5 / 6.8.3 endpoints — one no-auth probe each.
req "noauth__org_tree_401"      401 ""        GET "${API}/tenants/${T1_TENANT_ID}/org-tree"
req "noauth__org_children_401"  401 ""        GET "${API}/tenants/${T1_TENANT_ID}/org-nodes/${T1_HQ_NODE_ID}/children"
req "noauth__dashboard_fleet_401" 401 ""      GET "${API}/dashboard/fleet-stats"
req "noauth__dashboard_gov_401"   401 ""      GET "${API}/dashboard/governance-stats"
req "noauth__role_assignments_401" 401 ""     GET "${API}/role-assignments"

# ---- Per-user matrix runner ----
# Args: caller_label  jwt_path  caller_kind  own_tenant  own_user  other_tenant  other_user  own_hq  other_hq
#   caller_kind: "PLATFORM" or "TENANT"
#   own_hq / other_hq: HQ-level org-node id rooted in own_tenant / other_tenant
#     respectively. Used by the /org-nodes/{node_id}/children cells (Step 5.3)
#     where the router's node_exists check filters by both tenant_id AND
#     node_id — pairing a node id with the wrong tenant produces 404
#     regardless of session type.
run_matrix_for_caller() {
    local label="$1" jwt="$2" kind="$3"
    local own_tenant="$4" own_user="$5" other_tenant="$6" other_user="$7"
    local own_hq="$8" other_hq="$9"

    section "${kind} caller — ${label}  (own_tenant=${own_tenant})"

    # --- /lookups (multi-user-type; T_other moot for batch endpoints) ---
    req "${label}__lookups__all" 200 "$jwt" GET \
        "${API}/lookups?lists=tenant_tier,tenant_region,tenant_status,tenant_industry,module_code,country"
    req "${label}__lookups__empty_param" 200 "$jwt" GET "${API}/lookups?lists="
    req "${label}__lookups__unknown_list" 200 "$jwt" GET "${API}/lookups?lists=does_not_exist"

    # --- /tenants list + filters + stats ---
    # NOTE: /tenants list accepts `sort` as of Step 6.4 — aggregate keys
    # (num_users_active_{asc,desc}, num_stores_{asc,desc}) plus the
    # original created_at/name/tier asc+desc.
    req "${label}__tenants__list"          200 "$jwt" GET "${API}/tenants"
    req "${label}__tenants__list_limit2"   200 "$jwt" GET "${API}/tenants?limit=2&offset=0"
    req "${label}__tenants__list_offset2"  200 "$jwt" GET "${API}/tenants?limit=2&offset=2"
    req "${label}__tenants__search_buc"    200 "$jwt" GET "${API}/tenants?search=Buc"
    req "${label}__tenants__tier_smb"      200 "$jwt" GET "${API}/tenants?tier=SMB"
    req "${label}__tenants__sort_users_desc" 200 "$jwt" GET "${API}/tenants?sort=num_users_active_desc&limit=10"
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

    # --- /tenants/{tenant_id}/org-tree (Step 5.3: multi-user-type with RLS) ---
    # PLATFORM sees any tenant (D-29 OR on tenants_self_access); TENANT
    # gets 404 on cross-tenant (RLS hides the tenant row at resolution).
    # depth=99 trips Pydantic's le=MAX_DEPTH=6 (Query constraint at
    # src/admin_backend/routers/v1/org_tree.py:131-141) → 422 before handler.
    req "${label}__org_tree__own"           200 "$jwt" GET "${API}/tenants/${own_tenant}/org-tree"
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__org_tree__other"     200 "$jwt" GET "${API}/tenants/${other_tenant}/org-tree"
    else
        req "${label}__org_tree__other"     404 "$jwt" GET "${API}/tenants/${other_tenant}/org-tree"
    fi
    req "${label}__org_tree__unknown"       404 "$jwt" GET "${API}/tenants/${UNKNOWN_UUID}/org-tree"
    req "${label}__org_tree__depth_2"       200 "$jwt" GET "${API}/tenants/${own_tenant}/org-tree?depth=2"
    req "${label}__org_tree__depth_99"      422 "$jwt" GET "${API}/tenants/${own_tenant}/org-tree?depth=99"

    # --- /tenants/{tenant_id}/org-nodes/{node_id}/children (Step 5.3) ---
    # node_exists() filters WHERE tenant_id=:tenant_id AND id=:node_id at
    # src/admin_backend/repositories/org_nodes.py:236-244 — the tenant_id
    # filter is in the WHERE clause itself; RLS doesn't bypass it. So:
    #   own_tenant + own_hq          → 200 (always)
    #   other_tenant + other_hq      → 200 for PLATFORM (D-29 OR resolves
    #                                  both tenants + node_exists matches);
    #                                  404 for TENANT (tenant resolution
    #                                  itself fails — RLS-as-404)
    #   own_tenant + other_hq        → 404 (always — node_exists rejects:
    #                                  the node's tenant_id ≠ requested
    #                                  tenant_id, regardless of session)
    req "${label}__children__own"           200 "$jwt" GET "${API}/tenants/${own_tenant}/org-nodes/${own_hq}/children"
    req "${label}__children__own_limit"     200 "$jwt" GET "${API}/tenants/${own_tenant}/org-nodes/${own_hq}/children?limit=2&offset=0"
    if [[ "$kind" == "PLATFORM" ]]; then
        req "${label}__children__other"     200 "$jwt" GET "${API}/tenants/${other_tenant}/org-nodes/${other_hq}/children"
    else
        req "${label}__children__other"     404 "$jwt" GET "${API}/tenants/${other_tenant}/org-nodes/${other_hq}/children"
    fi
    req "${label}__children__cross_node"    404 "$jwt" GET "${API}/tenants/${own_tenant}/org-nodes/${other_hq}/children"

    # --- /dashboard/fleet-stats (Step 6.5: multi-user-type, RLS scopes) ---
    # Status-code only. RLS persona projection (PLATFORM sees fleet totals;
    # TENANT sees own-tenant projection) is real but verified in pytest, not
    # here.
    req "${label}__dashboard__fleet_stats"  200 "$jwt" GET "${API}/dashboard/fleet-stats"

    # --- /dashboard/governance-stats (Step 6.5) ---
    req "${label}__dashboard__governance"   200 "$jwt" GET "${API}/dashboard/governance-stats"

    # --- /role-assignments (Step 6.8.3: grouped envelope) ---
    # Multi-user-type with a security-load-bearing twist: TENANT JWTs cause
    # the platform-side query to be SHORT-CIRCUITED at the router (not RLS;
    # platform_user_role_assignments has no RLS) per locked decision 12 at
    # src/admin_backend/routers/v1/role_assignments.py:307-314. Status-only
    # assertion here; the no-call invariant is locked in pytest R2.
    # sort=nope → InvalidSortKeyError → InvalidSortKeyClientError → 400
    # INVALID_SORT_KEY (verified at role_assignments.py:346-349).
    req "${label}__ra__list"                200 "$jwt" GET "${API}/role-assignments"
    req "${label}__ra__status_active"       200 "$jwt" GET "${API}/role-assignments?status=ACTIVE"
    req "${label}__ra__tenant_id_own"       200 "$jwt" GET "${API}/role-assignments?tenant_id=${own_tenant}"
    req "${label}__ra__sort_asc"            200 "$jwt" GET "${API}/role-assignments?sort=granted_at_asc"
    req "${label}__ra__invalid_sort"        400 "$jwt" GET "${API}/role-assignments?sort=nope"
    # TENANT-only: exercise the platform-side short-circuit. PLATFORM
    # callers would also return 200 here but with populated platform items;
    # the test's value is the short-circuit path under TENANT.
    if [[ "$kind" == "TENANT" ]]; then
        req "${label}__ra__platform_user_id_short_circuit" 200 "$jwt" GET "${API}/role-assignments?platform_user_id=${ANY_PLATFORM_USER_ID}"
    fi
}

# Each PLATFORM caller gets T1's values as "own" and T2's as "other"; the
# OTHER concept is moot for PLATFORM but the matrix still hits the URL.
P1_PREFIX=$(email_to_token_name "$PLATFORM_EMAIL_1")
P2_PREFIX=$(email_to_token_name "$PLATFORM_EMAIL_2")
T1_PREFIX=$(email_to_token_name "$TENANT_EMAIL_1")
T2_PREFIX=$(email_to_token_name "$TENANT_EMAIL_2")

run_matrix_for_caller "${P1_PREFIX}_P" "$P1_JWT" PLATFORM \
    "$T1_TENANT_ID" "$T1_USER_ID" "$T2_TENANT_ID" "$T2_USER_ID" \
    "$T1_HQ_NODE_ID" "$T2_HQ_NODE_ID"
run_matrix_for_caller "${P2_PREFIX}_P" "$P2_JWT" PLATFORM \
    "$T1_TENANT_ID" "$T1_USER_ID" "$T2_TENANT_ID" "$T2_USER_ID" \
    "$T1_HQ_NODE_ID" "$T2_HQ_NODE_ID"
run_matrix_for_caller "${T1_PREFIX}_T" "$T1_JWT" TENANT \
    "$T1_TENANT_ID" "$T1_USER_ID" "$T2_TENANT_ID" "$T2_USER_ID" \
    "$T1_HQ_NODE_ID" "$T2_HQ_NODE_ID"
run_matrix_for_caller "${T2_PREFIX}_T" "$T2_JWT" TENANT \
    "$T2_TENANT_ID" "$T2_USER_ID" "$T1_TENANT_ID" "$T1_USER_ID" \
    "$T2_HQ_NODE_ID" "$T1_HQ_NODE_ID"

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
