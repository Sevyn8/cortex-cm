-- ============================================================================
-- Ithina: org_nodes
-- Postgres SQL DDL
-- Version: v2
--
-- This file defines org_nodes, the tree-position table for the
-- organisation hierarchy. Every entity that participates in the tenant's
-- org tree (tenants, stores, and intermediate groupings like BU / HQ /
-- Region / Department) has a corresponding row in org_nodes.
--
-- Changes from v1:
--   * Audit columns adopt pattern (b): each actor slot is a UUID +
--     actor_user_type_enum pair, no FK. created_by, updated_by, and
--     archived_by columns renamed and split:
--       - created_by  -> created_by_user_id  + created_by_user_type
--       - updated_by  -> updated_by_user_id  + updated_by_user_type
--       - archived_by -> archived_by_user_id + archived_by_user_type
--     The actor may be a platform_user (Phase 1) or a tenant_user
--     (post-launch tree edits).
--   * ck_org_nodes_archived_consistency updated to enforce both columns
--     of the archived_by pair are set together with archived_at.
--   * New CHECKs ck_org_nodes_*_actor_pair enforce actor pair shape.
--   * No FK on actor columns. App layer validates UUID exists in the
--     table indicated by user_type (FN-AB-09 tech debt).
--
-- Purpose (retained from v1):
--   * Drive the org-tree UI.
--   * Anchor permissions: a permission attached to a node cascades to
--     descendants via the tree.
--   * Uniform model for varied tenant hierarchies (tenants may skip
--     levels: SmartStore Demo has Tenant > HQ > Store, no Region).
--
-- ltree extension (retained):
--   * Materialised path column for descendant / ancestor queries via
--     <@ and @> operators. Cloud SQL allows ltree.
--   * Path maintained by app layer on insert and on tree mutations.
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * Extension ltree.
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
--       - enum actor_user_type_enum (defined in tenant_users v1)
--
-- Sync invariants enforced outside this DDL (app layer for v1):
--   * Every tenants row has exactly one org_nodes row with node_type=TENANT
--     and parent_id=NULL. Created together at tenant onboarding.
--   * Every stores row has exactly one org_nodes row with node_type=STORE.
--     Linked via stores.org_node_id (added in stores v4).
--   * The parent_id of any node references a node within the same
--     tenant_id (enforced by composite FK).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Required extension
-- ----------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS ltree;


-- ----------------------------------------------------------------------------
-- Enum types used by org_nodes
-- ----------------------------------------------------------------------------

CREATE TYPE org_node_type_enum AS ENUM (
    'TENANT',
    'BUSINESS_UNIT',
    'HQ',
    'COUNTRY',
    'REGION',
    'STORE',
    'DEPARTMENT'
        -- DEPARTMENT is in-scope for v1 as a tree node. Linkage to
        -- product_catalogues (department-to-category) is parked; revisit
        -- when SKUs or sales need department-level grouping.
);

CREATE TYPE org_node_status_enum AS ENUM (
    'ACTIVE',
    'INACTIVE',
    'ARCHIVED'
);


-- ----------------------------------------------------------------------------
-- org_nodes
--
-- A tree-position record. Holds where a node sits in the tenant's
-- organisation hierarchy and its display metadata. Business attributes
-- (currency, tax_treatment, address, contact_email, etc.) live on the
-- entity tables (tenants, stores), not here.
-- ----------------------------------------------------------------------------

CREATE TABLE org_nodes (

    -- ---------- Surrogate primary key ----------
    id                  UUID                    NOT NULL DEFAULT uuidv7(),

    -- ---------- Ownership ----------
    tenant_id           UUID                    NOT NULL,
        -- Every node belongs to exactly one tenant. For TENANT-type nodes
        -- this column matches the entity itself (tenant_id = tenants.id
        -- of the corresponding tenant row).

    -- ---------- Tree position ----------
    parent_id           UUID                    NULL,
        -- NULL only for TENANT-type nodes (tree root for the tenant).
        -- All other nodes must reference a parent within the same tenant.
        -- v1 does not enforce parent-child type pairs; app layer applies
        -- shape rules.

    path                LTREE                   NOT NULL,
        -- Materialised path from the tenant root to this node, using
        -- node code labels separated by dots, lowercased.
        -- Example: 'buc.hq.tx.s101.deli'.
        -- Maintained by app layer on insert and on tree mutations.
        -- Used for descendant / ancestor queries via ltree operators.

    -- ---------- Identity ----------
    node_type           org_node_type_enum      NOT NULL,
    name                TEXT                    NOT NULL,
        -- Display name (e.g., 'Texas Region', 'Buc-ee's #101 New Braunfels').
    code                TEXT                    NOT NULL,
        -- Short code used in UI badges and as the path label component
        -- (e.g., 'TX', 'TX-101', 'BU-HQ'). Unique within tenant.
        -- Stored case-insensitively; uniqueness enforced via expression
        -- index below.

    -- ---------- Lifecycle ----------
    status              org_node_status_enum    NOT NULL DEFAULT 'ACTIVE',
        -- ACTIVE: node is live in the tree.
        -- INACTIVE: hidden from default UI but retained.
        -- ARCHIVED: soft-deleted, kept for audit and historical queries.

    -- ---------- Audit (pattern b: id + type, no FK) ----------
    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by_user_id      UUID                    NULL,
    created_by_user_type    actor_user_type_enum    NULL,
        -- platform_users.id (PLATFORM) or tenant_users.id (TENANT) of
        -- the actor who created this node. App layer validates UUID
        -- exists in the table indicated by user_type. NULL only when
        -- created by a system process (e.g., auto-creation on tenant
        -- insert).
    updated_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_by_user_id      UUID                    NULL,
    updated_by_user_type    actor_user_type_enum    NULL,
    archived_at             TIMESTAMPTZ             NULL,
        -- Set when status transitions to ARCHIVED.
    archived_by_user_id     UUID                    NULL,
    archived_by_user_type   actor_user_type_enum    NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_org_nodes
        PRIMARY KEY (id),

    -- Composite FK: parent must be in the same tenant. Requires the
    -- (tenant_id, id) compound to be referencable, hence the unique
    -- constraint below.
    CONSTRAINT fk_org_nodes_parent_same_tenant
        FOREIGN KEY (tenant_id, parent_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT fk_org_nodes_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- Required by the composite FK above.
    CONSTRAINT uq_org_nodes_tenant_id
        UNIQUE (tenant_id, id),

    CONSTRAINT ck_org_nodes_name_length
        CHECK (LENGTH(name) BETWEEN 1 AND 200),

    CONSTRAINT ck_org_nodes_code_format
        CHECK (
            code ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]$'
            OR LENGTH(code) = 1
        ),

    -- TENANT nodes have no parent; all other nodes must have one.
    -- Free-form means we do not constrain WHICH parent type, only that
    -- non-tenant nodes have one.
    CONSTRAINT ck_org_nodes_root_parent_consistency
        CHECK (
            (node_type = 'TENANT' AND parent_id IS NULL)
            OR
            (node_type != 'TENANT' AND parent_id IS NOT NULL)
        ),

    CONSTRAINT ck_org_nodes_archived_consistency
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

    -- Pattern (b): each actor pair must be both NULL or both NOT NULL.
    CONSTRAINT ck_org_nodes_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_org_nodes_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK and the unique constraints (which auto-create indexes)
-- ----------------------------------------------------------------------------

-- Case-insensitive UNIQUE on code within a tenant. 'TX-101' and 'tx-101'
-- cannot both exist in the same tenant.
CREATE UNIQUE INDEX uq_org_nodes_tenant_code_lower
    ON org_nodes (tenant_id, LOWER(code));

-- Tenant-scoped queries (every multi-tenant access path filters by tenant_id).
CREATE INDEX ix_org_nodes_tenant
    ON org_nodes (tenant_id);

-- Direct-children lookup ("show me the kids of node X").
CREATE INDEX ix_org_nodes_parent
    ON org_nodes (parent_id);

-- Filter by node type within a tenant ("all stores in this tenant tree",
-- "all regions"). Useful for the tree-rendering query.
CREATE INDEX ix_org_nodes_tenant_type
    ON org_nodes (tenant_id, node_type);

-- ltree GIST index for descendant / ancestor queries (path <@ ?, ? @> path).
-- This is the load-bearing index for tree walks.
CREATE INDEX ix_org_nodes_path_gist
    ON org_nodes USING GIST (path);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
--
-- Uses the shared set_updated_at_timestamp() function defined in the
-- shared-utilities migration.
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_org_nodes_set_updated_at
    BEFORE UPDATE ON org_nodes
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- org_nodes is tenant-owned. The policy filters rows where tenant_id
-- matches the session-scoped app.tenant_id. Staff connections with
-- BYPASSRLS skip the policy entirely. FORCE RLS prevents the table owner
-- from also bypassing.
-- ----------------------------------------------------------------------------

ALTER TABLE org_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_nodes FORCE ROW LEVEL SECURITY;

CREATE POLICY org_nodes_tenant_isolation
    ON org_nodes
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- Note: current_setting('app.tenant_id', TRUE) returns NULL when the
-- session variable is unset. Default-deny by construction.
