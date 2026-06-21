-- ============================================================
-- Assorted Tests and query 
-- ============================================================


SELECT current_user, session_user, current_database();

SELECT rolname, rolsuper, rolbypassrls, rolcanlogin
FROM pg_roles
WHERE rolname = current_user;

SELECT count(*) FROM core.tenants;

RESET app.user_type;
SET app.user_type = 'PLATFORM';

SELECT count(*) FROM core.tenants;
SELECT id, name, status, tier FROM core.tenants ORDER BY name;

SELECT * FROM core.tenant_users LIMIT 20;
SELECT * FROM core.stores;
SELECT * FROM core.tenant_module_access;
SELECT * FROM core.user_role_assignments;
SELECT count(*) FROM core.org_nodes;

SELECT id, name FROM core.tenants ORDER BY name;


RESET app.user_type;
RESET app.tenant_id;
SET app.user_type = 'TENANT';
SET app.tenant_id = '019dfe1b-9a0a-759b-b144-e4ad78ce81a1';  -- replace with actual Buc-ee's UUID

SELECT id, name FROM core.tenants;       -- expect 1 row (Buc-ee's only)
SELECT id, email FROM core.tenant_users; -- expect 6 rows (Buc-ee's tenant_users only)
SELECT id, name FROM core.stores;               -- expect 3 rows (Buc-ee's stores only)';  -- replace with actual Buc-ee's UUID

SELECT
    schemaname,
    tablename,
    policyname,
    qual
FROM pg_policies
WHERE schemaname = 'core'
ORDER BY tablename, policyname;

SET app.user_type = 'PLATFORM';

-- All platform users
SELECT id, email, full_name, status
FROM core.platform_users
ORDER BY email;

-- All tenant users with their tenant_id (for impersonation pairs)
SELECT
    tu.id           AS user_id,
    tu.tenant_id    AS tenant_id,
    t.name          AS tenant_name,
    tu.email,
    tu.full_name,
    tu.status
FROM core.tenant_users tu
JOIN core.tenants t ON t.id = tu.tenant_id
ORDER BY t.name, tu.email;


