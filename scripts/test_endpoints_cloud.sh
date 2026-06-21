#!/usr/bin/env bash
# scripts/test_endpoints_cloud.sh — cloud-targeted curl harness.
#
# Cloud-aware sibling of scripts/test_endpoints_max_view.sh (commit 1df5cf3).
# Same matrix, same assertions, same output style — only JWT loading,
# fixture-discovery side-effect paths, and pre-flight checks differ. Phase 4
# is byte-identical to the local script (verified via awk-extracted diff at
# build time; re-verify after every sync).
#
# Usage (from project root):
#   ./scripts/test_endpoints_cloud.sh <base-url>
#
# Example:
#   ./scripts/test_endpoints_cloud.sh https://admin-backend-f2qhpcdeba-el.a.run.app
#
# Pre-conditions (operator-side; verified inside Phase 0):
#   - The 9 cloud JWTs at scripts/jwt/tokens/cloud/ are present and non-empty.
#     The cloud variant CONSUMES pre-minted JWTs; it does NOT mint them.
#     The minting flow is in commit d689fb3 context (gitignored directory).
#   - Cloud service /api/v1/health returns 200.
#   - Cloud service /api/v1/ready reports .db == "ok".
#
# JWT-to-caller mapping (each overridable via env var):
#   P1 PLATFORM   anjali-cloud-150d.jwt
#   P2 PLATFORM   devon-cloud-150d.jwt
#   T1 TENANT     marcus-t-cloud-150d.jwt   (Buc-ee's)
#   T2 TENANT     a-kowalski-cloud-150d.jwt (Żabka Group)
#
# Override a path:
#   P1_JWT_PATH=path/to/other.jwt ./scripts/test_endpoints_cloud.sh <url>
#
# Cloud-specific differences from test_endpoints_max_view.sh:
#   - Does NOT mint JWTs (no scripts/jwt/generate.sh call).
#   - Does NOT touch docs/endpoints/openapi.json — writes to
#     /tmp/openapi-cloud-<timestamp>.json instead.
#   - Saves response bodies to scripts/test_endpoints/results-cloud/<ts>/
#     (separate from local's results/ tree).
#   - Skips the email-existence DB check (no local DB on cloud substrate);
#     replaces it with JWT-file presence + /ready db:ok checks.
#   - BASE_URL is a required positional arg (no http://localhost:8000 default).
#
# Naming note: $P1_JWT through $T2_JWT hold FILE PATHS, not JWT contents —
# the req() function in scripts/test_endpoints_max_view.sh reads the file
# per-call. The cloud variant inherits the local convention (and the
# variable names) so that Phase 4 stays byte-identical.
#
# Exit codes:
#   0  — all calls returned the expected status.
#   1  — at least one call mismatched, or pre-flight bailed.

set -uo pipefail
# Deliberately NOT `set -e`: individual curl calls are allowed to return
# non-200; the harness captures status codes and continues so the summary
# is complete. Setup-phase failures bail explicitly via `die`.

# === Configuration ===========================================================
if [[ $# -lt 1 || -z "${1:-}" ]]; then
    echo "Usage: $0 <base-url>" >&2
    echo "Example: $0 https://admin-backend-f2qhpcdeba-el.a.run.app" >&2
    exit 1
fi
BASE_URL="$1"
API="${BASE_URL}/api/v1"

# Hardcoded — these match the cloud DB seed (same fake-company fixture as
# local). Used by Phase 3 (TENANT_EMAIL_*) for tenant_user discovery, and by
# Phase 4's email_to_token_name prefix derivation (PLATFORM_EMAIL_*). Phase 4
# is byte-identical with the local script, so these four variables MUST be
# defined upstream — do NOT remove them.
PLATFORM_EMAIL_1="anjali@ithina.ai"          # P1
PLATFORM_EMAIL_2="devon@ithina.ai"           # P2
TENANT_EMAIL_1="marcus.t@bucees.com"         # T1 (Buc-ee's)
TENANT_EMAIL_2="a.kowalski@zabka.pl"         # T2 (Żabka Group)

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RESULTS_DIR="scripts/test_endpoints/results-cloud/${TIMESTAMP}"
JWT_DIR="scripts/jwt/tokens/cloud"
OPENAPI_DEST="/tmp/openapi-cloud-${TIMESTAMP}.json"
UNKNOWN_UUID="00000000-0000-0000-0000-000000000000"
BODY_PREVIEW_MAX="${BODY_PREVIEW_MAX:-1200}"

# JWT-to-caller paths. Override any via env var:
#   P1_JWT_PATH=path/to/other.jwt ./scripts/test_endpoints_cloud.sh <url>
# Choice rationale: Anjali (P1) + Devon (P2) are two distinct PLATFORM
# identities; Marcus (T1, Buc-ee's) + Anna (T2, Żabka) are two TENANT
# callers in DIFFERENT tenants, so cross-tenant probes are meaningful.
P1_JWT_PATH="${P1_JWT_PATH:-${JWT_DIR}/anjali-cloud-150d.jwt}"
P2_JWT_PATH="${P2_JWT_PATH:-${JWT_DIR}/devon-cloud-150d.jwt}"
T1_JWT_PATH="${T1_JWT_PATH:-${JWT_DIR}/marcus-t-cloud-150d.jwt}"
T2_JWT_PATH="${T2_JWT_PATH:-${JWT_DIR}/a-kowalski-cloud-150d.jwt}"

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

# === Phase 0 — Pre-flight ===================================================
section "Phase 0 — pre-flight"

command -v jq   >/dev/null 2>&1 || die "jq not on PATH"
command -v curl >/dev/null 2>&1 || die "curl not on PATH"

# Server health (no JWT, public path).
health_status=$(curl -s -o /dev/null -w "%{http_code}" "${API}/health" 2>/dev/null || echo "000")
if [[ "$health_status" != "200" ]]; then
    die "cloud /health unreachable at ${API}/health (got ${health_status}). \
Is the cloud service up at ${BASE_URL}?"
fi
echo "  ${C_GRN}✓${C_RST} server healthy at ${BASE_URL}"

# Readiness check — parse JSON, expect .db == "ok". Cloud DB is the substrate
# for every downstream call; bail early if the deployed service can't reach it.
ready_body=$(curl -s "${API}/ready" 2>/dev/null || echo "{}")
ready_db=$(echo "$ready_body" | jq -r '.db // empty' 2>/dev/null || echo "")
if [[ "$ready_db" != "ok" ]]; then
    die "cloud /ready did not report .db == \"ok\" (got: ${ready_body}). \
Cloud DB is the substrate for all downstream calls."
fi
echo "  ${C_GRN}✓${C_RST} cloud /ready reports db=ok"

# JWT-file presence — cloud variant CONSUMES pre-minted JWTs; it does not
# mint. No expiry check: the 150-day window is comfortable, and if a JWT
# happens to be expired, matrix cells will surface 401 and the operator
# will know immediately.
for path in "$P1_JWT_PATH" "$P2_JWT_PATH" "$T1_JWT_PATH" "$T2_JWT_PATH"; do
    if [[ ! -s "$path" ]]; then
        die "Cloud JWT missing: ${path}. The cloud variant does NOT mint JWTs; \
see commit d689fb3 context for the minting flow."
    fi
done
echo "  ${C_GRN}✓${C_RST} 4 cloud JWT files present"

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

# Fetch the spec and pretty-print through jq. Cloud variant writes to
# /tmp — docs/endpoints/openapi.json is the local snapshot and stays
# untouched here.
if ! curl -sf "${API}/openapi.json" | jq . > "${OPENAPI_DEST}.tmp"; then
    rm -f "${OPENAPI_DEST}.tmp"
    die "failed to fetch ${API}/openapi.json (server returned non-2xx) \
or jq failed to parse it"
fi
mv "${OPENAPI_DEST}.tmp" "$OPENAPI_DEST"
path_count=$(jq '.paths | length' < "$OPENAPI_DEST")
echo "  ${C_GRN}✓${C_RST} saved ${OPENAPI_DEST} (${path_count} paths)"
jq -r '.paths | keys[] | "    - " + .' < "$OPENAPI_DEST"

# === Phase 2 — Load 4 cloud JWTs ============================================
section "Phase 2 — load 4 cloud JWTs"

# Cloud variant CONSUMES pre-minted JWT file paths into the same variable
# names the local script uses ($P1_JWT..$T2_JWT). These hold FILE PATHS,
# not contents — req() reads each file per-call (mirrors the local
# convention; required for Phase 4 byte-identity).
P1_JWT="$P1_JWT_PATH"
P2_JWT="$P2_JWT_PATH"
T1_JWT="$T1_JWT_PATH"
T2_JWT="$T2_JWT_PATH"

# Per-file existence already verified in Phase 0; just log here.
echo "  ${C_GRN}✓${C_RST} P1 PLATFORM → $P1_JWT"
echo "  ${C_GRN}✓${C_RST} P2 PLATFORM → $P2_JWT"
echo "  ${C_GRN}✓${C_RST} T1 TENANT   → $T1_JWT"
echo "  ${C_GRN}✓${C_RST} T2 TENANT   → $T2_JWT"

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

# Step 6.21.1: assert the new top-level tenant_root_* fields land on the
# /org-tree response. Cloud-strict assertion keyed on Buc-ee's (T1): the
# tenant-root code is ``BUC-EES`` and the path is ``buc_ees`` per
# operator-verified Cloud SQL state. Frontend uses tenant_root_id (not
# tenant_id) as ``parent_id`` on POST /org-tree under the synthesized
# TENANT row.
T1_TENANT_ROOT_ID=$(jq -r '.tenant_root_id // empty' < "$setup_t1_tree")
T1_TENANT_ROOT_CODE=$(jq -r '.tenant_root_code // empty' < "$setup_t1_tree")
T1_TENANT_ROOT_PATH=$(jq -r '.tenant_root_path // empty' < "$setup_t1_tree")
total_count=$((total_count + 1))
if [[ -n "$T1_TENANT_ROOT_ID" \
      && "$T1_TENANT_ROOT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ \
      && "$T1_TENANT_ROOT_CODE" == "BUC-EES" \
      && "$T1_TENANT_ROOT_PATH" == "buc_ees" ]]; then
    echo "  ${C_GRN}[✓ 200]${C_RST} GET /tenants/${T1_TENANT_ID}/org-tree carries tenant_root_id/code=BUC-EES/path=buc_ees  ${C_DIM}— setup__t1_tenant_root_fields${C_RST}"
    ok_count=$((ok_count + 1))
else
    echo "  ${C_RED}[✗ id=${T1_TENANT_ROOT_ID:-null} code=${T1_TENANT_ROOT_CODE:-null} path=${T1_TENANT_ROOT_PATH:-null}]${C_RST} T1 /org-tree missing tenant_root_* fields  ${C_DIM}— setup__t1_tenant_root_fields${C_RST}"
    fail_count=$((fail_count + 1))
    FAILURES+=("setup__t1_tenant_root_fields (id=${T1_TENANT_ROOT_ID:-null})")
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

    # --- /me/* (Step 6.9.2: multi-user-type; caller-state endpoints) ---
    # /me/permissions returns the caller's full grant set (always an array;
    # empty if no grants). /me/can-do is a server-authoritative single-
    # permission check. Cloud script asserts 200 only; the `allowed` boolean
    # is verified in pytest. Probed tuple ADMIN.USERS.VIEW.TENANT exists in
    # seed across both audiences.
    req "${label}__me__permissions"            200 "$jwt" GET "${API}/me/permissions"
    req "${label}__me__can_do"                 200 "$jwt" GET "${API}/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=TENANT"

    # --- /stores (Step 6.17.2: multi-user-type with RLS scoping) ---
    # Same posture as local: PLATFORM sees all, TENANT sees own only,
    # detail on UNKNOWN_UUID → 404 STORE_NOT_FOUND (anchor dep miss).
    # Cloud catalogue must include ADMIN.STORES.VIEW.TENANT for OWNER
    # per Step 6.17.1; PLATFORM cascades from .GLOBAL.
    req "${label}__stores__list"               200 "$jwt" GET "${API}/stores"
    req "${label}__stores__detail_unknown"     404 "$jwt" GET "${API}/stores/${UNKNOWN_UUID}"

    # --- /audit/activities (Step 6.16.3: multi-user-type with RLS scoping) ---
    # PLATFORM sees merged UNION across both audit tables; TENANT sees
    # only own-tenant rows (tenant_activity_audit_logs, RLS-scoped).
    # Both callers gated on ADMIN.AUDIT_LOG.VIEW.TENANT: SUPER_ADMIN +
    # PLATFORM_ADMIN + SUPPORT_ADMIN cascade from .VIEW.GLOBAL (Step
    # 6.16.3 catalogue update); tenant roles with .VIEW.TENANT pass
    # directly. Cursor pagination; malformed cursor -> 422
    # INVALID_CURSOR; UNKNOWN_UUID -> 404 AUDIT_EVENT_NOT_FOUND.
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

# === Phase 4b — Step 6.11.2 tenants write flow ==============================
# Cloud-mirror of test_endpoints.sh's Phase 4b. 5 outside-matrix entries:
# POST + PATCH + /suspend + /activate happy path (PLATFORM-1 caller), plus
# 1 TENANT audience-deny on POST. Names UUID-suffixed for re-run safety
# against the long-lived cloud target — each run leaks one tenant in
# ACTIVE state on Cloud SQL (operator cleanup if repeated runs accumulate
# noise).

section "Step 6.11.2 — tenants write flow"

WRITE_SUFFIX="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
WRITE_NAME="cloud-${WRITE_SUFFIX}"
P1_JWT_VALUE="$(cat "$P1_JWT")"
T1_JWT_VALUE="$(cat "$T1_JWT")"

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
  "primary_contact_name": "Cloud Endpoint Operator",
  "contact_email": "cloud-${WRITE_SUFFIX}@test.example.com",
  "number_of_stores": 5,
  "number_of_stores_as_of_date": "2026-01-01"
}
EOF
)"

write_req "write_flow__create" 201 "$P1_JWT_VALUE" POST "${API}/tenants" "$CREATE_BODY"

CREATE_OUTFILE=$(printf "%s/%03d__write_flow__create.json" "$RESULTS_DIR" "$seq")
WRITE_TENANT_ID="$(jq -r '.id // empty' < "$CREATE_OUTFILE" 2>/dev/null || echo "")"

if [[ -n "$WRITE_TENANT_ID" ]]; then
    # Step 6.20.1: POST -> GET roundtrip. Pre-fix the GET returned 404
    # because POST did not provision a tenant-root org_node.
    write_req "write_flow__post_get_roundtrip" 200 "$P1_JWT_VALUE" GET \
        "${API}/tenants/${WRITE_TENANT_ID}" ""
    write_req "write_flow__patch"    200 "$P1_JWT_VALUE" PATCH \
        "${API}/tenants/${WRITE_TENANT_ID}" \
        '{"primary_contact_name":"Cloud patched"}'
    write_req "write_flow__suspend"  200 "$P1_JWT_VALUE" POST \
        "${API}/tenants/${WRITE_TENANT_ID}/suspend" ""
    write_req "write_flow__activate" 200 "$P1_JWT_VALUE" POST \
        "${API}/tenants/${WRITE_TENANT_ID}/activate" ""
fi

write_req "write_flow__audience_deny" 403 "$T1_JWT_VALUE" POST "${API}/tenants" \
    '{"name":"tenant-jwt-should-not-land","region":"US","tier":"ENTERPRISE","industry":"GROCERY","country":"United States","primary_contact_name":"X","contact_email":"x@test.example.com","number_of_stores":1,"number_of_stores_as_of_date":"2026-01-01"}'

# === Phase 4c — Step 6.10.1 tenant-users write flow =========================
# Cloud-mirror of test_endpoints.sh's Phase 4c. 5 outside-matrix entries:
# POST + PATCH + /suspend(409) + /activate(409) + TENANT self-edit deny.
# Suspend/activate against the fresh INVITED user return 409
# INVALID_STATE_TRANSITION — the 200 happy paths require an ACTIVE user
# (covered by integration tests S1/S2/A1/A2). UUID-suffixed emails for
# re-run safety.

section "Step 6.10.1 — tenant-users write flow"

# Resolve OWNER role_id from the seeded catalogue (deterministic across
# local + cloud).
TU_OWNER_ROLE_ID="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
    "${API}/roles?limit=50" 2>/dev/null \
    | jq -r '.tenant_roles.items[] | select(.code == "OWNER") | .id' \
    | head -n1)"

if [[ -z "$TU_OWNER_ROLE_ID" || "$TU_OWNER_ROLE_ID" == "null" ]]; then
    warn "Could not resolve OWNER role_id; tenant-users write flow skipped"
else
    # Step 6.14: resolve two distinct anchor org_nodes.
    TU_TREE_RESP="$(curl -s -H "Authorization: Bearer ${P1_JWT_VALUE}" \
        "${API}/tenants/${T1_TENANT_ID}/org-tree" 2>/dev/null)"
    TU_ANCHOR_A="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tree[0].id // empty')"
    TU_ANCHOR_B="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tree[0].children[0].id // .tree[0].id // empty')"

    if [[ -z "$TU_ANCHOR_A" ]]; then
        warn "Could not resolve org_node anchor; tenant-users write flow skipped"
    else
        TU_SUFFIX="$(uuidgen 2>/dev/null \
            || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
        TU_CREATE_BODY="$(cat <<EOF
{
  "tenant_id": "${T1_TENANT_ID}",
  "email": "tec-tu-${TU_SUFFIX}@test.example.com",
  "full_name": "TEC TU ${TU_SUFFIX}",
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
                '{"full_name":"TEC TU patched"}'
            write_req "tu_flow__suspend_invited_409" 409 "$P1_JWT_VALUE" POST \
                "${API}/tenant-users/${WRITE_TU_ID}/suspend" ""
            write_req "tu_flow__activate_invited_409" 409 "$P1_JWT_VALUE" POST \
                "${API}/tenant-users/${WRITE_TU_ID}/activate" ""
        fi

        # Step 6.14 additions (cloud-mirror of test_endpoints.sh).
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
  "email": "tec-tu14-${TU14_SUFFIX}@test.example.com",
  "full_name": "TEC TU14 ${TU14_SUFFIX}",
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

        MISSING_ANCHOR="$(uuidgen 2>/dev/null \
            || python3 -c 'import uuid;print(uuid.uuid4())')"
        write_req "tu14_flow__post_invalid_org_node" 422 "$P1_JWT_VALUE" POST \
            "${API}/tenant-users" \
            "$(cat <<EOF
{
  "tenant_id": "${T1_TENANT_ID}",
  "email": "tec-tu14-bad-${TU14_SUFFIX}@test.example.com",
  "full_name": "TEC TU14 Bad",
  "roles": [{"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${MISSING_ANCHOR}"}]
}
EOF
)"

        if [[ -n "${T2_JWT_VALUE:-}" ]]; then
            write_req "tu14_flow__post_admin_role_denied" 403 \
                "$T2_JWT_VALUE" POST "${API}/tenant-users" \
                "$(cat <<EOF
{
  "tenant_id": "${T2_TENANT_ID:-${T1_TENANT_ID}}",
  "email": "tec-tu14-admin-${TU14_SUFFIX}@test.example.com",
  "full_name": "TEC TU14 ADMIN Caller",
  "roles": [{"role_id": "${TU_OWNER_ROLE_ID}", "org_node_id": "${TU_ANCHOR_A}"}]
}
EOF
)"
        fi
    fi
fi

# TENANT-1 OWNER patching self -> 403 SELF_EDIT_FORBIDDEN. T1_USER_ID is
# the JWT's user_id by construction (captured at JWT mint time).
write_req "tu_flow__self_edit_deny" 403 "$T1_JWT_VALUE" PATCH \
    "${API}/tenant-users/${T1_USER_ID}" \
    '{"full_name":"Trying To Edit Self"}'

# === Phase 4d — Step 6.15 module-access write flow ==========================
# Cloud-mirror of test_endpoints.sh's Phase 4d. 6 outside-matrix
# entries:
#
#   1. enable upsert (missing -> 200 + new row)
#   2. enable no-op (ENABLED -> ENABLED, 200, row unchanged)
#   3. disable flip (ENABLED -> DISABLED, 200)
#   4. disable no-op (DISABLED -> DISABLED, 200)
#   5. disable on a different missing module (404 MODULE_ACCESS_NOT_FOUND)
#   6. TENANT JWT against own tenant -> 403 PLATFORM_AUDIENCE_REQUIRED
#
# Tenant selection iterates the visible /tenants list, picks one whose
# /org-tree returns 200 (anchor reachability) and that has 2+ unused
# modules. Cloud and local should converge to the same seed shape so
# the chosen tenant differs only by UUID.

section "Step 6.15 — module-access write flow"

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
# Cloud mirror of test_endpoints.sh Phase 4e. Six entries. Note: cloud
# catalogue update is DEFERRED to next Phase 6 deploy cycle per the
# Step 6.13 operator note; before that deploy lands, OWNER and
# PLATFORM_ADMIN may NOT have the post-Phase-3b ORG_NODES.CONFIGURE
# grants on cloud. SUPER_ADMIN happy-path (P1) and TENANT-no-grant
# (P3 deny) are reliable; the P2 reparent and OWNER paths may produce
# 403 PERMISSION_DENIED until cloud catches up. Keep entries as-is so
# the cloud-test run signals the gap.

section "Step 6.13 — org-tree write flow"

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

        write_req "ot_flow__cascade_reject" 422 "$P1_JWT_VALUE" POST \
            "${API}/tenants/${OT_TENANT_ID}/org-tree" \
            "{\"parent_id\":\"${OT_NEW_ID}\",\"node_type\":\"REGION\",\"code\":\"te-rev-${OT_SUFFIX:0:8}\",\"name\":\"rev\"}"

        write_req "ot_flow__duplicate_code" 409 "$P1_JWT_VALUE" POST \
            "${API}/tenants/${OT_TENANT_ID}/org-tree" \
            "{\"parent_id\":\"${OT_PARENT_A}\",\"node_type\":\"STORE\",\"code\":\"${OT_CODE}\",\"name\":\"dup\"}"
    fi

    write_req "ot_flow__tenant_no_grant_deny" 403 "$T1_JWT_VALUE" POST \
        "${API}/tenants/${T1_TENANT_ID}/org-tree" \
        "{\"parent_id\":\"${T1_TENANT_ID}\",\"node_type\":\"STORE\",\"code\":\"te-deny-${OT_SUFFIX:0:8}\",\"name\":\"x\"}"
fi

# === Phase 4f — Step 6.17.3 stores write flow ===============================
# Three outside-matrix entries: POST create (UUID-suffixed name + store_code),
# PATCH rename, TENANT-side denial (random TENANT JWT with no STORES grant).
#
# Re-uses OT_TENANT_ID resolved in Phase 4e. UUID-suffixed identifiers for
# re-run safety.

section "Step 6.17.3 — stores write flow"

if [[ -z "$OT_TENANT_ID" || "$OT_TENANT_ID" == "null" ]]; then
    warn "stores write flow skipped — no resolvable tenant_id"
else
    ST_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    ST_SUFFIX="${ST_SUFFIX:0:8}"
    ST_NAME="tec-store-${ST_SUFFIX}"
    ST_CODE="TEC-${ST_SUFFIX}"

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
            '{"name":"tec-store-renamed"}'
    fi

    # TENANT OWNER happy path (multi-audience contract). Mirrors the
    # local test_endpoints.sh entry; the TENANT-no-grants deny case is
    # covered by integration RC7. Reuse T1_TENANT_ROOT_ID set during
    # fixture discovery (Step 6.21.1 setup__t1_tenant_root_fields).
    ST_OWNER_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    ST_OWNER_SUFFIX="${ST_OWNER_SUFFIX:0:8}"
    write_req "store_flow__tenant_owner_create" 201 "$T1_JWT_VALUE" POST \
        "${API}/stores" \
        "{\"tenant_id\":\"${T1_TENANT_ID}\",\"parent_org_node_id\":\"${T1_TENANT_ROOT_ID}\",\"name\":\"owner-${ST_OWNER_SUFFIX}\",\"country\":\"United States\",\"timezone\":\"America/New_York\",\"currency\":\"USD\",\"store_code\":\"ON-${ST_OWNER_SUFFIX}\",\"tax_treatment\":\"EXCLUSIVE\"}"
fi

# === Phase 4g — Step 6.17.4 stores set-status flow ==========================
# Two outside-matrix entries reusing ST_NEW_ID from Phase 4f. Order
# is "rejected first, happy second" to preserve the ACTIVE source
# state for the happy transition.

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
# Mirrors test_endpoints.sh Phase 4h. 5 outside-matrix entries: happy path,
# TENANT audience deny, unknown 404, SUPER_ADMIN protected 409, forbidden
# field 422.

section "Step 6.18.3 — roles PATCH flow"

PLATFORM_ADMIN_ROLE_ID=$(jq -r '.platform_roles.items[] | select(.code=="PLATFORM_ADMIN") | .id' \
    < "$setup_roles" | head -1)

if [[ -z "$PLATFORM_ADMIN_ROLE_ID" || "$PLATFORM_ADMIN_ROLE_ID" == "null" ]]; then
    warn "roles PATCH flow skipped — PLATFORM_ADMIN id not resolvable"
else
    write_req "role_patch__happy" 200 "$P1_JWT_VALUE" PATCH \
        "${API}/roles/${PLATFORM_ADMIN_ROLE_ID}" \
        '{"description":"cloud-test-endpoint patched description"}'

    write_req "role_patch__forbidden_status" 422 "$P1_JWT_VALUE" PATCH \
        "${API}/roles/${PLATFORM_ADMIN_ROLE_ID}" \
        '{"status":"INACTIVE"}'

    write_req "role_patch__tenant_audience_deny" 403 "$T1_JWT_VALUE" PATCH \
        "${API}/roles/${PLATFORM_ADMIN_ROLE_ID}" \
        '{"name":"tenant-jwt-should-not-land"}'
fi

write_req "role_patch__unknown_404" 404 "$P1_JWT_VALUE" PATCH \
    "${API}/roles/00000000-0000-0000-0000-000000000000" \
    '{"name":"unreachable"}'

write_req "role_patch__super_admin_protected" 409 "$P1_JWT_VALUE" PATCH \
    "${API}/roles/${PLATFORM_ROLE_ID}" \
    '{"name":"super-admin-should-not-edit"}'

# === Phase 4i — Step 6.20.2 /me/can-do target_anchor validation =============
# Mirrors test_endpoints.sh Phase 4i. Single outside-matrix entry: GET
# /me/can-do with a hyphen-bearing target_anchor (UUID shape) -> 422 from
# Pydantic, BEFORE the ltree CAST runs. Closes FN-AB-61.
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

if [[ "$RUNTIME" -gt 120 ]]; then
    warn "Runtime exceeded 120s threshold (${RUNTIME}s); investigate \
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
