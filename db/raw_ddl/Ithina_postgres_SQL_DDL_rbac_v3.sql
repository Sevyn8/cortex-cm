-- ============================================================================
-- Ithina: RBAC (permissions, roles, role_permissions,
--               platform_user_role_assignments, tenant_user_role_assignments)
-- Postgres SQL DDL
-- Version: v3
--
-- This file is the as-shipped baseline for the RBAC schema after Step 6.8.1's
-- table split. It supersedes v2 conceptually but does not replace it; per the
-- frozen-DDL convention, v2 stays as historical record.
--
-- Changes from v2 (delivered by Alembic migration in Step 6.8.1):
--   * Drop `user_role_assignments` (the dual-FK XOR table).
--   * Add `platform_user_role_assignments` — platform-global, no RLS,
--     references platform_users only.
--   * Add `tenant_user_role_assignments` — multi-tenant, RLS+FORCE with the
--     unconditional PLATFORM OR-branch (D-29 form, identical to the other 5
--     multi-tenant tables); references tenant_users + org_nodes via composite
--     FKs to (tenant_id, id) on each, making AI-RBAC-06 cross-tenant
--     injection structurally impossible at the schema layer rather than
--     enforced at the application layer.
--   * Both new tables carry an audience-check row-level trigger (CHECK
--     constraints can't query other tables; trigger enforces role.audience
--     consistency on INSERT and UPDATE OF role_id).
--
-- Pre-existing tables (permissions, roles, role_permissions) are reproduced
-- verbatim from v2 so this file is self-contained.
--
-- The shared enum types (role_audience_enum, module_enum, resource_enum,
-- action_enum, permission_scope_enum, role_status_enum,
-- user_role_assignment_status_enum) are reproduced unchanged from v2.
--
-- Out of scope for v0 (retained):
--   * Tenant-custom roles (FN-AB-06).
--   * Approval workflow tables.
--   * Manager-approval requirements per permission.
--   * Time-bound assignments.
--   * Role hierarchies.
--   * Per-user permission overrides.
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * tenant_users (id)         — and (tenant_id, id) UNIQUE for the
--                                 composite FK (added by Step 6.8.1's
--                                 migration; not reflected in
--                                 tenant_users_v1.sql per frozen-DDL).
--   * org_nodes (id, tenant_id) — UNIQUE (tenant_id, id) already declared
--                                 by org_nodes_v2.sql.
--   * platform_users (id)
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--       - enum actor_user_type_enum
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by RBAC (unchanged from v2)
-- ----------------------------------------------------------------------------

CREATE TYPE role_audience_enum AS ENUM (
    'PLATFORM',
        -- Ithina staff role. Eligible for GLOBAL-scope permissions.
    'TENANT'
        -- Customer-side role. Cap is TENANT-scope.
);

CREATE TYPE module_enum AS ENUM (
    'ROOS',
    'GOAL_CONSOLE',
    'PRICING_OS',
    'PERISHABLES_ASSISTANT',
    'PROMOTIONS_ASSISTANT',
    'ADMIN'
);

CREATE TYPE resource_enum AS ENUM (
    -- Pricing OS
    'PRICING_RULES',
    'MARKDOWNS',
    -- Perishables Assistant
    'EXPIRING_ITEMS',
    'WASTE_LOG',
    'DONATION_ROUTING',
    -- Promotions Assistant
    'CAMPAIGNS',
    -- Admin
    'USERS',
    'ROLES',
    'AUDIT_LOG',
    'TENANTS',
    'STORES',
    'ORG_NODES'
        -- Extend by ALTER TYPE as new resources surface.
);

CREATE TYPE action_enum AS ENUM (
    'VIEW',
    'CONFIGURE',
    'EXECUTE',
    'APPROVE',
    'OVERRIDE',
    'AUDIT'
);

CREATE TYPE permission_scope_enum AS ENUM (
    'GLOBAL',
        -- Platform-wide. Only assignable via PLATFORM-audience roles.
    'TENANT',
    'REGION',
    'STORE'
);

CREATE TYPE role_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ARCHIVED'
);

CREATE TYPE user_role_assignment_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE'
);


-- ----------------------------------------------------------------------------
-- permissions  (verbatim from v2)
-- ----------------------------------------------------------------------------

CREATE TABLE permissions (

    -- ---------- Surrogate primary key ----------
    id              UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Composite identity ----------
    module          module_enum             NOT NULL,
    resource        resource_enum           NOT NULL,
    action          action_enum             NOT NULL,
    scope           permission_scope_enum   NOT NULL,

    -- ---------- Display ----------
    code            TEXT                    NOT NULL,
        -- Derived stable string for logs and audit references, e.g.
        -- 'PRICING_OS.MARKDOWNS.APPROVE.REGION'. App layer or trigger
        -- maintains; in v1 set on insert by app layer.
    description     TEXT                    NULL,

    -- ---------- Audit ----------
    created_at      TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ             NOT NULL DEFAULT NOW(),

    -- ---------- Constraints ----------

    CONSTRAINT pk_permissions
        PRIMARY KEY (id),

    CONSTRAINT uq_permissions_tuple
        UNIQUE (module, resource, action, scope),

    CONSTRAINT uq_permissions_code
        UNIQUE (code),

    CONSTRAINT ck_permissions_code_format
        CHECK (code ~ '^[A-Z_]+\.[A-Z_]+\.[A-Z_]+\.[A-Z_]+$')
);

CREATE TRIGGER tg_permissions_set_updated_at
    BEFORE UPDATE ON permissions
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- roles  (verbatim from v2)
-- ----------------------------------------------------------------------------

CREATE TABLE roles (

    -- ---------- Surrogate primary key ----------
    id              UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Identity ----------
    name            TEXT                    NOT NULL,
        -- Display name (e.g., 'Pricing Manager', 'Super Admin').
    code            TEXT                    NOT NULL,
        -- Stable wire-code (e.g., 'PRICING_MANAGER', 'SUPER_ADMIN').
        -- Used in API and config files; not localised.
    description     TEXT                    NULL,

    -- ---------- Classification ----------
    audience        role_audience_enum      NOT NULL,
        -- PLATFORM: Ithina staff. May hold GLOBAL-scope permissions.
        -- TENANT:   customer staff. Cap is TENANT-scope.

    -- ---------- Lifecycle ----------
    status          role_status_enum        NOT NULL DEFAULT 'ACTIVE',
    is_system       BOOLEAN                 NOT NULL DEFAULT FALSE,
        -- TRUE for roles shipped by Ithina that should not be deleted
        -- (e.g., SUPER_ADMIN). FALSE for roles created post-launch.
        -- Mutating system roles requires elevated privilege.

    -- ---------- Audit (pattern b) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,
    updated_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_by_user_id      UUID                    NULL,
    updated_by_user_type    actor_user_type_enum    NULL,
    archived_at             TIMESTAMPTZ             NULL,
    archived_by_user_id     UUID                    NULL,
    archived_by_user_type   actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_roles
        PRIMARY KEY (id),

    CONSTRAINT uq_roles_code
        UNIQUE (code),

    CONSTRAINT ck_roles_name_length
        CHECK (LENGTH(name) BETWEEN 1 AND 100),

    CONSTRAINT ck_roles_code_format
        CHECK (code ~ '^[A-Z][A-Z0-9_]{1,49}$'),

    CONSTRAINT ck_roles_archived_consistency
        CHECK (
            (status = 'ARCHIVED'
                AND archived_at IS NOT NULL
                AND archived_by_user_id IS NOT NULL
                AND archived_by_user_type IS NOT NULL)
            OR
            (status != 'ARCHIVED'
                AND archived_at IS NULL
                AND archived_by_user_id IS NULL
                AND archived_by_user_type IS NULL)
        ),

    CONSTRAINT ck_roles_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_roles_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        )
);

CREATE INDEX ix_roles_audience
    ON roles (audience);

CREATE INDEX ix_roles_status
    ON roles (status);

CREATE TRIGGER tg_roles_set_updated_at
    BEFORE UPDATE ON roles
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

-- Bootstrap-protection trigger function (Step 6.20.3). Pins the
-- SUPER_ADMIN row: status, code, and audience are immutable; the row
-- itself cannot be deleted. Name and description remain editable so
-- branding/display copy can change without bypassing the bootstrap
-- protection. Dispatch on TG_OP so one trigger covers UPDATE and
-- DELETE; the function filters by OLD.code = 'SUPER_ADMIN' so non-
-- SUPER_ADMIN rows pass through untouched.
CREATE OR REPLACE FUNCTION protect_super_admin_role()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.code = 'SUPER_ADMIN' THEN
            RAISE EXCEPTION
                'bootstrap-protection: SUPER_ADMIN role cannot be deleted';
        END IF;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.code = 'SUPER_ADMIN' AND (
            NEW.code IS DISTINCT FROM OLD.code OR
            NEW.status IS DISTINCT FROM OLD.status OR
            NEW.audience IS DISTINCT FROM OLD.audience
        ) THEN
            RAISE EXCEPTION
                'bootstrap-protection: SUPER_ADMIN role status, code, and audience are immutable';
        END IF;
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_roles_protect_super_admin
    BEFORE UPDATE OR DELETE ON roles
    FOR EACH ROW
    EXECUTE FUNCTION protect_super_admin_role();


-- ----------------------------------------------------------------------------
-- role_permissions  (verbatim from v2)
-- ----------------------------------------------------------------------------

CREATE TABLE role_permissions (

    role_id                 UUID                    NOT NULL,
    permission_id           UUID                    NOT NULL,

    -- ---------- Audit (pattern b) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_role_permissions
        PRIMARY KEY (role_id, permission_id),

    CONSTRAINT fk_role_permissions_role
        FOREIGN KEY (role_id)
        REFERENCES roles (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_role_permissions_permission
        FOREIGN KEY (permission_id)
        REFERENCES permissions (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT ck_role_permissions_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        )
);

CREATE INDEX ix_role_permissions_permission
    ON role_permissions (permission_id);

-- Audience-scope coherence trigger function (Step 6.20.3). DDL backstop
-- for AI-RBAC-01: a TENANT-audience role cannot hold a GLOBAL-scope
-- permission. App-layer pre-check at Step 6.18.3 LD17 returns a clean
-- 422 envelope for API callers; this trigger catches direct-SQL, seed-
-- loader, and any future endpoint that omits the LD17 check.
CREATE OR REPLACE FUNCTION enforce_role_audience_scope_coherence()
RETURNS TRIGGER AS $$
DECLARE
    v_role_audience role_audience_enum;
    v_perm_scope permission_scope_enum;
BEGIN
    SELECT audience INTO v_role_audience FROM roles WHERE id = NEW.role_id;
    SELECT scope INTO v_perm_scope FROM permissions WHERE id = NEW.permission_id;
    IF v_role_audience = 'TENANT' AND v_perm_scope = 'GLOBAL' THEN
        RAISE EXCEPTION
            'audience-scope-check: TENANT-audience role cannot hold GLOBAL-scope permission (role_id=%, permission_id=%)',
            NEW.role_id, NEW.permission_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_role_permissions_audience_scope_coherence
    BEFORE INSERT OR UPDATE OF role_id, permission_id ON role_permissions
    FOR EACH ROW
    EXECUTE FUNCTION enforce_role_audience_scope_coherence();

-- Bootstrap-protection trigger function (Step 6.20.3). Pins the
-- (SUPER_ADMIN, ADMIN.ROLES.OVERRIDE.GLOBAL) grant: cannot be deleted.
-- Backstops the Step 6.18.3 LD6/LD8 two-layer LAST_OVERRIDE_HOLDER
-- invariant. UPDATE-OF-role_id/permission_id rename vectors are NOT
-- covered (deliberate LD2); renames are operator-deliberate actions,
-- not bypass paths.
CREATE OR REPLACE FUNCTION protect_super_admin_override_global_grant()
RETURNS TRIGGER AS $$
DECLARE
    v_super_admin_id UUID;
    v_override_global_id UUID;
BEGIN
    SELECT id INTO v_super_admin_id FROM roles WHERE code = 'SUPER_ADMIN';
    SELECT id INTO v_override_global_id FROM permissions
        WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL';
    IF OLD.role_id = v_super_admin_id AND OLD.permission_id = v_override_global_id THEN
        RAISE EXCEPTION
            'bootstrap-protection: cannot delete SUPER_ADMIN x ADMIN.ROLES.OVERRIDE.GLOBAL grant';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_role_permissions_protect_super_admin_override
    BEFORE DELETE ON role_permissions
    FOR EACH ROW
    EXECUTE FUNCTION protect_super_admin_override_global_grant();


-- ----------------------------------------------------------------------------
-- platform_user_role_assignments
--
-- Assigns a PLATFORM-audience role to an Ithina staff member. PLATFORM
-- assignments have no tenant or org_node anchor; the role is global by
-- definition and applies platform-wide.
--
-- Platform-global table. No tenant_id. No RLS. Mirrors platform_users'
-- posture (D-12 Pattern 2 split): visibility is controlled by who can
-- read the table at the application layer, not by row-level filtering.
-- Step 6.8 RBAC enforcement reads this table when resolving a PLATFORM
-- user's permission set.
--
-- Audience is enforced by the row-level trigger
-- enforce_platform_role_audience(): role_id must reference a row in
-- `roles` with audience = 'PLATFORM'. CHECK constraints cannot query
-- other tables, so a trigger is required.
-- ----------------------------------------------------------------------------

CREATE TABLE platform_user_role_assignments (

    -- ---------- Surrogate primary key ----------
    id                       UUID                                NOT NULL DEFAULT uuidv7(),

    -- ---------- Subject + role ----------
    platform_user_id         UUID                                NOT NULL,
    role_id                  UUID                                NOT NULL,

    -- ---------- Lifecycle ----------
    status                   user_role_assignment_status_enum    NOT NULL,

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    granted_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
    granted_by_user_id       UUID                                NULL,
    granted_by_user_type     actor_user_type_enum                NULL,
    revoked_at               TIMESTAMPTZ                         NULL,
    revoked_by_user_id       UUID                                NULL,
    revoked_by_user_type     actor_user_type_enum                NULL,

    updated_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- Constraints ----------

    CONSTRAINT pk_platform_user_role_assignments
        PRIMARY KEY (id),

    CONSTRAINT fk_platform_user_role_assignments_platform_user
        FOREIGN KEY (platform_user_id) REFERENCES platform_users (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_platform_user_role_assignments_role
        FOREIGN KEY (role_id) REFERENCES roles (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT ck_platform_user_role_assignments_granted_by_actor_pair
        CHECK (
            (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
            OR
            (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_platform_user_role_assignments_revoked_by_actor_pair
        CHECK (
            (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
            OR
            (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
        ),

    -- Mirrors v2's revoked_consistency CHECK pattern exactly.
    -- user_role_assignment_status_enum has 2 values: 'ACTIVE', 'INACTIVE'.
    CONSTRAINT ck_platform_user_role_assignments_revoked_consistency
        CHECK (
            (status = 'INACTIVE'
                AND revoked_at IS NOT NULL
                AND revoked_by_user_id IS NOT NULL
                AND revoked_by_user_type IS NOT NULL)
            OR
            (status = 'ACTIVE'
                AND revoked_at IS NULL
                AND revoked_by_user_id IS NULL
                AND revoked_by_user_type IS NULL)
        )
);

-- Partial UNIQUE: a platform user cannot hold the same role twice (active).
-- PLATFORM assignments have no org_node anchor, so uniqueness is just
-- (platform_user_id, role_id).
CREATE UNIQUE INDEX uq_platform_user_role_assignments_active
    ON platform_user_role_assignments (platform_user_id, role_id)
    WHERE status = 'ACTIVE';

-- "What roles does this platform user have?"
CREATE INDEX ix_platform_user_role_assignments_platform_user
    ON platform_user_role_assignments (platform_user_id);

-- "Who has this role assigned?" (reverse-lookup)
CREATE INDEX ix_platform_user_role_assignments_role
    ON platform_user_role_assignments (role_id);

-- Active platform assignments for permission resolution at login.
CREATE INDEX ix_platform_user_role_assignments_platform_user_active
    ON platform_user_role_assignments (platform_user_id)
    WHERE status = 'ACTIVE';

CREATE TRIGGER tg_platform_user_role_assignments_set_updated_at
    BEFORE UPDATE ON platform_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

-- Audience-check trigger function. CHECK constraints cannot query other
-- tables, so role.audience consistency requires a row-level trigger.
-- Structural-impossibility guard: prevents inserting a TENANT-audience
-- role into the platform table.
CREATE OR REPLACE FUNCTION enforce_platform_role_audience()
RETURNS TRIGGER AS $$
DECLARE
    v_audience role_audience_enum;
BEGIN
    SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
    IF v_audience IS DISTINCT FROM 'PLATFORM' THEN
        RAISE EXCEPTION
            'audience-check: platform_user_role_assignments requires PLATFORM-audience role; role % has audience %',
            NEW.role_id, v_audience;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_platform_user_role_assignments_audience_check
    BEFORE INSERT OR UPDATE OF role_id ON platform_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION enforce_platform_role_audience();

-- No RLS. Platform-global table.


-- ----------------------------------------------------------------------------
-- tenant_user_role_assignments
--
-- Assigns a TENANT-audience role to a tenant user at a specific org_node
-- anchor. Permissions granted by the role cascade DOWNWARD from the
-- org_node anchor through the org tree (ltree path comparison; resolved
-- at request time, not stored).
--
-- A user may hold multiple roles. A user's effective permission set is
-- the UNION of permissions across all active assignments.
--
-- Cross-tenant injection prevention is structural via composite FKs:
--   * (tenant_id, tenant_user_id) -> tenant_users (tenant_id, id)
--   * (tenant_id, org_node_id)    -> org_nodes (tenant_id, id)
-- A row whose denormalised tenant_id mismatches the user's or the
-- org_node's tenant_id is impossible by FK declaration. Closes
-- AI-RBAC-06 at the schema layer (replaces the previous app-layer
-- pre-check forward note in v2).
--
-- RLS shape: unconditional PLATFORM OR-branch (D-29), identical to the
-- 5 other multi-tenant tables. PLATFORM sessions see all rows across
-- all tenants; TENANT sessions see only their own.
--
-- Audience is enforced by the row-level trigger
-- enforce_tenant_role_audience(): role_id must reference a row in
-- `roles` with audience = 'TENANT'.
-- ----------------------------------------------------------------------------

CREATE TABLE tenant_user_role_assignments (

    -- ---------- Surrogate primary key ----------
    id                       UUID                                NOT NULL DEFAULT uuidv7(),

    -- ---------- Subject + anchor + role ----------
    tenant_user_id           UUID                                NOT NULL,
    tenant_id                UUID                                NOT NULL,
    org_node_id              UUID                                NOT NULL,
    role_id                  UUID                                NOT NULL,

    -- ---------- Lifecycle ----------
    status                   user_role_assignment_status_enum    NOT NULL,

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    granted_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
    granted_by_user_id       UUID                                NULL,
    granted_by_user_type     actor_user_type_enum                NULL,
    revoked_at               TIMESTAMPTZ                         NULL,
    revoked_by_user_id       UUID                                NULL,
    revoked_by_user_type     actor_user_type_enum                NULL,

    updated_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    -- ---------- Constraints ----------

    CONSTRAINT pk_tenant_user_role_assignments
        PRIMARY KEY (id),

    -- Composite FK to tenant_users — enforces tenant_id matches the user's
    -- tenant. Structural-impossibility guarantee for cross-tenant injection
    -- on the user side. Requires UNIQUE (tenant_id, id) on tenant_users.
    CONSTRAINT fk_tenant_user_role_assignments_tenant_user_same_tenant
        FOREIGN KEY (tenant_id, tenant_user_id)
        REFERENCES tenant_users (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    -- Composite FK to org_nodes — enforces tenant_id matches the org_node's
    -- tenant. Structural-impossibility guarantee on the org_node side.
    CONSTRAINT fk_tenant_user_role_assignments_org_node_same_tenant
        FOREIGN KEY (tenant_id, org_node_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_user_role_assignments_role
        FOREIGN KEY (role_id) REFERENCES roles (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_user_role_assignments_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT ck_tenant_user_role_assignments_granted_by_actor_pair
        CHECK (
            (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
            OR
            (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_user_role_assignments_revoked_by_actor_pair
        CHECK (
            (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
            OR
            (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_user_role_assignments_revoked_consistency
        CHECK (
            (status = 'INACTIVE'
                AND revoked_at IS NOT NULL
                AND revoked_by_user_id IS NOT NULL
                AND revoked_by_user_type IS NOT NULL)
            OR
            (status = 'ACTIVE'
                AND revoked_at IS NULL
                AND revoked_by_user_id IS NULL
                AND revoked_by_user_type IS NULL)
        )
);

-- Partial UNIQUE: a tenant user cannot hold the same role at the same
-- org_node twice (active). Mirrors v2's
-- uq_user_role_assignments_tenant_active_unique.
CREATE UNIQUE INDEX uq_tenant_user_role_assignments_active
    ON tenant_user_role_assignments (tenant_user_id, role_id, org_node_id)
    WHERE status = 'ACTIVE';

-- Tenant-scoped queries (RLS path).
CREATE INDEX ix_tenant_user_role_assignments_tenant
    ON tenant_user_role_assignments (tenant_id);

-- "What roles does this tenant user have?"
CREATE INDEX ix_tenant_user_role_assignments_tenant_user
    ON tenant_user_role_assignments (tenant_user_id);

-- "Who has this role assigned at this org_node?"
CREATE INDEX ix_tenant_user_role_assignments_role_org_node
    ON tenant_user_role_assignments (role_id, org_node_id);

-- Active tenant assignments for permission resolution at login.
CREATE INDEX ix_tenant_user_role_assignments_tenant_user_active
    ON tenant_user_role_assignments (tenant_user_id)
    WHERE status = 'ACTIVE';

CREATE TRIGGER tg_tenant_user_role_assignments_set_updated_at
    BEFORE UPDATE ON tenant_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

CREATE OR REPLACE FUNCTION enforce_tenant_role_audience()
RETURNS TRIGGER AS $$
DECLARE
    v_audience role_audience_enum;
BEGIN
    SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
    IF v_audience IS DISTINCT FROM 'TENANT' THEN
        RAISE EXCEPTION
            'audience-check: tenant_user_role_assignments requires TENANT-audience role; role % has audience %',
            NEW.role_id, v_audience;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_tenant_user_role_assignments_audience_check
    BEFORE INSERT OR UPDATE OF role_id ON tenant_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION enforce_tenant_role_audience();

-- ---------- Row-Level Security ----------
-- Unconditional PLATFORM OR-branch (D-29). Identical shape to the other 5
-- multi-tenant tables. PLATFORM session sees every row regardless of
-- app.tenant_id; TENANT session sees only rows whose tenant_id matches
-- app.tenant_id. NULLIF wrapper per D-27.

ALTER TABLE tenant_user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_user_role_assignments FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_user_role_assignments_tenant_isolation
    ON tenant_user_role_assignments
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    );


-- ============================================================================
-- Application-layer invariants (post-split)
--
-- Surviving from v2:
--
-- AI-RBAC-01: When inserting role_permissions, if role.audience='TENANT'
--             reject any permission with scope='GLOBAL'. App-layer pre-check.
--
-- AI-RBAC-03: Assignment org_node level must be at or above the widest
--             permission scope in the role. App-layer pre-check.
--
-- AI-RBAC-04: Permission resolution at request time must filter by:
--             - assignment.status = ACTIVE
--             - role.status = ACTIVE
--             - user.status = 'ACTIVE'
--             - org_node.status = ACTIVE  (TENANT only)
--
-- AI-RBAC-05: Permission cascade for TENANT-audience: a permission granted
--             at assignment org_node X applies to X and all descendants of X
--             via ltree path. PLATFORM-audience: applies platform-wide.
--
-- Retired by Step 6.8.1 (no longer needed):
--
-- AI-RBAC-02: PLATFORM/TENANT shape distinction. Was enforced by app-layer
--             plus DDL CHECKs on the unified table. Now enforced by table
--             choice — the wrong shape literally cannot exist.
--
-- AI-RBAC-06: Cross-tenant injection prevention. Was app-layer pre-check
--             with a forward note for a future DB trigger. Now enforced by
--             composite FKs on tenant_user_role_assignments. The forward
--             note is retired.
-- ============================================================================
