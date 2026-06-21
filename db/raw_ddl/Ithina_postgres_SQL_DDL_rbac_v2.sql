-- ============================================================================
-- Ithina: RBAC (roles, permissions, role_permissions, user_role_assignments)
-- Postgres SQL DDL
-- Version: v2
--
-- This file defines the RBAC entities for the Ithina platform.
--
-- Model summary:
--   * Permission shape: Module + Resource + Action + Scope. Stored as a
--     composite row in the `permissions` table (the canonical catalogue
--     of valid permission tuples).
--   * Role: a named bundle of permissions. Two role universes:
--       - PLATFORM roles: Ithina staff. Always Global-scope-eligible.
--       - TENANT roles: customer-side. Cap is Tenant-scope.
--     All roles are platform-defined in v1; tenant users cannot create
--     roles.
--   * role_permissions: many-to-many junction.
--   * user_role_assignments: many-to-many between users and roles, with
--     an org_node anchor (for TENANT-audience). A user can hold multiple
--     roles, each at its own org_node. The org_node is the cascade root
--     for that assignment's permissions; downward cascade via ltree path.
--
-- Changes from v1:
--   * Pattern 2 user split: physical separation of platform_users and
--     tenant_users tables. user_role_assignments must reference exactly
--     one of the two:
--       - PLATFORM-audience: platform_user_id NOT NULL, tenant_user_id NULL,
--         tenant_id NULL, org_node_id NULL.
--       - TENANT-audience:   tenant_user_id NOT NULL, platform_user_id NULL,
--         tenant_id NOT NULL, org_node_id NOT NULL.
--     Replaces the single user_id FK that v1 declared.
--   * Two FK constraints (one to each user table), each fires only when
--     its column is non-NULL.
--   * XOR CHECK enforces exactly one user reference per row.
--   * granted_by, revoked_by adopt pattern (b): UUID + actor_user_type
--     enum, no FK.
--
-- Key model decisions (retained):
--   * Assignment-vs-permission scope rule (AI-RBAC-03): app layer
--     validates assignment org_node level >= widest permission scope
--     in role.
--   * tenant_id denormalised on user_role_assignments for RLS efficiency.
--   * Roles, permissions, role_permissions: platform-global, no RLS,
--     API-layer authorisation.
--   * user_role_assignments: RLS on tenant_id; PLATFORM rows have
--     tenant_id=NULL and visible only to staff queries (BYPASSRLS).
--
-- Out of scope for v1 (retained):
--   * Tenant-custom roles (FN-AB-06).
--   * Approval workflow tables.
--   * Manager-approval requirements per permission.
--   * Time-bound assignments.
--   * Role hierarchies.
--   * Per-user permission overrides.
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * org_nodes (id, tenant_id)
--   * platform_users (id)
--   * tenant_users (id)
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--       - enum actor_user_type_enum
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by RBAC
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
-- permissions
--
-- Canonical catalogue of every valid (module, resource, action, scope)
-- tuple. New permissions are added by Ithina platform admins via
-- migration or admin API. UI matrix renders directly from this table.
--
-- Platform-global table. No tenant_id. No RLS. Access controlled by
-- API-layer authorisation: only roles with ADMIN.PERMISSIONS.VIEW can
-- read the catalogue (in practice all logged-in users for UI rendering),
-- and only ROLES.CONFIGURE can mutate.
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
-- roles
--
-- Named bundle of permissions. Platform-defined and platform-global.
-- Same role catalogue applies to every tenant. Audience flag controls
-- whether the role is for Ithina staff (PLATFORM) or customer users
-- (TENANT).
--
-- Platform-global table. No tenant_id. No RLS. Access controlled by
-- API-layer authorisation: all logged-in users can read; only
-- ROLES.CONFIGURE can mutate.
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


-- ----------------------------------------------------------------------------
-- role_permissions
--
-- Many-to-many junction between roles and permissions. Defines which
-- permissions a role grants. Updated only by Ithina platform admins.
--
-- Platform-global table. No tenant_id. No RLS.
--
-- Audience-scope invariant (enforced at app layer in v1):
--   * If role.audience = TENANT, no linked permission may have
--     scope = GLOBAL. GLOBAL is reserved for platform roles.
--   * If role.audience = PLATFORM, any scope is permitted.
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


-- ----------------------------------------------------------------------------
-- user_role_assignments
--
-- Assigns a role to a user at a specific org_node anchor. Permissions
-- granted by the role cascade DOWNWARD from the org_node anchor through
-- the org tree (ltree path comparison).
--
-- A user may hold multiple roles. A user's effective permission set is
-- the UNION of permissions across all active assignments.
--
-- Shape rules (some enforced at DB level, some at app layer):
--
--   PLATFORM audience (Ithina staff):
--     - tenant_id      MUST be NULL
--     - org_node_id    MUST be NULL
--     - The staff user is global-scope; no anchor needed.
--
--   TENANT audience (customer staff):
--     - tenant_id      MUST be NOT NULL
--     - org_node_id    MUST be NOT NULL
--     - org_node_id must belong to the same tenant_id (enforced by
--       composite FK to org_nodes (tenant_id, id)).
--     - org_node level must be at or above the widest permission scope
--       in the role. Enforced at app layer in v1.
--
-- tenant_id is denormalised onto this row (rather than joined through
-- org_nodes) so RLS can filter directly.
-- ----------------------------------------------------------------------------

CREATE TABLE user_role_assignments (

    -- ---------- Surrogate primary key ----------
    id                      UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- Subject (Pattern 2: exactly one of these is non-NULL) ----------
    platform_user_id        UUID                            NULL,
        -- platform_users.id when role.audience = PLATFORM. NULL when
        -- role.audience = TENANT.
    tenant_user_id          UUID                            NULL,
        -- tenant_users.id when role.audience = TENANT. NULL when
        -- role.audience = PLATFORM.

    -- ---------- Granted role ----------
    role_id                 UUID                            NOT NULL,

    -- ---------- Anchor ----------
    tenant_id               UUID                            NULL,
        -- NOT NULL for TENANT-audience roles, NULL for PLATFORM-audience.
    org_node_id             UUID                            NULL,
        -- NOT NULL for TENANT-audience roles, NULL for PLATFORM-audience.
        -- Permissions cascade DOWNWARD from this node via ltree path.

    -- ---------- Lifecycle ----------
    status                  user_role_assignment_status_enum NOT NULL DEFAULT 'ACTIVE',

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    granted_at              TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    granted_by_user_id      UUID                            NULL,
    granted_by_user_type    actor_user_type_enum            NULL,
    revoked_at              TIMESTAMPTZ                     NULL,
    revoked_by_user_id      UUID                            NULL,
    revoked_by_user_type    actor_user_type_enum            NULL,
    updated_at              TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),

    -- ---------- Constraints ----------

    CONSTRAINT pk_user_role_assignments
        PRIMARY KEY (id),

    CONSTRAINT fk_user_role_assignments_role
        FOREIGN KEY (role_id)
        REFERENCES roles (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_user_role_assignments_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- Composite FK ensures the org_node belongs to the same tenant.
    -- Relies on org_nodes UNIQUE(tenant_id, id).
    CONSTRAINT fk_user_role_assignments_org_node_same_tenant
        FOREIGN KEY (tenant_id, org_node_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- FK to platform_users (fires only when platform_user_id is non-NULL).
    CONSTRAINT fk_user_role_assignments_platform_user
        FOREIGN KEY (platform_user_id)
        REFERENCES platform_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- FK to tenant_users (fires only when tenant_user_id is non-NULL).
    CONSTRAINT fk_user_role_assignments_tenant_user
        FOREIGN KEY (tenant_user_id)
        REFERENCES tenant_users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- XOR: exactly one of platform_user_id / tenant_user_id must be set.
    CONSTRAINT ck_user_role_assignments_user_xor
        CHECK (
            (platform_user_id IS NOT NULL AND tenant_user_id IS NULL)
            OR
            (platform_user_id IS NULL AND tenant_user_id IS NOT NULL)
        ),

    -- Anchor shape: tenant_id and org_node_id must both be NULL
    -- (PLATFORM-audience) or both NOT NULL (TENANT-audience).
    CONSTRAINT ck_user_role_assignments_anchor_shape
        CHECK (
            (tenant_id IS NULL AND org_node_id IS NULL)
            OR
            (tenant_id IS NOT NULL AND org_node_id IS NOT NULL)
        ),

    -- Combined: PLATFORM user has no anchor; TENANT user must have anchor.
    -- Cross-check with the user XOR + anchor shape constraints above.
    CONSTRAINT ck_user_role_assignments_user_anchor_consistency
        CHECK (
            (platform_user_id IS NOT NULL AND tenant_id IS NULL AND org_node_id IS NULL)
            OR
            (tenant_user_id   IS NOT NULL AND tenant_id IS NOT NULL AND org_node_id IS NOT NULL)
        ),

    -- Pattern (b) actor pair shape.
    CONSTRAINT ck_user_role_assignments_granted_by_actor_pair
        CHECK (
            (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
            OR
            (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_user_role_assignments_revoked_by_actor_pair
        CHECK (
            (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
            OR
            (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_user_role_assignments_revoked_consistency
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

-- ---------- Indexes ----------

-- Partial UNIQUE: a platform user cannot hold the same role twice
-- (active). PLATFORM assignments have no org_node anchor, so the
-- uniqueness is just (platform_user_id, role_id).
CREATE UNIQUE INDEX uq_user_role_assignments_platform_active_unique
    ON user_role_assignments (platform_user_id, role_id)
    WHERE status = 'ACTIVE' AND platform_user_id IS NOT NULL;

-- Partial UNIQUE: a tenant user cannot hold the same role at the same
-- org_node twice (active).
CREATE UNIQUE INDEX uq_user_role_assignments_tenant_active_unique
    ON user_role_assignments (tenant_user_id, role_id, org_node_id)
    WHERE status = 'ACTIVE' AND tenant_user_id IS NOT NULL;

-- Tenant-scoped queries (RLS path).
CREATE INDEX ix_user_role_assignments_tenant
    ON user_role_assignments (tenant_id);

-- "What roles does this platform user have?"
CREATE INDEX ix_user_role_assignments_platform_user
    ON user_role_assignments (platform_user_id)
    WHERE platform_user_id IS NOT NULL;

-- "What roles does this tenant user have?"
CREATE INDEX ix_user_role_assignments_tenant_user
    ON user_role_assignments (tenant_user_id)
    WHERE tenant_user_id IS NOT NULL;

-- "Who has this role assigned at this org_node?"
CREATE INDEX ix_user_role_assignments_role_org_node
    ON user_role_assignments (role_id, org_node_id);

-- Active platform assignments for permission resolution at login.
CREATE INDEX ix_user_role_assignments_platform_user_active
    ON user_role_assignments (platform_user_id)
    WHERE status = 'ACTIVE' AND platform_user_id IS NOT NULL;

-- Active tenant assignments for permission resolution at login.
CREATE INDEX ix_user_role_assignments_tenant_user_active
    ON user_role_assignments (tenant_user_id)
    WHERE status = 'ACTIVE' AND tenant_user_id IS NOT NULL;

CREATE TRIGGER tg_user_role_assignments_set_updated_at
    BEFORE UPDATE ON user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

-- ---------- Row-Level Security ----------
--
-- TENANT-audience assignment rows have tenant_id set; tenant users see
-- only their own tenant's rows. PLATFORM-audience rows have
-- tenant_id=NULL and are visible only to staff connections (which use
-- BYPASSRLS or a staff-role view that exposes them).
--
-- The policy expression filters by tenant_id match. NULL tenant_id rows
-- are filtered out for tenant sessions (NULL = NULL is UNKNOWN in SQL).
-- Staff sessions skip RLS via BYPASSRLS.

ALTER TABLE user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_role_assignments FORCE ROW LEVEL SECURITY;

CREATE POLICY user_role_assignments_tenant_isolation
    ON user_role_assignments
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);


-- ============================================================================
-- Application-layer invariants captured here for reference. These are
-- not enforced by DDL in v1; document them in CLAUDE.md and enforce in
-- service code.
--
-- AI-RBAC-01: When inserting role_permissions, if role.audience='TENANT'
--             reject any permission with scope='GLOBAL'.
--
-- AI-RBAC-02: When inserting user_role_assignments:
--             - role.audience='PLATFORM' requires tenant_id IS NULL and
--               org_node_id IS NULL, AND the user must be Ithina staff.
--             - role.audience='TENANT' requires tenant_id IS NOT NULL
--               and org_node_id IS NOT NULL, AND the user must belong to
--               the same tenant.
--
-- AI-RBAC-03: Assignment org_node level must be at or above the widest
--             permission scope in the role. (Pricing Manager with
--             Region-scope permissions: assignable at Region or Tenant,
--             not at Store.)
--
-- AI-RBAC-04: Permission resolution at request time must filter by:
--             - assignment.status = ACTIVE
--             - role.status = ACTIVE
--             - user.is_active = TRUE
--             - org_node.status = ACTIVE
--             AuthContext caches the resolved set; invalidate on changes
--             to any of the above.
--
-- AI-RBAC-05: Permission cascade for TENANT-audience: a permission
--             granted at assignment org_node X applies to X and all
--             descendants of X in the org tree (ltree path comparison).
--             For PLATFORM-audience: permission applies globally.
--
-- AI-RBAC-06: Cross-tenant injection prevention. When inserting a
--             user_role_assignments row with tenant_user_id NOT NULL,
--             app layer must verify that
--                 tenant_users.tenant_id WHERE id = tenant_user_id
--             equals the assignment's tenant_id column. Without this
--             check, a row could pair a tenant_user from tenant A with
--             tenant_id = tenant B; RLS would hide it from tenant A's
--             session, making it invisible to that tenant's auditors.
--             Future enforcement: DB trigger that performs this lookup
--             on insert/update. Tracked as part of FN-AB-09 tech debt.
-- ============================================================================
