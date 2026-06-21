
-- ============================================================
-- PROFILE: PLATFORM — Anjali Mehta (Super Admin)
-- Expected: sees ALL data across all tenants
-- ============================================================

RESET app.user_type;
RESET app.tenant_id;

SET app.user_type = 'PLATFORM';
-- app.tenant_id deliberately not set — PLATFORM branch makes it irrelevant

-- Confirm the GUCs
SELECT
    current_setting('app.user_type', true) AS user_type,
    current_setting('app.tenant_id', true) AS tenant_id,
    'Anjali Mehta (PLATFORM)' AS profile;

-- Fleet visibility — expect 7 tenants
SELECT count(*) AS tenant_count FROM core.tenants;

-- All tenant users across all tenants — expect 17
SELECT count(*) AS tenant_users_count FROM core.tenant_users;

-- All stores — expect 25
SELECT count(*) AS stores_count FROM core.stores;

-- All org_nodes — expect 49
SELECT count(*) AS org_nodes_count FROM core.org_nodes;

-- tenant_module_access rows
SELECT count(*) AS module_access_rows FROM core.tenant_module_access;

-- user_role_assignments — both tenant-scoped AND PLATFORM-audience (NULL tenant_id)
-- expect 22 total (3 PLATFORM-audience + 19 TENANT-scoped per Step 3.5 docstring)
SELECT
    count(*) FILTER (WHERE tenant_id IS NULL) AS platform_audience,
    count(*) FILTER (WHERE tenant_id IS NOT NULL) AS tenant_scoped,
    count(*) AS total
FROM core.user_role_assignments;

-- Per-tenant breakdown — confirms full visibility
SELECT
    t.name AS tenant,
    t.tier,
    t.status
FROM core.tenants t
ORDER BY t.name;