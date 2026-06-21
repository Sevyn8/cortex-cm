#!/usr/bin/env bash
# =============================================================================
# scripts/smoke_curl.sh
# =============================================================================
# Lightweight cloud-ready smoke check. Hits a representative slice of
# endpoints and asserts each returns the expected HTTP status code.
#
# COMPLEMENTS test_endpoints.sh — that script is the comprehensive local
# integration matrix (~150 calls across 4 callers, mints fresh JWTs from
# local DB, saves response bodies). This script is intentionally smaller
# (~15 calls), deployment-target-agnostic, and consumes pre-existing JWT
# files. Use after cloud deploys to confirm the new revision is healthy
# end-to-end.
#
# USAGE:
#   ./scripts/smoke_curl.sh <base-url>
#
# Examples:
#   ./scripts/smoke_curl.sh http://localhost:8000
#   ./scripts/smoke_curl.sh https://admin-backend-315143921819.asia-south1.run.app
#
# JWT EXPECTATIONS:
#   This script consumes pre-minted JWT files from scripts/jwt/tokens/.
#   PLATFORM JWT is required (universal across local + cloud). TENANT JWT
#   is optional — enables the cross-tenant 404 + tenant-filter assertions.
#
#   Mint local JWTs:
#     ./scripts/jwt/generate_7d.sh anjali@ithina.ai          # platform
#     ./scripts/jwt/generate_7d.sh marcus.t@bucees.com       # tenant (local)
#
#   For cloud TENANT JWTs (tenant_id differs between local + cloud), the
#   workflow is intentionally manual today. See the "Cloud-tenant JWT
#   minting" section in docs/build-step-workflow.md for the inline pattern
#   referenced from prompts/step-4_4-cloud-run-deploy-dev.md section 5.
#
#   This is captured as a tooling improvement to land later — see
#   build-step-workflow.md "Future improvements".
#
# DEFAULT JWT FILES:
#   scripts/jwt/tokens/anjali-7d.jwt        (PLATFORM, required)
#   scripts/jwt/tokens/marcus-t-7d.jwt      (TENANT, optional — local only)
#
# Override via env:
#   PJWT_FILE=path/to/platform.jwt TJWT_FILE=path/to/tenant.jwt \
#     ./scripts/smoke_curl.sh <url>
#
# WHAT'S CHECKED (69 endpoints, in order):
#   1.  GET /api/v1/health (no auth)                            → 200
#   2.  GET /api/v1/ready (no auth)                             → 200
#   3.  GET /api/v1/tenants (no auth)                           → 401
#   4.  GET /api/v1/tenants (PLATFORM)                          → 200
#   5.  GET /api/v1/tenants?sort=num_users_active_desc&limit=5 (PLATFORM)  → 200
#       (Step 6.5 dashboard's Top Tenants panel — Step 6.4 sort key)
#   6.  GET /api/v1/tenants/stats (PLATFORM)                    → 200
#   7.  GET /api/v1/platform-users (PLATFORM)                   → 200
#   8.  GET /api/v1/tenant-users (PLATFORM)                     → 200
#   9.  GET /api/v1/lookups (PLATFORM)                          → 200
#   10. GET /api/v1/roles (PLATFORM)                            → 200
#   11. GET /api/v1/permissions (PLATFORM)                      → 200
#   12. GET /api/v1/permission-matrix (PLATFORM)                → 200
#   12a. GET /api/v1/roles/{super_admin_id} (PLATFORM)          → 200 (Step 6.18.2)
#        Resolves SUPER_ADMIN id from /roles output at runtime.
#   13. GET /api/v1/dashboard/fleet-stats (PLATFORM)            → 200
#   14. GET /api/v1/dashboard/governance-stats (PLATFORM)       → 200
#   15. GET /api/v1/module-access/modules (PLATFORM)            → 200
#   16. GET /api/v1/module-access/matrix (PLATFORM)             → 200
#   17. GET /api/v1/me/permissions (PLATFORM)                   → 200  (Step 6.9.2)
#   18. GET /api/v1/me/can-do?module=ADMIN&... (PLATFORM)       → 200  (Step 6.9.2)
#   18a. GET /api/v1/me/can-do?...&target_anchor=<UUID-with-hyphens> → 422
#        Pydantic pattern rejects non-ltree input before the gate runs
#        (Step 6.20.2; cloud-reported failure shape v0.1.17).
#   19. GET /api/v1/platform-users (TENANT)                     → 403  (only if TJWT)
#   20. GET /api/v1/roles (TENANT)                              → 200  (only if TJWT)
#   21. GET /api/v1/permission-matrix (TENANT)                  → 200  (only if TJWT)
#   21a. GET /api/v1/roles/{owner_id} (TENANT)                  → 200  (Step 6.18.2; only if TJWT)
#   21b. GET /api/v1/roles/{super_admin_id} (TENANT)            → 404  ROLE_NOT_FOUND
#        (audience filter at app layer; D-17; Step 6.18.2; only if TJWT)
#   22. POST /api/v1/tenants (PLATFORM, ts-suffixed name)       → 201  (Step 6.11.2)
#   22b. GET /api/v1/tenants/{captured_id} (PLATFORM, roundtrip) → 200  (Step 6.20.1)
#        POST→GET regression lock: pre-6.20.1, POST succeeded but
#        GET 404'd because no tenant-root org_node existed for the
#        new tenant. Captures the bug that motivated this step.
#   23. PATCH /api/v1/tenants/{captured_id} (PLATFORM)          → 200  (Step 6.11.2)
#   24. POST /api/v1/tenants/{captured_id}/suspend (PLATFORM)   → 200  (Step 6.11.2)
#   25. POST /api/v1/tenants/{captured_id}/activate (PLATFORM)  → 200  (Step 6.11.2)
#   26. POST /api/v1/tenants (TENANT)                           → 403  PLATFORM_AUDIENCE_REQUIRED
#                                                                       (only if TJWT)
#   27. POST /api/v1/tenant-users (PLATFORM)                    → 201  INVITED (Step 6.10.1)
#   28. PATCH /api/v1/tenant-users/{captured_id} (PLATFORM)     → 200  (Step 6.10.1)
#   29. POST .../suspend on INVITED (PLATFORM)                  → 409  INVALID_STATE_TRANSITION
#                                                                       (INVITED -> SUSPENDED structurally rejected
#                                                                        by ck_tenant_users_auth0_sub_consistency;
#                                                                        smoke verifies the 409 mapping. The 200
#                                                                        happy path needs an ACTIVE user, which
#                                                                        requires DB-side promotion outside the
#                                                                        smoke surface — covered by integration
#                                                                        tests S1/S2.)
#   30. POST .../activate on INVITED (PLATFORM)                 → 409  INVALID_STATE_TRANSITION
#                                                                       (INVITED -> ACTIVE is the Auth0 invite-accept
#                                                                        callback flow, out of v0 scope.
#                                                                        Endpoint contract verified at the 409 path;
#                                                                        the 200 happy path is covered by
#                                                                        integration tests A1/A2.)
#   31. PATCH /api/v1/tenant-users/{self_id} (TENANT)           → 403  SELF_EDIT_FORBIDDEN
#                                                                       (only if TJWT)
#   32. POST .../module-access/{tid}/{module}/enable (PLATFORM, missing) → 200 (Step 6.15)
#   33. POST .../module-access/{tid}/{module}/enable (PLATFORM, noop)    → 200 (Step 6.15)
#   34. POST .../module-access/{tid}/{module}/disable (PLATFORM, flip)   → 200 (Step 6.15)
#   35. POST .../module-access/{tid}/{module}/disable (PLATFORM, noop)   → 200 (Step 6.15)
#   36. POST .../module-access/{tid}/{other}/disable (PLATFORM, missing) → 404
#       MODULE_ACCESS_NOT_FOUND (Step 6.15)
#   37. POST .../module-access/{tid}/{module}/enable (TENANT)            → 403
#       PLATFORM_AUDIENCE_REQUIRED (Step 6.15) — only if TJWT
#   38. POST /api/v1/tenant-users multi-anchor (PLATFORM)        → 201  (Step 6.14)
#   39. PATCH /api/v1/tenant-users diff-replace (PLATFORM)       → 200  (Step 6.14)
#   40. PATCH /api/v1/tenant-users no-op (PLATFORM)              → 200  (Step 6.14)
#   41. POST /api/v1/tenant-users invalid org_node_id (PLATFORM) → 422  INVALID_ORG_NODE (Step 6.14)
#   42. POST /api/v1/tenants/{tid}/org-tree add STORE (PLATFORM)         → 201 (Step 6.13)
#   43. PATCH /api/v1/tenants/{tid}/org-tree/{node_id} rename (PLATFORM) → 200 (Step 6.13)
#   44. PATCH /org-tree reparent (PLATFORM)                              → 200 (Step 6.13)
#   45. POST /org-tree cascade-order reject (REGION under STORE)         → 422 INVALID_PARENT_NODE_TYPE (Step 6.13)
#   46. POST /org-tree duplicate-code reject                             → 409 DUPLICATE_ORG_NODE_CODE (Step 6.13)
#   47. GET /api/v1/stores (PLATFORM)                           → 200 (Step 6.17.2)
#   48. GET /api/v1/stores/{first_id} (PLATFORM)                → 200 (Step 6.17.2)
#                                                                       (skipped if list returns no rows)
#   49. POST /api/v1/stores (PLATFORM, parent_org_node_id=tenant_root) → 201 (Step 6.21.2)
#   49a. POST /stores response carries org_node_id (UUID-shaped)        → 201 ok (Step 6.21.2)
#   50. PATCH /api/v1/stores/{captured_id} rename (PLATFORM)             → 200 (Step 6.17.3)
#   51. POST /api/v1/stores (TENANT OWNER for own tenant)                → 201 (Step 6.17.3)
#       (multi-audience happy path — TENANT OWNER holds
#        ADMIN.STORES.CONFIGURE.TENANT via the Step 6.17.1 seed; the
#        deny path for TENANT-no-grants is exercised by integration
#        test RC7. only if TJWT)
#   52. POST /api/v1/stores/{captured_id}/set-status ACTIVE→OPENING     → 409 INVALID_STATE_TRANSITION (Step 6.17.4)
#   53. POST /api/v1/stores/{captured_id}/set-status ACTIVE→INACTIVE     → 200 + status=INACTIVE (Step 6.17.4)
#       Order: rejected first (ACTIVE state preserved from POST), happy second (flips to INACTIVE).
#       Single store reused across both calls; UUID-suffixed identifiers from #49 keep re-runs clean.
#   53a. PATCH /api/v1/roles/{platform_admin_id} description (PJWT SUPER_ADMIN)  → 200 (Step 6.18.3)
#   53b. PATCH /api/v1/roles/{owner_id}          name (TJWT OWNER)               → 403 PLATFORM_AUDIENCE_REQUIRED
#                                                                                  (only if TJWT)
#   53c. PATCH /api/v1/roles/{unknown_uuid}      name (PJWT)                     → 404 ROLE_NOT_FOUND
#   53d. PATCH /api/v1/roles/{super_admin_id}    name (PJWT)                     → 409 SUPER_ADMIN_PROTECTED
#   53e. PATCH /api/v1/roles/{platform_admin_id} body forbidden status (PJWT)    → 422
#   53f. GET /api/v1/tenants/{tid}/org-tree response carries     → tenant_root_id /
#        tenant_root_id, tenant_root_code, tenant_root_path         _code / _path
#        on the same fetch already used by the 6.14 anchor              all present (Step 6.21.1)
#        resolution above.
#   54. GET /api/v1/audit/activities?limit=5 (PLATFORM)         → 200 (Step 6.16.3)
#   55. GET /api/v1/audit/activities?cursor=<bad> (PLATFORM)    → 422 INVALID_CURSOR (Step 6.16.3)
#   56. GET /api/v1/audit/activities?limit=5 (TENANT)           → 200 (Step 6.16.3, only if TJWT)
#   57. Health-version match check                              → version field in body
#
# NOTE on write-endpoint state: each run creates one tenant and leaves it in
# ACTIVE state. Names are UUID-suffixed so re-runs don't collide. Manual
# cleanup is the operator's responsibility if running repeatedly against
# a long-lived target.
#
# EXIT CODES:
#   0   all checks passed
#   1   one or more checks failed (bodies dumped to stdout for the failures)
#
# Maintenance: extend by 1-3 lines per new build step. Add a `req` line for
# each new canonical endpoint. Don't grow this past ~25 endpoints — that's
# what test_endpoints.sh is for.
# =============================================================================

set -uo pipefail

# === Args ===================================================================
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <base-url>" >&2
    echo "Example: $0 http://localhost:8000" >&2
    exit 2
fi

BASE_URL="$1"
API="${BASE_URL}/api/v1"

# === JWT discovery ==========================================================
PJWT_FILE="${PJWT_FILE:-scripts/jwt/tokens/anjali-7d.jwt}"
TJWT_FILE="${TJWT_FILE:-scripts/jwt/tokens/marcus-t-7d.jwt}"

if [[ ! -s "$PJWT_FILE" ]]; then
    echo "ERROR: PLATFORM JWT not found at $PJWT_FILE" >&2
    echo "Mint with: ./scripts/jwt/generate_7d.sh anjali@ithina.ai" >&2
    exit 2
fi
PJWT="$(cat "$PJWT_FILE")"

# TENANT JWT is optional. Load if present; tag tenant tests as enabled.
TJWT=""
TENANT_TESTS_ENABLED=0
if [[ -s "$TJWT_FILE" ]]; then
    TJWT="$(cat "$TJWT_FILE")"
    TENANT_TESTS_ENABLED=1
fi

# === Color setup ============================================================
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    G=$'\033[1;32m'; R=$'\033[1;31m'; Y=$'\033[1;33m'; D=$'\033[2m'; N=$'\033[0m'
else
    G=""; R=""; Y=""; D=""; N=""
fi

# === Counters ===============================================================
PASS=0
FAIL=0
declare -a FAILURES=()

# === req <label> <expected_status> <jwt> <method> <path> ====================
# JWT arg: empty string for no-auth, "$PJWT" or "$TJWT" otherwise.
# Path is appended to $API.
# On failure, prints the response body so debugging doesn't require digging
# through saved files (this is the lightweight smoke posture).
req() {
    local label="$1" expected="$2" jwt="$3" method="$4" path="$5"

    local -a hdrs=("-H" "Accept: application/json")
    if [[ -n "$jwt" ]]; then
        hdrs+=("-H" "Authorization: Bearer ${jwt}")
    fi

    local body status
    # Capture body and status separately. -w writes status to stderr-of-curl
    # (which we redirect to stdout via process substitution).
    body=$(curl -s -o /dev/stdout -w "%{http_code}" \
        -X "$method" "${hdrs[@]}" "${API}${path}" 2>/dev/null || echo "000")
    # The trick: -w "%{http_code}" appends status to body. Split them.
    status="${body: -3}"
    body="${body%???}"

    if [[ "$status" == "$expected" ]]; then
        printf "  ${G}[✓ %s]${N} %s %s ${D}— %s${N}\n" "$status" "$method" "$path" "$label"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected %s]${N} %s %s ${D}— %s${N}\n" \
            "$status" "$expected" "$method" "$path" "$label"
        FAIL=$((FAIL + 1))
        FAILURES+=("$label (got $status, expected $expected)")
        # Print body (truncated to 5 lines) so failure is debuggable inline.
        printf '%s\n' "$body" | head -5 | sed 's/^/        ↳ /'
    fi
}

# === Run smoke ==============================================================
echo
echo "smoke_curl.sh — target: ${BASE_URL}"
echo "                PLATFORM JWT: ${PJWT_FILE}"
if [[ "$TENANT_TESTS_ENABLED" -eq 1 ]]; then
    echo "                TENANT JWT:   ${TJWT_FILE}"
else
    echo "                TENANT JWT:   ${Y}(skipped — file not found)${N}"
fi
echo

# Public, no auth.
req "health"          200 ""       GET /health
req "ready"           200 ""       GET /ready

# No-auth on a protected endpoint must return 401, not 500.
req "tenants_no_auth" 401 ""       GET /tenants

# PLATFORM-as-caller, every list endpoint.
req "tenants_list"           200 "$PJWT" GET /tenants
# Step 6.5 dashboard's Top Tenants panel: top 5 by active-user count.
# Validates the Step 6.4 num_users_active_desc sort key end-to-end —
# without it, the dashboard would receive 400 INVALID_SORT_KEY.
req "tenants_top_by_users"   200 "$PJWT" GET "/tenants?sort=num_users_active_desc&limit=5"
req "tenants_stats"          200 "$PJWT" GET /tenants/stats
req "platform_users_list"    200 "$PJWT" GET /platform-users
req "tenant_users_list"      200 "$PJWT" GET /tenant-users
# Stores (Step 6.17.2). GET list, then GET detail via the first
# returned id. Detail is conditionally skipped if list returns no
# rows (target DB has no stores) — same defensive pattern as the
# TENANT JWT conditional below.
req "stores_list"            200 "$PJWT" GET /stores
STORES_FIRST_ID="$(curl -s -H "Authorization: Bearer ${PJWT}" \
    "${API}/stores?limit=1" \
    | python3 -c 'import sys,json
d=json.load(sys.stdin)
print(d["items"][0]["id"] if d.get("items") else "")' 2>/dev/null || echo "")"
if [[ -n "$STORES_FIRST_ID" ]]; then
    req "stores_detail"          200 "$PJWT" GET "/stores/${STORES_FIRST_ID}"
else
    echo "  ${Y}[!] stores_detail skipped — list returned no rows${N}"
fi
req "lookups_batch"          200 "$PJWT" GET "/lookups?lists=tenant_tier,tenant_industry"

# RBAC endpoints (Step 6.1).
req "roles_list_platform"          200 "$PJWT" GET /roles
req "permissions_list"             200 "$PJWT" GET /permissions
req "permission_matrix_platform"   200 "$PJWT" GET /permission-matrix

# Step 6.18.2 — GET /api/v1/roles/{role_id} (E7 detail endpoint).
# Resolve the seeded SUPER_ADMIN id at runtime (UUIDv7 generated at
# seed time, differs per environment). Resolve OWNER similarly for
# the TENANT-side happy path. Both calls 404 cleanly if the lookup
# returns empty, surfacing the seed-state issue rather than passing.
SUPER_ADMIN_ID="$(curl -s -H "Authorization: Bearer ${PJWT}" \
    "${API}/roles?limit=50" 2>/dev/null \
    | jq -r '.platform_roles.items[] | select(.code == "SUPER_ADMIN") | .id' \
    | head -n1)"
if [[ -n "$SUPER_ADMIN_ID" && "$SUPER_ADMIN_ID" != "null" ]]; then
    req "role_detail_platform"     200 "$PJWT" GET "/roles/${SUPER_ADMIN_ID}"
else
    echo "  ${Y}[!] role_detail_platform skipped — SUPER_ADMIN not found${N}"
fi

# Step 6.18.3 — PATCH /api/v1/roles/{role_id} (E8 role-edit endpoint).
# Resolve a non-SUPER_ADMIN PLATFORM-audience role id for the happy
# path (PLATFORM_ADMIN). SUPER_ADMIN is locked from PATCH per LD12.
PLATFORM_ADMIN_ID="$(curl -s -H "Authorization: Bearer ${PJWT}" \
    "${API}/roles?limit=50" 2>/dev/null \
    | jq -r '.platform_roles.items[] | select(.code == "PLATFORM_ADMIN") | .id' \
    | head -n1)"
if [[ -n "$PLATFORM_ADMIN_ID" && "$PLATFORM_ADMIN_ID" != "null" ]]; then
    # Happy path: PATCH description on PLATFORM_ADMIN. Idempotent at
    # smoke scale (same description across re-runs).
    PATCH_HAPPY_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d '{"description":"smoke-patch-description"}' \
        "${API}/roles/${PLATFORM_ADMIN_ID}" 2>/dev/null)"
    if [[ "$PATCH_HAPPY_STATUS" == "200" ]]; then
        printf "  ${G}[✓ 200]${N} PATCH /roles/{id} ${D}— role_patch_happy${N}\n"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 200]${N} PATCH /roles/{id} ${D}— role_patch_happy${N}\n" "$PATCH_HAPPY_STATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("role_patch_happy (got $PATCH_HAPPY_STATUS, expected 200)")
    fi

    # Forbidden field: extra='forbid' on status -> 422.
    PATCH_FORBIDDEN_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d '{"status":"INACTIVE"}' \
        "${API}/roles/${PLATFORM_ADMIN_ID}" 2>/dev/null)"
    if [[ "$PATCH_FORBIDDEN_STATUS" == "422" ]]; then
        printf "  ${G}[✓ 422]${N} PATCH /roles/{id} ${D}— role_patch_forbidden_field${N}\n"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 422]${N} PATCH /roles/{id} ${D}— role_patch_forbidden_field${N}\n" "$PATCH_FORBIDDEN_STATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("role_patch_forbidden_field (got $PATCH_FORBIDDEN_STATUS, expected 422)")
    fi
else
    echo "  ${Y}[!] role_patch_happy + role_patch_forbidden_field skipped — PLATFORM_ADMIN not found${N}"
fi

# Unknown role_id -> 404.
PATCH_UNKNOWN_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
    -H "Authorization: Bearer ${PJWT}" \
    -H "Content-Type: application/json" \
    -d '{"name":"smoke-unreachable"}' \
    "${API}/roles/00000000-0000-0000-0000-000000000000" 2>/dev/null)"
if [[ "$PATCH_UNKNOWN_STATUS" == "404" ]]; then
    printf "  ${G}[✓ 404]${N} PATCH /roles/{unknown} ${D}— role_patch_unknown${N}\n"
    PASS=$((PASS + 1))
else
    printf "  ${R}[✗ %s, expected 404]${N} PATCH /roles/{unknown} ${D}— role_patch_unknown${N}\n" "$PATCH_UNKNOWN_STATUS"
    FAIL=$((FAIL + 1))
    FAILURES+=("role_patch_unknown (got $PATCH_UNKNOWN_STATUS, expected 404)")
fi

# SUPER_ADMIN protected -> 409 SUPER_ADMIN_PROTECTED.
if [[ -n "$SUPER_ADMIN_ID" && "$SUPER_ADMIN_ID" != "null" ]]; then
    PATCH_SA_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d '{"name":"smoke-should-not-land"}' \
        "${API}/roles/${SUPER_ADMIN_ID}" 2>/dev/null)"
    if [[ "$PATCH_SA_STATUS" == "409" ]]; then
        printf "  ${G}[✓ 409]${N} PATCH /roles/{super_admin} ${D}— role_patch_super_admin_protected${N}\n"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 409]${N} PATCH /roles/{super_admin} ${D}— role_patch_super_admin_protected${N}\n" "$PATCH_SA_STATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("role_patch_super_admin_protected (got $PATCH_SA_STATUS, expected 409)")
    fi
fi

# Dashboard endpoints (Step 6.5). Card-shaped responses (D-30 exception).
# fleet-stats returns 4 cards (active_tenants, platform_users, stores,
# mrr_aggregated); governance-stats returns 4 cards, 3 of which are
# stubbed (available: false) in v0.
req "dashboard_fleet_stats"        200 "$PJWT" GET /dashboard/fleet-stats
req "dashboard_governance_stats"   200 "$PJWT" GET /dashboard/governance-stats

# Module Access endpoints (Step 6.7). /modules returns 6 cards in locked
# order; /matrix returns the tenant × module grid (paginated).
req "module_access_modules"        200 "$PJWT" GET /module-access/modules
req "module_access_matrix"         200 "$PJWT" GET "/module-access/matrix?limit=10"

# /me/* endpoints (Step 6.9.2). /permissions returns the caller's full grant
# set (always an array); /can-do is a server-authoritative single-permission
# check. Anjali (SUPER_ADMIN PLATFORM JWT) holds ADMIN.USERS.VIEW.GLOBAL so
# the can-do probe returns allowed=true; smoke just asserts 200.
req "me_permissions_platform"      200 "$PJWT" GET /me/permissions
req "me_can_do_platform"           200 "$PJWT" GET "/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=GLOBAL"

# Step 6.20.2: malformed target_anchor (UUID with hyphens; cloud-reported
# failure shape per v0.1.17 / admin-backend-00018-46f). Pre-fix this 500'd
# because psycopg.errors.SyntaxError bubbled to the generic 500 envelope.
# Post-fix the Pydantic pattern validator rejects at 422 BEFORE the gate
# dependency runs. The check is JWT-type-agnostic (Pydantic Query validation
# fires before auth dispatch); PJWT runs unconditionally so no TJWT gate.
req "me_can_do_ltree_validation_422" 422 "$PJWT" GET "/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=GLOBAL&target_anchor=019df261-b87c-7d3e-ab9e-dcf26259cec6"

# Audit endpoints (Step 6.16.3). /audit/activities is the cursor-paginated
# list of audit rows; PLATFORM callers see merged UNION across both audit
# tables, TENANT callers see RLS-scoped tenant-table rows only. List + bad
# cursor + tenant-side list cover the wire surface.
req "audit_list_platform"          200 "$PJWT" GET "/audit/activities?limit=5"
req "audit_list_malformed_cursor"  422 "$PJWT" GET "/audit/activities?cursor=not-valid-base64-json"

# TENANT-as-caller, only if JWT available.
if [[ "$TENANT_TESTS_ENABLED" -eq 1 ]]; then
    # PLATFORM-only gate must reject TENANT.
    req "platform_users_tenant_403" 403 "$TJWT" GET /platform-users
    # TENANT can list roles (audience-filtered to TENANT-audience only).
    req "roles_list_tenant"         200 "$TJWT" GET /roles
    # TENANT can read the matrix (column count = 12 for TENANT vs 15 for PLATFORM,
    # but smoke just asserts 200).
    req "permission_matrix_tenant"  200 "$TJWT" GET /permission-matrix

    # Step 6.18.2 — GET /api/v1/roles/{role_id} TENANT-side coverage.
    # Happy: TENANT JWT reads OWNER (TENANT-audience role) -> 200.
    # Deny:  TENANT JWT reads SUPER_ADMIN (PLATFORM-audience) -> 404
    # ROLE_NOT_FOUND (audience filter applied at app layer; D-17).
    OWNER_ID="$(curl -s -H "Authorization: Bearer ${TJWT}" \
        "${API}/roles?limit=50" 2>/dev/null \
        | jq -r '.tenant_roles.items[] | select(.code == "OWNER") | .id' \
        | head -n1)"
    if [[ -n "$OWNER_ID" && "$OWNER_ID" != "null" ]]; then
        req "role_detail_tenant"        200 "$TJWT" GET "/roles/${OWNER_ID}"
    else
        echo "  ${Y}[!] role_detail_tenant skipped — OWNER not found${N}"
    fi
    if [[ -n "$SUPER_ADMIN_ID" && "$SUPER_ADMIN_ID" != "null" ]]; then
        req "role_detail_cross_audience_404" \
                                       404 "$TJWT" GET "/roles/${SUPER_ADMIN_ID}"
    else
        echo "  ${Y}[!] role_detail_cross_audience_404 skipped — SUPER_ADMIN id not resolved${N}"
    fi

    # Step 6.16.3: TENANT audit list (RLS narrows to own-tenant rows;
    # 200 with empty or non-empty items array). Cursor pattern + limit
    # parameters work identically across audiences.
    req "audit_list_tenant"          200 "$TJWT" GET "/audit/activities?limit=5"

    # Step 6.18.3 — PATCH refused on TENANT JWT (Layer 1 audience deny).
    if [[ -n "$OWNER_ID" && "$OWNER_ID" != "null" ]]; then
        PATCH_TENANT_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${TJWT}" \
            -H "Content-Type: application/json" \
            -d '{"name":"smoke-tenant-cannot"}' \
            "${API}/roles/${OWNER_ID}" 2>/dev/null)"
        if [[ "$PATCH_TENANT_STATUS" == "403" ]]; then
            printf "  ${G}[✓ 403]${N} PATCH /roles/{id} (TJWT) ${D}— role_patch_tenant_audience_deny${N}\n"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 403]${N} PATCH /roles/{id} (TJWT) ${D}— role_patch_tenant_audience_deny${N}\n" "$PATCH_TENANT_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("role_patch_tenant_audience_deny (got $PATCH_TENANT_STATUS, expected 403)")
        fi
    fi
fi

# === Step 6.11.2 — tenants write flow (PLATFORM) ============================
# Chain: POST create -> PATCH name change -> POST /suspend -> POST /activate.
# Each call uses the id returned by the create. Names UUID-suffixed so the
# same script can run repeatedly without 409 DUPLICATE_TENANT_NAME.
#
# The first call is the only one that needs the response body; subsequent
# calls reuse `req` for status-only assertions.

echo
echo "  ${D}--- Step 6.11.2 write flow (PLATFORM) ---${N}"

SMOKE_TENANT_SUFFIX="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
SMOKE_TENANT_NAME="smoke-${SMOKE_TENANT_SUFFIX}"

CREATE_RESP="$(curl -s -w "\n%{http_code}" -X POST \
    -H "Authorization: Bearer ${PJWT}" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d "$(cat <<EOF
{
  "name": "${SMOKE_TENANT_NAME}",
  "region": "US",
  "tier": "ENTERPRISE",
  "industry": "GROCERY",
  "country": "United States",
  "primary_contact_name": "Smoke Operator",
  "contact_email": "smoke-${SMOKE_TENANT_SUFFIX}@test.example.com",
  "number_of_stores": 5,
  "number_of_stores_as_of_date": "2026-01-01"
}
EOF
)" \
    "${API}/tenants" 2>/dev/null)"

CREATE_STATUS="$(printf '%s' "$CREATE_RESP" | tail -n1)"
CREATE_BODY="$(printf '%s' "$CREATE_RESP" | sed '$d')"

if [[ "$CREATE_STATUS" == "201" ]]; then
    printf "  ${G}[✓ %s]${N} POST /tenants ${D}— write_flow__create (name=%s)${N}\n" \
        "$CREATE_STATUS" "$SMOKE_TENANT_NAME"
    PASS=$((PASS + 1))
    SMOKE_TENANT_ID="$(printf '%s' "$CREATE_BODY" | jq -r '.id' 2>/dev/null || echo "")"
else
    printf "  ${R}[✗ %s, expected 201]${N} POST /tenants ${D}— write_flow__create${N}\n" "$CREATE_STATUS"
    printf '%s\n' "$CREATE_BODY" | head -5 | sed 's/^/        ↳ /'
    FAIL=$((FAIL + 1))
    FAILURES+=("write_flow__create (got $CREATE_STATUS, expected 201)")
    SMOKE_TENANT_ID=""
fi

if [[ -n "$SMOKE_TENANT_ID" && "$SMOKE_TENANT_ID" != "null" ]]; then
    # Step 6.20.1 — POST then GET roundtrip. Pre-fix the next call
    # returned 404 because POST did not provision a tenant-root
    # org_node and the GET handler depends on get_tenant_anchor.
    ROUNDTRIP_RESP="$(curl -s -w '\n%{http_code}' \
        -H "Authorization: Bearer ${PJWT}" \
        "${API}/tenants/${SMOKE_TENANT_ID}" 2>/dev/null)"
    ROUNDTRIP_STATUS="$(printf '%s' "$ROUNDTRIP_RESP" | tail -n1)"
    ROUNDTRIP_BODY="$(printf '%s' "$ROUNDTRIP_RESP" | sed '$d')"
    ROUNDTRIP_ID="$(printf '%s' "$ROUNDTRIP_BODY" | jq -r '.id' 2>/dev/null || echo "")"
    if [[ "$ROUNDTRIP_STATUS" == "200" && "$ROUNDTRIP_ID" == "$SMOKE_TENANT_ID" ]]; then
        printf "  ${G}[✓ %s]${N} GET /tenants/{id} ${D}— write_flow__post_get_roundtrip${N}\n" "$ROUNDTRIP_STATUS"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 200 with id=%s]${N} GET /tenants/{id} ${D}— write_flow__post_get_roundtrip${N}\n" "$ROUNDTRIP_STATUS" "$SMOKE_TENANT_ID"
        printf '%s\n' "$ROUNDTRIP_BODY" | head -5 | sed 's/^/        ↳ /'
        FAIL=$((FAIL + 1))
        FAILURES+=("write_flow__post_get_roundtrip (got $ROUNDTRIP_STATUS, expected 200 with id=$SMOKE_TENANT_ID)")
    fi

    # PATCH the just-created tenant's contact name.
    PATCH_BODY='{"primary_contact_name":"Smoke Patched"}'
    PATCH_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d "$PATCH_BODY" \
        "${API}/tenants/${SMOKE_TENANT_ID}" 2>/dev/null)"
    if [[ "$PATCH_STATUS" == "200" ]]; then
        printf "  ${G}[✓ %s]${N} PATCH /tenants/{id} ${D}— write_flow__patch${N}\n" "$PATCH_STATUS"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 200]${N} PATCH /tenants/{id} ${D}— write_flow__patch${N}\n" "$PATCH_STATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("write_flow__patch (got $PATCH_STATUS, expected 200)")
    fi

    req "write_flow__suspend"  200 "$PJWT" POST "/tenants/${SMOKE_TENANT_ID}/suspend"
    req "write_flow__activate" 200 "$PJWT" POST "/tenants/${SMOKE_TENANT_ID}/activate"
fi

# TENANT-as-caller audience-deny on POST (only if TJWT present).
if [[ "$TENANT_TESTS_ENABLED" -eq 1 ]]; then
    AUD_DENY_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        -H "Authorization: Bearer ${TJWT}" \
        -H "Content-Type: application/json" \
        -d '{"name":"tenant-jwt-should-not-land","region":"US","tier":"ENTERPRISE","industry":"GROCERY","country":"United States","primary_contact_name":"X","contact_email":"x@test.example.com","number_of_stores":1,"number_of_stores_as_of_date":"2026-01-01"}' \
        "${API}/tenants" 2>/dev/null)"
    if [[ "$AUD_DENY_STATUS" == "403" ]]; then
        printf "  ${G}[✓ %s]${N} POST /tenants ${D}— write_flow__audience_deny (TENANT)${N}\n" "$AUD_DENY_STATUS"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 403]${N} POST /tenants ${D}— write_flow__audience_deny${N}\n" "$AUD_DENY_STATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("write_flow__audience_deny (got $AUD_DENY_STATUS, expected 403)")
    fi
fi

# === Step 6.10.1 — tenant-users write flow ===================================
# Chain: POST create -> PATCH name change -> POST /suspend -> POST /activate
# -> TENANT self-edit deny. Suspend / activate against an INVITED user are
# expected to return 409 INVALID_STATE_TRANSITION (the 200 happy paths
# require an ACTIVE user, which the smoke can't promote without DB access
# — covered by integration tests). All 4 endpoints' gate + repo + matrix
# logic still fire end-to-end.

echo
echo "  ${D}--- Step 6.10.1 tenant-users write flow ---${N}"

# Resolve seed tenant_id from /tenants list (deterministic — Buc-ee's by
# name). The tenant_id varies across environments (local vs cloud seed
# runs), so resolving by name keeps the smoke environment-agnostic.
TU_TENANT_ID="$(curl -s \
    -H "Authorization: Bearer ${PJWT}" \
    -H "Accept: application/json" \
    "${API}/tenants?limit=50" 2>/dev/null \
    | jq -r '.items[] | select(.name == "Buc-ee'\''s") | .id' \
    | head -n1)"

if [[ -z "$TU_TENANT_ID" || "$TU_TENANT_ID" == "null" ]]; then
    # Fall back to the first visible tenant — smoke shouldn't fail
    # entirely if the seed shape shifts later.
    TU_TENANT_ID="$(curl -s \
        -H "Authorization: Bearer ${PJWT}" \
        "${API}/tenants?limit=1" 2>/dev/null \
        | jq -r '.items[0].id')"
fi

# Resolve a TENANT-audience role_id (OWNER, seeded). Same lookup
# pattern — pick by code so the smoke is shape-stable across envs.
TU_ROLE_ID="$(curl -s \
    -H "Authorization: Bearer ${PJWT}" \
    "${API}/roles?limit=50" 2>/dev/null \
    | jq -r '.tenant_roles.items[] | select(.code == "OWNER") | .id' \
    | head -n1)"

# Step 6.14: resolve two distinct anchor org_nodes from the tenant's
# org-tree. The TENANT root is never in /tenants/{id}/org-tree (per
# the doc); ``tree[0].id`` and ``tree[0].children[0].id`` give two
# valid descendant anchors for the multi-anchor smoke test below.
TU_TREE_RESP="$(curl -s \
    -H "Authorization: Bearer ${PJWT}" \
    "${API}/tenants/${TU_TENANT_ID}/org-tree" 2>/dev/null)"
TU_ANCHOR_A="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tree[0].id // empty')"
TU_ANCHOR_B="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tree[0].children[0].id // .tree[0].id // empty')"

# Step 6.21.1: verify the new top-level fields land in the GET /org-tree
# response. ``tenant_root_id`` is the org_nodes.id of the tenant-root
# (distinct from ``tenant_id``); frontend uses it as ``parent_id`` on
# POST /org-tree. Looser assertion (local): non-empty UUID-shaped
# string. Cloud smoke (test_endpoints_cloud.sh) asserts the exact
# code/path for Buc-ee's.
TU_TENANT_ROOT_ID="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tenant_root_id // empty')"
TU_TENANT_ROOT_CODE="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tenant_root_code // empty')"
TU_TENANT_ROOT_PATH="$(printf '%s' "$TU_TREE_RESP" | jq -r '.tenant_root_path // empty')"
if [[ -n "$TU_TENANT_ROOT_ID" \
      && "$TU_TENANT_ROOT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ \
      && -n "$TU_TENANT_ROOT_CODE" \
      && -n "$TU_TENANT_ROOT_PATH" ]]; then
    printf "  ${G}[✓ 200]${N} GET /tenants/{id}/org-tree carries tenant_root_id/code/path ${D}— org_tree__tenant_root_fields${N}\n"
    PASS=$((PASS + 1))
else
    printf "  ${R}[✗ tenant_root_id=%s code=%s path=%s]${N} GET /org-tree missing tenant_root_* fields ${D}— org_tree__tenant_root_fields${N}\n" \
        "${TU_TENANT_ROOT_ID:-null}" "${TU_TENANT_ROOT_CODE:-null}" "${TU_TENANT_ROOT_PATH:-null}"
    FAIL=$((FAIL + 1))
    FAILURES+=("org_tree__tenant_root_fields (got id=${TU_TENANT_ROOT_ID:-null})")
fi

if [[ -z "$TU_TENANT_ID" || "$TU_TENANT_ID" == "null" \
      || -z "$TU_ROLE_ID" || "$TU_ROLE_ID" == "null" \
      || -z "$TU_ANCHOR_A" ]]; then
    echo "  ${Y}[skip]${N} tenant-users write flow — could not resolve seed"
    echo "         (tenant_id=${TU_TENANT_ID:-null}, role_id=${TU_ROLE_ID:-null}, anchor=${TU_ANCHOR_A:-null})"
else
    SMOKE_TU_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    SMOKE_TU_EMAIL="tu-smoke-${SMOKE_TU_SUFFIX}@test.example.com"
    SMOKE_TU_NAME="TU Smoke ${SMOKE_TU_SUFFIX}"

    TU_CREATE_RESP="$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Accept: application/json" \
        -H "Content-Type: application/json" \
        -d "{\"tenant_id\":\"${TU_TENANT_ID}\",\"email\":\"${SMOKE_TU_EMAIL}\",\"full_name\":\"${SMOKE_TU_NAME}\",\"roles\":[{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${TU_ANCHOR_A}\"}]}" \
        "${API}/tenant-users" 2>/dev/null)"
    TU_CREATE_STATUS="$(printf '%s' "$TU_CREATE_RESP" | tail -n1)"
    TU_CREATE_BODY="$(printf '%s' "$TU_CREATE_RESP" | sed '$d')"

    if [[ "$TU_CREATE_STATUS" == "201" ]]; then
        printf "  ${G}[✓ %s]${N} POST /tenant-users ${D}— tu_flow__create (email=%s)${N}\n" \
            "$TU_CREATE_STATUS" "$SMOKE_TU_EMAIL"
        PASS=$((PASS + 1))
        SMOKE_TU_ID="$(printf '%s' "$TU_CREATE_BODY" | jq -r '.id' 2>/dev/null || echo "")"
    else
        printf "  ${R}[✗ %s, expected 201]${N} POST /tenant-users ${D}— tu_flow__create${N}\n" \
            "$TU_CREATE_STATUS"
        printf '%s\n' "$TU_CREATE_BODY" | head -5 | sed 's/^/        ↳ /'
        FAIL=$((FAIL + 1))
        FAILURES+=("tu_flow__create (got $TU_CREATE_STATUS, expected 201)")
        SMOKE_TU_ID=""
    fi

    if [[ -n "$SMOKE_TU_ID" && "$SMOKE_TU_ID" != "null" ]]; then
        TU_PATCH_BODY='{"full_name":"TU Smoke Patched"}'
        TU_PATCH_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d "$TU_PATCH_BODY" \
            "${API}/tenant-users/${SMOKE_TU_ID}" 2>/dev/null)"
        if [[ "$TU_PATCH_STATUS" == "200" ]]; then
            printf "  ${G}[✓ %s]${N} PATCH /tenant-users/{id} ${D}— tu_flow__patch${N}\n" \
                "$TU_PATCH_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 200]${N} PATCH /tenant-users/{id} ${D}— tu_flow__patch${N}\n" \
                "$TU_PATCH_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("tu_flow__patch (got $TU_PATCH_STATUS, expected 200)")
        fi

        # INVITED -> SUSPENDED is structurally rejected by
        # ck_tenant_users_auth0_sub_consistency. The app layer maps
        # this to 409 INVALID_STATE_TRANSITION; smoke verifies the
        # 409 path (the 200 happy path needs an ACTIVE user — out of
        # reach without DB access).
        req "tu_flow__suspend_invited_409"  409 "$PJWT" \
            POST "/tenant-users/${SMOKE_TU_ID}/suspend"

        # INVITED -> ACTIVE is the Auth0 invite-accept callback flow
        # (Stage 3); the explicit /activate refuses to take that path.
        req "tu_flow__activate_invited_409" 409 "$PJWT" \
            POST "/tenant-users/${SMOKE_TU_ID}/activate"
    fi
fi

# TENANT-as-caller self-edit deny (only if TJWT present).
if [[ "$TENANT_TESTS_ENABLED" -eq 1 ]]; then
    # Extract user_id from the TENANT JWT to target the same row. JWT
    # payload is base64-url-encoded; restore padding before decode.
    TJWT_USER_ID="$(python3 -c '
import base64, json, sys
token = sys.stdin.read().strip()
parts = token.split(".")
if len(parts) < 2:
    sys.exit(0)
payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
try:
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    print(payload.get("https://ithina.com/user_id", ""))
except Exception:
    pass
' <<< "$TJWT")"

    if [[ -n "$TJWT_USER_ID" ]]; then
        SELF_EDIT_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${TJWT}" \
            -H "Content-Type: application/json" \
            -d '{"full_name":"Trying To Edit Self"}' \
            "${API}/tenant-users/${TJWT_USER_ID}" 2>/dev/null)"
        if [[ "$SELF_EDIT_STATUS" == "403" ]]; then
            printf "  ${G}[✓ %s]${N} PATCH /tenant-users/{self} ${D}— tu_flow__self_edit_deny${N}\n" \
                "$SELF_EDIT_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 403]${N} PATCH /tenant-users/{self} ${D}— tu_flow__self_edit_deny${N}\n" \
                "$SELF_EDIT_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("tu_flow__self_edit_deny (got $SELF_EDIT_STATUS, expected 403)")
        fi
    else
        echo "  ${Y}[skip]${N} tu_flow__self_edit_deny — could not extract user_id from TJWT"
    fi
fi

# === Step 6.14 — tenant-users role-assignment writes (per-anchor + diff) =====
# Four additional checks on top of the Step 6.10.1 flow above:
#   1. POST multi-anchor: two {role_id, org_node_id} items at distinct
#      anchors create 2 ACTIVE rows.
#   2. PATCH diff-replace with overlap: 1 unchanged + 1 revoke + 1 grant.
#   3. PATCH no-op (desired == current): zero row changes.
#   4. POST with non-existent org_node_id -> 422 INVALID_ORG_NODE.

echo
echo "  ${D}--- Step 6.14 role-assignment write flow ---${N}"

if [[ -z "$TU_TENANT_ID" || "$TU_TENANT_ID" == "null" \
      || -z "$TU_ROLE_ID" || "$TU_ROLE_ID" == "null" \
      || -z "$TU_ANCHOR_A" \
      || -z "$TU_ANCHOR_B" ]]; then
    echo "  ${Y}[skip]${N} 6.14 flow — seed lookup incomplete"
else
    # 1. Multi-anchor POST: two distinct anchors. Re-resolve a second
    # TENANT-audience role so the two rows differ on role_id too
    # (cleaner Pattern-B exercise than reusing TU_ROLE_ID).
    TU_ROLE_ID_2="$(curl -s \
        -H "Authorization: Bearer ${PJWT}" \
        "${API}/roles?limit=50" 2>/dev/null \
        | jq -r '.tenant_roles.items[] | select(.code != "OWNER") | .id' \
        | head -n1)"
    [[ -z "$TU_ROLE_ID_2" || "$TU_ROLE_ID_2" == "null" ]] && TU_ROLE_ID_2="$TU_ROLE_ID"

    SMOKE_TU14_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    SMOKE_TU14_EMAIL="tu14-smoke-${SMOKE_TU14_SUFFIX}@test.example.com"

    TU14_MULTI_RESP="$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Accept: application/json" \
        -H "Content-Type: application/json" \
        -d "{\"tenant_id\":\"${TU_TENANT_ID}\",\"email\":\"${SMOKE_TU14_EMAIL}\",\"full_name\":\"TU14 Multi-Anchor\",\"roles\":[{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${TU_ANCHOR_A}\"},{\"role_id\":\"${TU_ROLE_ID_2}\",\"org_node_id\":\"${TU_ANCHOR_B}\"}]}" \
        "${API}/tenant-users" 2>/dev/null)"
    TU14_MULTI_STATUS="$(printf '%s' "$TU14_MULTI_RESP" | tail -n1)"
    TU14_MULTI_BODY="$(printf '%s' "$TU14_MULTI_RESP" | sed '$d')"

    if [[ "$TU14_MULTI_STATUS" == "201" ]]; then
        TU14_USER_ID="$(printf '%s' "$TU14_MULTI_BODY" | jq -r '.id')"
        TU14_ACTIVE_COUNT="$(printf '%s' "$TU14_MULTI_BODY" \
            | jq -r '[.roles[] | select(.status=="ACTIVE")] | length')"
        if [[ "$TU14_ACTIVE_COUNT" -ge 2 ]]; then
            printf "  ${G}[✓ %s]${N} POST /tenant-users multi-anchor ${D}— tu14__post_multi (active=%s)${N}\n" \
                "$TU14_MULTI_STATUS" "$TU14_ACTIVE_COUNT"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected >=2 ACTIVE]${N} POST multi-anchor ${D}— tu14__post_multi${N}\n" \
                "$TU14_ACTIVE_COUNT"
            FAIL=$((FAIL + 1))
            FAILURES+=("tu14__post_multi (active=$TU14_ACTIVE_COUNT, expected >=2)")
        fi
    else
        printf "  ${R}[✗ %s, expected 201]${N} POST /tenant-users multi-anchor ${D}— tu14__post_multi${N}\n" \
            "$TU14_MULTI_STATUS"
        printf '%s\n' "$TU14_MULTI_BODY" | head -3 | sed 's/^/        ↳ /'
        FAIL=$((FAIL + 1))
        FAILURES+=("tu14__post_multi (got $TU14_MULTI_STATUS, expected 201)")
        TU14_USER_ID=""
    fi

    # 2. Diff-replace PATCH: keep (role, A); revoke (role_2, B); add (role, B).
    if [[ -n "$TU14_USER_ID" && "$TU14_USER_ID" != "null" ]]; then
        TU14_DIFF_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d "{\"roles\":[{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${TU_ANCHOR_A}\"},{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${TU_ANCHOR_B}\"}]}" \
            "${API}/tenant-users/${TU14_USER_ID}" 2>/dev/null)"
        if [[ "$TU14_DIFF_STATUS" == "200" ]]; then
            printf "  ${G}[✓ %s]${N} PATCH /tenant-users diff-replace ${D}— tu14__patch_diff${N}\n" \
                "$TU14_DIFF_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 200]${N} PATCH /tenant-users diff-replace ${D}— tu14__patch_diff${N}\n" \
                "$TU14_DIFF_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("tu14__patch_diff (got $TU14_DIFF_STATUS, expected 200)")
        fi

        # 3. No-op PATCH: identical desired set.
        TU14_NOOP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d "{\"roles\":[{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${TU_ANCHOR_A}\"},{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${TU_ANCHOR_B}\"}]}" \
            "${API}/tenant-users/${TU14_USER_ID}" 2>/dev/null)"
        if [[ "$TU14_NOOP_STATUS" == "200" ]]; then
            printf "  ${G}[✓ %s]${N} PATCH /tenant-users no-op ${D}— tu14__patch_noop${N}\n" \
                "$TU14_NOOP_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 200]${N} PATCH /tenant-users no-op ${D}— tu14__patch_noop${N}\n" \
                "$TU14_NOOP_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("tu14__patch_noop (got $TU14_NOOP_STATUS, expected 200)")
        fi
    fi

    # 4. POST with non-existent org_node_id -> 422 INVALID_ORG_NODE.
    SMOKE_BAD_EMAIL="tu14-bad-${SMOKE_TU14_SUFFIX}@test.example.com"
    MISSING_ANCHOR="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4())')"
    TU14_BAD_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d "{\"tenant_id\":\"${TU_TENANT_ID}\",\"email\":\"${SMOKE_BAD_EMAIL}\",\"full_name\":\"TU14 Bad Anchor\",\"roles\":[{\"role_id\":\"${TU_ROLE_ID}\",\"org_node_id\":\"${MISSING_ANCHOR}\"}]}" \
        "${API}/tenant-users" 2>/dev/null)"
    if [[ "$TU14_BAD_STATUS" == "422" ]]; then
        printf "  ${G}[✓ %s]${N} POST /tenant-users invalid_org_node ${D}— tu14__post_invalid_org_node${N}\n" \
            "$TU14_BAD_STATUS"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s, expected 422]${N} POST /tenant-users invalid_org_node ${D}— tu14__post_invalid_org_node${N}\n" \
            "$TU14_BAD_STATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("tu14__post_invalid_org_node (got $TU14_BAD_STATUS, expected 422)")
    fi
fi

# === Step 6.15 — module-access write flow (PLATFORM) =========================
# Chain: enable upsert -> enable no-op -> disable flip -> disable no-op ->
# disable on a different missing module (404 MODULE_ACCESS_NOT_FOUND) ->
# TENANT audience-deny (only if TJWT).
#
# Uses the seed tenant resolved above (TU_TENANT_ID = Buc-ee's or first
# visible). Picks a module that is NOT already on the tenant. We probe
# the tenant's current module set first and choose two distinct unused
# codes. If no two unused codes exist (rare), we skip the block — smoke
# stays environment-tolerant.
#
# Re-run safety: each run leaks one DISABLED row per script invocation.
# Names not involved — the (tenant_id, module) uniqueness arbiter means
# every run mutates the same row pair. Cleanup is the operator's
# responsibility; mirrors the leaked-tenant pattern from the 6.11.2
# flow above.

echo
echo "  ${D}--- Step 6.15 module-access write flow ---${N}"

# Find a seeded tenant with 2+ unused module codes. Iterate visible
# tenants and probe /tenants/{id} for each until one qualifies AND
# verify it has a tenant-root org_node (required by the gate's
# anchor_dep). Skips smoke-created tenants from earlier runs (those
# lack org_node roots and would 404 at the anchor lookup).
#
# Verification: GET /tenants/{id}/org-tree returns 200 when an
# org_node root exists; 404 otherwise. Same RLS posture as the
# write endpoint's anchor dep, so it's a reliable proxy.
MA_TENANT_ID=""
SMOKE_MODULE=""
SMOKE_OTHER_MODULE=""

TENANT_IDS_JSON="$(curl -s \
    -H "Authorization: Bearer ${PJWT}" \
    "${API}/tenants?limit=50" 2>/dev/null \
    | jq -r '.items[].id' 2>/dev/null)"

while IFS= read -r tid; do
    [[ -z "$tid" ]] && continue

    # Anchor reachability: only seeded tenants have a tenant-root.
    ANCHOR_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${PJWT}" \
        "${API}/tenants/${tid}/org-tree" 2>/dev/null)"
    [[ "$ANCHOR_STATUS" == "200" ]] || continue

    EXISTING_MODULES="$(curl -s \
        -H "Authorization: Bearer ${PJWT}" \
        "${API}/tenants/${tid}" 2>/dev/null \
        | jq -r '.modules[].code' 2>/dev/null | sort -u)"

    CAND1=""
    CAND2=""
    for candidate in GOAL_CONSOLE PROMOTIONS_ASSISTANT PERISHABLES_ASSISTANT PRICING_OS; do
        if ! grep -qxF "$candidate" <<< "$EXISTING_MODULES"; then
            if [[ -z "$CAND1" ]]; then
                CAND1="$candidate"
            elif [[ -z "$CAND2" ]]; then
                CAND2="$candidate"
                break
            fi
        fi
    done

    if [[ -n "$CAND1" && -n "$CAND2" ]]; then
        MA_TENANT_ID="$tid"
        SMOKE_MODULE="$CAND1"
        SMOKE_OTHER_MODULE="$CAND2"
        break
    fi
done <<< "$TENANT_IDS_JSON"

if [[ -z "$MA_TENANT_ID" ]]; then
    echo "  ${Y}[skip]${N} module-access write flow — no tenant has 2+ unused modules"
else
    ENABLE_PATH="/module-access/${MA_TENANT_ID}/${SMOKE_MODULE}/enable"
    DISABLE_PATH="/module-access/${MA_TENANT_ID}/${SMOKE_MODULE}/disable"
    MISSING_DISABLE_PATH="/module-access/${MA_TENANT_ID}/${SMOKE_OTHER_MODULE}/disable"

    req "module_access_enable_upsert_create" 200 "$PJWT" \
        POST "$ENABLE_PATH"
    req "module_access_enable_noop_on_enabled" 200 "$PJWT" \
        POST "$ENABLE_PATH"
    req "module_access_disable_flip"          200 "$PJWT" \
        POST "$DISABLE_PATH"
    req "module_access_disable_noop"          200 "$PJWT" \
        POST "$DISABLE_PATH"
    req "module_access_disable_on_missing"    404 "$PJWT" \
        POST "$MISSING_DISABLE_PATH"

    # TENANT audience-deny: must target a tenant the TENANT JWT can
    # SEE via RLS — otherwise the gate's anchor_dep (resolved before
    # the gate body) raises TENANT_NOT_FOUND ahead of the Layer 1
    # audience check. Extract the TJWT's own tenant_id and use it.
    if [[ "$TENANT_TESTS_ENABLED" -eq 1 ]]; then
        TJWT_TENANT_ID="$(python3 -c '
import base64, json, sys
token = sys.stdin.read().strip()
parts = token.split(".")
if len(parts) < 2:
    sys.exit(0)
payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
try:
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    print(payload.get("https://ithina.com/tenant_id", ""))
except Exception:
    pass
' <<< "$TJWT")"
        if [[ -n "$TJWT_TENANT_ID" && "$TJWT_TENANT_ID" != "null" ]]; then
            TENANT_DENY_PATH="/module-access/${TJWT_TENANT_ID}/${SMOKE_MODULE}/enable"
            req "module_access_tenant_audience_deny" 403 "$TJWT" \
                POST "$TENANT_DENY_PATH"
        else
            echo "  ${Y}[skip]${N} module_access_tenant_audience_deny — no tenant_id in TJWT"
        fi
    fi
fi

# === Step 6.13 — org-tree write flow (PLATFORM) =============================
# Five smoke entries exercising: Add Node, Edit (rename), Edit (reparent),
# cascade-order reject, duplicate-code reject.
#
# Tenant-root reparent reject is verified by integration tests (E7); not
# exercised here because the read endpoint excludes the TENANT root from
# its response by design, so smoke can't reach the root's id via API.
#
# Re-run safety: each run leaks STORE org_nodes per invocation (unique
# UUID-suffixed codes). Manual cleanup is the operator's responsibility.

echo
echo "  ${D}--- Step 6.13 org-tree write flow (PLATFORM) ---${N}"

OT_TENANT_ID="$(curl -s \
    -H "Authorization: Bearer ${PJWT}" \
    -H "Accept: application/json" \
    "${API}/tenants?limit=50" 2>/dev/null \
    | jq -r '.items[] | select(.name == "Buc-ee'\''s") | .id' \
    | head -n1)"

if [[ -z "$OT_TENANT_ID" || "$OT_TENANT_ID" == "null" ]]; then
    OT_TENANT_ID="$(curl -s \
        -H "Authorization: Bearer ${PJWT}" \
        "${API}/tenants?limit=1" 2>/dev/null \
        | jq -r '.items[0].id')"
fi

# Two visible parent nodes for the add + reparent flow. tree[0] is the
# first top-level child of the TENANT root (typically a BU or HQ in seed
# tenants); .tree[1] is a sibling. STORE-under-anything-above-STORE is
# allowed by the cascade-order rule (parent ord < 5).
OT_TREE_JSON="$(curl -s \
    -H "Authorization: Bearer ${PJWT}" \
    "${API}/tenants/${OT_TENANT_ID}/org-tree" 2>/dev/null)"
OT_PARENT_A="$(printf '%s' "$OT_TREE_JSON" | jq -r '.tree[0].id // empty')"
OT_PARENT_B="$(printf '%s' "$OT_TREE_JSON" | jq -r '.tree[1].id // .tree[0].children[0].id // empty')"

if [[ -z "$OT_TENANT_ID" || -z "$OT_PARENT_A" ]]; then
    echo "  ${Y}[skip]${N} org-tree write flow — could not resolve seed (tid=${OT_TENANT_ID:-null}, parent=${OT_PARENT_A:-null})"
else
    OT_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    # Step 6.21.2: POST /org-tree rejects node_type='STORE'. The
    # smoke flow now adds a DEPARTMENT (ord=6) under the existing
    # tree[0] parent (typically HQ-level ord<6). Rename and reparent
    # still work on DEPARTMENT.
    OT_DEPT_CODE="ot-dept-${OT_SUFFIX:0:8}"

    OT_ADD_RESP="$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Accept: application/json" \
        -H "Content-Type: application/json" \
        -d "{\"parent_id\":\"${OT_PARENT_A}\",\"node_type\":\"DEPARTMENT\",\"code\":\"${OT_DEPT_CODE}\",\"name\":\"OT Smoke Dept\"}" \
        "${API}/tenants/${OT_TENANT_ID}/org-tree" 2>/dev/null)"
    OT_ADD_STATUS="$(printf '%s' "$OT_ADD_RESP" | tail -n1)"
    OT_ADD_BODY="$(printf '%s' "$OT_ADD_RESP" | sed '$d')"

    if [[ "$OT_ADD_STATUS" == "201" ]]; then
        printf "  ${G}[✓ %s]${N} POST /org-tree add DEPARTMENT ${D}— ot_flow__add${N}\n" \
            "$OT_ADD_STATUS"
        PASS=$((PASS + 1))
        OT_NEW_ID="$(printf '%s' "$OT_ADD_BODY" | jq -r '.id' 2>/dev/null || echo "")"
    else
        printf "  ${R}[✗ %s, expected 201]${N} POST /org-tree add ${D}— ot_flow__add${N}\n" \
            "$OT_ADD_STATUS"
        printf '%s\n' "$OT_ADD_BODY" | head -5 | sed 's/^/        ↳ /'
        FAIL=$((FAIL + 1))
        FAILURES+=("ot_flow__add (got $OT_ADD_STATUS, expected 201)")
        OT_NEW_ID=""
    fi

    if [[ -n "$OT_NEW_ID" && "$OT_NEW_ID" != "null" ]]; then
        OT_RENAME_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d '{"name":"OT Renamed"}' \
            "${API}/tenants/${OT_TENANT_ID}/org-tree/${OT_NEW_ID}" 2>/dev/null)"
        if [[ "$OT_RENAME_STATUS" == "200" ]]; then
            printf "  ${G}[✓ %s]${N} PATCH /org-tree rename ${D}— ot_flow__rename${N}\n" \
                "$OT_RENAME_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 200]${N} PATCH /org-tree rename ${D}— ot_flow__rename${N}\n" \
                "$OT_RENAME_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("ot_flow__rename (got $OT_RENAME_STATUS, expected 200)")
        fi

        # Reparent: move DEPARTMENT under PARENT_B.
        if [[ -n "$OT_PARENT_B" && "$OT_PARENT_B" != "$OT_PARENT_A" ]]; then
            OT_REPARENT_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
                -H "Authorization: Bearer ${PJWT}" \
                -H "Content-Type: application/json" \
                -d "{\"parent_id\":\"${OT_PARENT_B}\"}" \
                "${API}/tenants/${OT_TENANT_ID}/org-tree/${OT_NEW_ID}" 2>/dev/null)"
            if [[ "$OT_REPARENT_STATUS" == "200" ]]; then
                printf "  ${G}[✓ %s]${N} PATCH /org-tree reparent ${D}— ot_flow__reparent${N}\n" \
                    "$OT_REPARENT_STATUS"
                PASS=$((PASS + 1))
            else
                printf "  ${R}[✗ %s, expected 200]${N} PATCH /org-tree reparent ${D}— ot_flow__reparent${N}\n" \
                    "$OT_REPARENT_STATUS"
                FAIL=$((FAIL + 1))
                FAILURES+=("ot_flow__reparent (got $OT_REPARENT_STATUS, expected 200)")
            fi
        else
            echo "  ${Y}[skip]${N} ot_flow__reparent — no sibling parent visible"
        fi

        # Step 6.21.2: POST /org-tree with node_type=STORE returns 422
        # (V8 equivalent at the smoke layer). Replaces the pre-6.21.2
        # cascade-order reject which exercised STORE-parent (now
        # unreachable via /org-tree).
        OT_STORE_REJECT_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d "{\"parent_id\":\"${OT_PARENT_A}\",\"node_type\":\"STORE\",\"code\":\"ot-store-${OT_SUFFIX:0:8}\",\"name\":\"forbidden\"}" \
            "${API}/tenants/${OT_TENANT_ID}/org-tree" 2>/dev/null)"
        if [[ "$OT_STORE_REJECT_STATUS" == "422" ]]; then
            printf "  ${G}[✓ %s]${N} POST /org-tree STORE-rejected ${D}— ot_flow__store_rejected${N}\n" \
                "$OT_STORE_REJECT_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 422]${N} POST /org-tree STORE-rejected ${D}— ot_flow__store_rejected${N}\n" \
                "$OT_STORE_REJECT_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("ot_flow__store_rejected (got $OT_STORE_REJECT_STATUS, expected 422)")
        fi

        # Duplicate-code reject: same code already used by the
        # DEPARTMENT just added.
        OT_DUP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d "{\"parent_id\":\"${OT_PARENT_A}\",\"node_type\":\"DEPARTMENT\",\"code\":\"${OT_DEPT_CODE}\",\"name\":\"dup\"}" \
            "${API}/tenants/${OT_TENANT_ID}/org-tree" 2>/dev/null)"
        if [[ "$OT_DUP_STATUS" == "409" ]]; then
            printf "  ${G}[✓ %s]${N} POST /org-tree duplicate-code ${D}— ot_flow__duplicate${N}\n" \
                "$OT_DUP_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 409]${N} POST /org-tree duplicate-code ${D}— ot_flow__duplicate${N}\n" \
                "$OT_DUP_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("ot_flow__duplicate (got $OT_DUP_STATUS, expected 409)")
        fi
    fi
fi

# === Step 6.17.3 — stores write flow (PLATFORM) =============================
# Chain: POST create (UUID-suffixed name + store_code) -> PATCH name change ->
# TENANT no-grants audience-deny (only if TJWT present, expects 403
# PERMISSION_DENIED — TENANT JWT has no seeded ADMIN.STORES.CONFIGURE.TENANT).
#
# Re-uses OT_TENANT_ID (Buc-ee's by name; first tenant otherwise) resolved
# in the Step 6.13 block above. UUID-suffixed name + store_code so re-runs
# don't 409 DUPLICATE_STORE_CODE. Manual cleanup is the operator's
# responsibility per the same posture as the tenants / org-tree flows.

echo
echo "  ${D}--- Step 6.17.3 stores write flow (PLATFORM) ---${N}"

if [[ -z "$OT_TENANT_ID" || "$OT_TENANT_ID" == "null" ]]; then
    echo "  ${Y}[skip]${N} stores write flow — no resolvable tenant_id"
else
    ST_SUFFIX="$(uuidgen 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
    ST_SUFFIX="${ST_SUFFIX:0:8}"
    SMOKE_STORE_NAME="smoke-store-${ST_SUFFIX}"
    SMOKE_STORE_CODE="ST-${ST_SUFFIX}"

    # Step 6.21.2: POST /stores requires parent_org_node_id. Use the
    # tenant root id surfaced by Step 6.21.1 in /org-tree.
    ST_CREATE_RESP="$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Accept: application/json" \
        -H "Content-Type: application/json" \
        -d "$(cat <<EOF
{
  "tenant_id": "${OT_TENANT_ID}",
  "parent_org_node_id": "${TU_TENANT_ROOT_ID}",
  "name": "${SMOKE_STORE_NAME}",
  "country": "United States",
  "timezone": "America/New_York",
  "currency": "USD",
  "store_code": "${SMOKE_STORE_CODE}",
  "tax_treatment": "EXCLUSIVE"
}
EOF
)" \
        "${API}/stores" 2>/dev/null)"
    ST_CREATE_STATUS="$(printf '%s' "$ST_CREATE_RESP" | tail -n1)"
    ST_CREATE_BODY="$(printf '%s' "$ST_CREATE_RESP" | sed '$d')"

    if [[ "$ST_CREATE_STATUS" == "201" ]]; then
        printf "  ${G}[✓ %s]${N} POST /stores ${D}— store_flow__create (code=%s)${N}\n" \
            "$ST_CREATE_STATUS" "$SMOKE_STORE_CODE"
        PASS=$((PASS + 1))
        SMOKE_STORE_ID="$(printf '%s' "$ST_CREATE_BODY" | jq -r '.id' 2>/dev/null || echo "")"
        # Step 6.21.2: response carries server-allocated org_node_id.
        SMOKE_STORE_ORG_NODE_ID="$(printf '%s' "$ST_CREATE_BODY" | jq -r '.org_node_id' 2>/dev/null || echo "")"
        if [[ -n "$SMOKE_STORE_ORG_NODE_ID" \
              && "$SMOKE_STORE_ORG_NODE_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
            printf "  ${G}[✓ 201]${N} POST /stores response carries org_node_id ${D}— store_flow__org_node_id_present${N}\n"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ org_node_id=%s]${N} POST /stores response missing org_node_id ${D}— store_flow__org_node_id_present${N}\n" "${SMOKE_STORE_ORG_NODE_ID:-null}"
            FAIL=$((FAIL + 1))
            FAILURES+=("store_flow__org_node_id_present (got ${SMOKE_STORE_ORG_NODE_ID:-null})")
        fi
    else
        printf "  ${R}[✗ %s, expected 201]${N} POST /stores ${D}— store_flow__create${N}\n" \
            "$ST_CREATE_STATUS"
        printf '%s\n' "$ST_CREATE_BODY" | head -5 | sed 's/^/        ↳ /'
        FAIL=$((FAIL + 1))
        FAILURES+=("store_flow__create (got $ST_CREATE_STATUS, expected 201)")
        SMOKE_STORE_ID=""
    fi

    if [[ -n "$SMOKE_STORE_ID" && "$SMOKE_STORE_ID" != "null" ]]; then
        ST_PATCH_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
            -H "Authorization: Bearer ${PJWT}" \
            -H "Content-Type: application/json" \
            -d '{"name":"smoke-store-renamed"}' \
            "${API}/stores/${SMOKE_STORE_ID}" 2>/dev/null)"
        if [[ "$ST_PATCH_STATUS" == "200" ]]; then
            printf "  ${G}[✓ %s]${N} PATCH /stores/{id} ${D}— store_flow__patch${N}\n" \
                "$ST_PATCH_STATUS"
            PASS=$((PASS + 1))
        else
            printf "  ${R}[✗ %s, expected 200]${N} PATCH /stores/{id} ${D}— store_flow__patch${N}\n" \
                "$ST_PATCH_STATUS"
            FAIL=$((FAIL + 1))
            FAILURES+=("store_flow__patch (got $ST_PATCH_STATUS, expected 200)")
        fi
    fi

    # TENANT OWNER happy path (multi-audience). TJWT carries Marcus
    # (Buc-ee's OWNER); OWNER holds ADMIN.STORES.CONFIGURE.TENANT per
    # the Step 6.17.1 seed update, so the gate admits. Targets the
    # TJWT's own tenant_id; verifies the multi-audience contract end
    # to end. The TENANT-no-grants deny path is covered by integration
    # test RC7 (not exercised in smoke because the seed has no
    # ungranted TENANT user).
    if [[ "$TENANT_TESTS_ENABLED" -eq 1 ]]; then
        TJWT_TENANT_ID_STORES="$(python3 -c '
import base64, json, sys
token = sys.stdin.read().strip()
parts = token.split(".")
if len(parts) < 2:
    sys.exit(0)
payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
try:
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    print(payload.get("https://ithina.com/tenant_id", ""))
except Exception:
    pass
' <<< "$TJWT")"
        if [[ -n "$TJWT_TENANT_ID_STORES" && "$TJWT_TENANT_ID_STORES" != "null" ]]; then
            STORES_OWNER_SUFFIX="$(uuidgen 2>/dev/null \
                || python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
            STORES_OWNER_SUFFIX="${STORES_OWNER_SUFFIX:0:8}"
            # Step 6.21.2: TJWT POST /stores needs parent_org_node_id;
            # fetch the TJWT tenant's root via /org-tree's
            # tenant_root_id (Step 6.21.1 surface).
            TJWT_TENANT_ROOT_ID="$(curl -s \
                -H "Authorization: Bearer ${TJWT}" \
                "${API}/tenants/${TJWT_TENANT_ID_STORES}/org-tree" 2>/dev/null \
                | jq -r '.tenant_root_id // empty')"
            STORES_OWNER_STATUS="$(curl -s -o /dev/null -w "%{http_code}" -X POST \
                -H "Authorization: Bearer ${TJWT}" \
                -H "Content-Type: application/json" \
                -d "{\"tenant_id\":\"${TJWT_TENANT_ID_STORES}\",\"parent_org_node_id\":\"${TJWT_TENANT_ROOT_ID}\",\"name\":\"owner-${STORES_OWNER_SUFFIX}\",\"country\":\"United States\",\"timezone\":\"America/New_York\",\"currency\":\"USD\",\"store_code\":\"ON-${STORES_OWNER_SUFFIX}\",\"tax_treatment\":\"EXCLUSIVE\"}" \
                "${API}/stores" 2>/dev/null)"
            if [[ "$STORES_OWNER_STATUS" == "201" ]]; then
                printf "  ${G}[✓ %s]${N} POST /stores ${D}— store_flow__tenant_owner_create${N}\n" \
                    "$STORES_OWNER_STATUS"
                PASS=$((PASS + 1))
            else
                printf "  ${R}[✗ %s, expected 201]${N} POST /stores ${D}— store_flow__tenant_owner_create${N}\n" \
                    "$STORES_OWNER_STATUS"
                FAIL=$((FAIL + 1))
                FAILURES+=("store_flow__tenant_owner_create (got $STORES_OWNER_STATUS, expected 201)")
            fi
        else
            echo "  ${Y}[skip]${N} store_flow__tenant_owner_create — no tenant_id in TJWT"
        fi
    fi
fi

# === Step 6.17.4 - stores set-status state-transition flow (PLATFORM) =======
# Two assertions exercising the 9-cell liberal matrix end-to-end via
# the wire:
#   1. ACTIVE -> OPENING rejected (LD1; *->OPENING not in matrix) -> 409
#   2. ACTIVE -> INACTIVE happy (Class 3) -> 200 + status=INACTIVE
#
# Order is "rejected first, happy second" deliberately: the new store
# from the 6.17.3 block lands in ACTIVE (DDL default; see Step 6.17.3
# FN-AB-51). Running the rejected check first keeps the row in ACTIVE
# so the second check has a valid source state. After both checks the
# row sits in INACTIVE; smoke does not reset.

echo
echo "  ${D}--- Step 6.17.4 stores set-status flow (PLATFORM) ---${N}"

if [[ -z "${SMOKE_STORE_ID:-}" || "$SMOKE_STORE_ID" == "null" ]]; then
    echo "  ${Y}[skip]${N} set-status flow — no SMOKE_STORE_ID from 6.17.3 block"
else
    # 1. Rejected: ACTIVE -> OPENING.
    SS_REJECT_STATUS="$(curl -s -o /tmp/smoke-set-status-reject.json \
        -w "%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d '{"target_status":"OPENING"}' \
        "${API}/stores/${SMOKE_STORE_ID}/set-status" 2>/dev/null)"
    SS_REJECT_CODE="$(jq -r '.code // empty' \
        < /tmp/smoke-set-status-reject.json 2>/dev/null || echo "")"
    if [[ "$SS_REJECT_STATUS" == "409" \
          && "$SS_REJECT_CODE" == "INVALID_STATE_TRANSITION" ]]; then
        printf "  ${G}[✓ %s]${N} POST /stores/{id}/set-status (ACTIVE->OPENING) ${D}— set_status__rejected${N}\n" \
            "$SS_REJECT_STATUS"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s/%s, expected 409/INVALID_STATE_TRANSITION]${N} POST /stores/{id}/set-status (ACTIVE->OPENING) ${D}— set_status__rejected${N}\n" \
            "$SS_REJECT_STATUS" "$SS_REJECT_CODE"
        FAIL=$((FAIL + 1))
        FAILURES+=("set_status__rejected (got $SS_REJECT_STATUS/$SS_REJECT_CODE, expected 409/INVALID_STATE_TRANSITION)")
    fi
    rm -f /tmp/smoke-set-status-reject.json

    # 2. Happy: ACTIVE -> INACTIVE.
    SS_HAPPY_STATUS="$(curl -s -o /tmp/smoke-set-status-happy.json \
        -w "%{http_code}" -X POST \
        -H "Authorization: Bearer ${PJWT}" \
        -H "Content-Type: application/json" \
        -d '{"target_status":"INACTIVE"}' \
        "${API}/stores/${SMOKE_STORE_ID}/set-status" 2>/dev/null)"
    SS_HAPPY_NEWSTATUS="$(jq -r '.status // empty' \
        < /tmp/smoke-set-status-happy.json 2>/dev/null || echo "")"
    if [[ "$SS_HAPPY_STATUS" == "200" \
          && "$SS_HAPPY_NEWSTATUS" == "INACTIVE" ]]; then
        printf "  ${G}[✓ %s]${N} POST /stores/{id}/set-status (ACTIVE->INACTIVE) ${D}— set_status__happy${N}\n" \
            "$SS_HAPPY_STATUS"
        PASS=$((PASS + 1))
    else
        printf "  ${R}[✗ %s/%s, expected 200/INACTIVE]${N} POST /stores/{id}/set-status (ACTIVE->INACTIVE) ${D}— set_status__happy${N}\n" \
            "$SS_HAPPY_STATUS" "$SS_HAPPY_NEWSTATUS"
        FAIL=$((FAIL + 1))
        FAILURES+=("set_status__happy (got $SS_HAPPY_STATUS/status=$SS_HAPPY_NEWSTATUS, expected 200/INACTIVE)")
    fi
    rm -f /tmp/smoke-set-status-happy.json
fi

# === Version check ==========================================================
# /health response must include a version field. Confirms SERVICE_VERSION
# env var reached the running container.
echo
HEALTH_BODY=$(curl -sf "${API}/health" 2>/dev/null || echo "{}")
REPORTED_VERSION=$(echo "$HEALTH_BODY" | jq -r '.version' 2>/dev/null || echo "?")
if [[ -n "$REPORTED_VERSION" && "$REPORTED_VERSION" != "?" && "$REPORTED_VERSION" != "null" ]]; then
    echo "  ${G}[✓]${N} health reports version: ${REPORTED_VERSION}"
    PASS=$((PASS + 1))
else
    echo "  ${R}[✗]${N} health response missing or empty 'version' field"
    FAIL=$((FAIL + 1))
    FAILURES+=("health version field missing")
fi

# === Summary ================================================================
TOTAL=$((PASS + FAIL))
echo
echo "  Total:  ${TOTAL}"
echo "  Passed: ${G}${PASS}${N}"
if [[ "$FAIL" -gt 0 ]]; then
    echo "  Failed: ${R}${FAIL}${N}"
    echo
    echo "${R}Failures:${N}"
    for f in "${FAILURES[@]}"; do
        echo "  - ${f}"
    done
    exit 1
fi

echo "  Failed: ${FAIL}"
echo
echo "${G}All ${TOTAL} smoke checks passed.${N}"
exit 0
