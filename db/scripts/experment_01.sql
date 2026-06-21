SET app.user_type = 'PLATFORM';
SELECT * FROM core.platform_users;
SELECT
    r.code                              AS role_code,
    r.name                              AS role_name,
    r.audience,
    ura.id                              AS assignment_id,
    ura.status                          AS assignment_status,
    CASE
        WHEN pu.id IS NOT NULL THEN 'PLATFORM'
        ELSE 'TENANT'
    END                                 AS user_kind,
    COALESCE(pu.id,   tu.id)            AS user_id,
    COALESCE(pu.email, tu.email)        AS email,
    COALESCE(pu.full_name, tu.full_name) AS full_name,
    COALESCE(pu.status::text, tu.status::text) AS user_status,
    ura.tenant_id,
    t.name                              AS tenant_name,
    ura.granted_at
FROM user_role_assignments ura
JOIN roles          r  ON r.id  = ura.role_id
LEFT JOIN platform_users pu ON pu.id = ura.platform_user_id
LEFT JOIN tenant_users   tu ON tu.id = ura.tenant_user_id
LEFT JOIN tenants        t  ON t.id  = ura.tenant_id
WHERE r.code = 'SUPER_ADMIN'
   OR LOWER(r.name) = 'super admin'
ORDER BY user_kind, email;

SELECT
    p.module,
    p.resource,
    array_agg(DISTINCT p.action::text ORDER BY p.action::text) AS actions,
    array_agg(DISTINCT p.scope::text  ORDER BY p.scope::text)  AS scopes,
    COUNT(*) AS permission_count
FROM user_role_assignments ura
JOIN role_permissions rp ON rp.role_id = ura.role_id
JOIN permissions      p  ON p.id        = rp.permission_id
JOIN roles            r  ON r.id        = ura.role_id
WHERE (ura.platform_user_id = '019dfe1b-99f6-738c-a9d6-5f723316f69e'
       OR ura.tenant_user_id = '019dfe1b-99f6-738c-a9d6-5f723316f69e')
  AND ura.status = 'ACTIVE'
  AND r.status   = 'ACTIVE'
GROUP BY p.module, p.resource
ORDER BY p.module, p.resource;