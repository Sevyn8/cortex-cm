-- ============================================================
-- PROFILE: TENANT — Marcus Tanner @ Buc-ee's
-- Expected: sees ONLY Buc-ee's data
-- ============================================================

RESET app.user_type;
RESET app.tenant_id;

SET app.user_type = 'TENANT';
SET app.tenant_id = '019dfe1b-9a0a-759b-b144-e4ad78ce81a1';  -- Buc-ee's tenant_id

-- Confirm the GUCs
SELECT
    current_setting('app.user_type', true) AS user_type,
    current_setting('app.tenant_id', true) AS tenant_id,
    'Marcus Tanner @ Buc-ee''s (TENANT)' AS profile;

-- Tenant visibility — expect 1 (only Buc-ee's)
SELECT count(*) AS tenant_count FROM core.tenants;
SELECT id, name, tier, status FROM core.tenants;

-- Tenant users — expect 6 (only Buc-ee's users)
SELECT count(*) AS tenant_users_count FROM core.tenant_users;
SELECT email, full_name, status FROM core.tenant_users ORDER BY email;

-- Stores — expect 3 (only Buc-ee's stores)
SELECT count(*) AS stores_count FROM core.stores;

-- Org nodes — expect Buc-ee's subset only
SELECT count(*) AS org_nodes_count FROM core.org_nodes;

-- tenant_module_access — expect 6 rows (Buc-ee's modules only)
SELECT module, status FROM core.tenant_module_access ORDER BY module;

-- user_role_assignments — expect ONLY tenant-scoped rows for Buc-ee's
-- PLATFORM-audience rows (tenant_id NULL) should be INVISIBLE per the
-- IS-NULL-gated OR-clause on this table
SELECT
    count(*) FILTER (WHERE tenant_id IS NULL) AS platform_audience_visible,
    count(*) FILTER (WHERE tenant_id IS NOT NULL) AS tenant_scoped_visible,
    count(*) AS total
FROM core.user_role_assignments;