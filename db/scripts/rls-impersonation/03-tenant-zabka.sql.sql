-- ============================================================
-- PROFILE: TENANT — Anna Kowalski @ Żabka Group
-- Expected: sees ONLY Żabka data
-- ============================================================

RESET app.user_type;
RESET app.tenant_id;

SET app.user_type = 'TENANT';
SET app.tenant_id = '019dfe1b-9a0e-7630-b698-45cd241be667';  -- Żabka Group tenant_id

-- Confirm the GUCs
SELECT
    current_setting('app.user_type', true) AS user_type,
    current_setting('app.tenant_id', true) AS tenant_id,
    'Anna Kowalski @ Żabka (TENANT)' AS profile;

-- Tenant visibility — expect 1 (only Żabka)
SELECT count(*) AS tenant_count FROM core.tenants;
SELECT id, name, tier, status FROM core.tenants;

-- Tenant users — expect 4 (Anna, Krzysztof, Magda, Piotr)
SELECT count(*) AS tenant_users_count FROM core.tenant_users;
SELECT email, full_name FROM core.tenant_users ORDER BY email;

-- Stores — Żabka subset only
SELECT count(*) AS stores_count FROM core.stores;

-- Tenant module access — Żabka modules only
SELECT module, status FROM core.tenant_module_access ORDER BY module;

-- user_role_assignments — only Żabka-scoped rows visible
SELECT
    count(*) FILTER (WHERE tenant_id IS NULL) AS platform_audience_visible,
    count(*) FILTER (WHERE tenant_id IS NOT NULL) AS tenant_scoped_visible
FROM core.user_role_assignments;