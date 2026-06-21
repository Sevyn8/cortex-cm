-- ============================================================================
-- Ithina: tenant_users
-- Postgres SQL DDL
-- Version: v1
--
-- Customer-side users. Employees of a tenant organisation (Owner,
-- Pricing Manager, Store Manager, Associate, etc.) who use Ithina
-- modules within their tenant's scope.
--
-- Physically separated from platform_users (Pattern 2): different table,
-- different DB role, RLS enforced. Cross-tenant data leak is structurally
-- prevented because tenant_users has no rows for staff and platform_users
-- has no tenant_id.
--
-- Key properties:
--   * tenant_id NOT NULL. Every row belongs to one tenant.
--   * RLS enabled with FORCE; tenant-scoped policy on app.tenant_id.
--   * Auth0 is the credential authority. auth0_sub maps Auth0 identity
--     to local row. NULL during INVITED state, populated on first login.
--   * Email unique per tenant: UNIQUE(tenant_id, email). Same human
--     across tenants (e.g., Maria at Buc-ee's AND at Zabka) has two
--     separate rows. Future evolution to one-row-per-human noted in
--     forward-notes (FN-AB-XX).
--   * Audit columns use pattern (b): *_user_id UUID + *_user_type enum,
--     no FK. created_by/updated_by can be a platform_user (Phase 1:
--     Ithina staff invites tenant users) or eventually a tenant_user
--     (Phase 2: tenant admins invite their own users). The schema
--     supports both from day one; app layer controls which is permitted
--     in which phase.
--
-- Lifecycle:
--   * INVITED  -- row created by an authorised actor; invite email sent;
--                 Auth0 signup not yet completed; auth0_sub NULL.
--   * ACTIVE   -- invite accepted; auth0_sub populated; can log in.
--   * SUSPENDED-- temporarily disabled; auth0_sub retained; cannot log in.
--   No INACTIVE / no soft-delete in v1. Hard delete blocked by FK
--   RESTRICT on referencing tables (audit history must remain resolvable).
--
-- Dependencies (must exist before this file runs):
--   * tenants (id)
--   * Shared utilities migration providing:
--       - function set_updated_at_timestamp()
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enum types used by tenant_users
-- ----------------------------------------------------------------------------

CREATE TYPE tenant_user_status_enum AS ENUM (
    'INVITED',
    'ACTIVE',
    'SUSPENDED'
);


-- ----------------------------------------------------------------------------
-- tenant_users
-- ----------------------------------------------------------------------------

CREATE TABLE tenant_users (

    -- ---------- Surrogate primary key ----------
    id                          UUID                            NOT NULL DEFAULT uuidv7(),

    -- ---------- Ownership ----------
    tenant_id                   UUID                            NOT NULL,

    -- ---------- External identity ----------
    auth0_sub                   TEXT                            NULL,
        -- Auth0 'sub' claim. Populated when the user completes Auth0
        -- signup. NULL only while status = INVITED. Unique per tenant
        -- when set; a single human (same Auth0 sub) may appear in
        -- multiple tenants as separate rows.

    -- ---------- Identity ----------
    email                       TEXT                            NOT NULL,
        -- Stored lowercased; CHECK enforces no uppercase characters.
        -- Unique per tenant.
    full_name                   TEXT                            NOT NULL,

    -- ---------- Lifecycle ----------
    status                      tenant_user_status_enum         NOT NULL DEFAULT 'INVITED',

    -- ---------- Invitation ----------
    invited_at                  TIMESTAMPTZ                     NULL,
    invitation_accepted_at      TIMESTAMPTZ                     NULL,

    -- ---------- Suspension (pattern b) ----------
    suspended_at                TIMESTAMPTZ                     NULL,
    suspended_by_user_id        UUID                            NULL,
    suspended_by_user_type      actor_user_type_enum            NULL,

    -- ---------- Audit (pattern b) ----------
    created_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    created_by_user_id          UUID                            NULL,
    created_by_user_type        actor_user_type_enum            NULL,
    updated_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
    updated_by_user_id          UUID                            NULL,
    updated_by_user_type        actor_user_type_enum            NULL,

    -- ---------- Constraints ----------

    CONSTRAINT pk_tenant_users
        PRIMARY KEY (id),

    CONSTRAINT fk_tenant_users_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    CONSTRAINT ck_tenant_users_email_format
        CHECK (
            email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
        ),

    CONSTRAINT ck_tenant_users_email_lowercase
        CHECK (email = LOWER(email)),

    CONSTRAINT ck_tenant_users_full_name_length
        CHECK (LENGTH(full_name) BETWEEN 1 AND 200),

    -- auth0_sub may be NULL only while status = INVITED.
    CONSTRAINT ck_tenant_users_auth0_sub_consistency
        CHECK (
            (status = 'INVITED' AND auth0_sub IS NULL)
            OR
            (status IN ('ACTIVE', 'SUSPENDED') AND auth0_sub IS NOT NULL)
        ),

    -- invitation_accepted_at must be set once status leaves INVITED.
    CONSTRAINT ck_tenant_users_invitation_accepted_consistency
        CHECK (
            (status = 'INVITED'
                AND invitation_accepted_at IS NULL)
            OR
            (status IN ('ACTIVE', 'SUSPENDED')
                AND invitation_accepted_at IS NOT NULL)
        ),

    -- Pattern (b) requires id and type to be set together (or both NULL).
    CONSTRAINT ck_tenant_users_suspended_actor_pair
        CHECK (
            (suspended_by_user_id IS NULL AND suspended_by_user_type IS NULL)
            OR
            (suspended_by_user_id IS NOT NULL AND suspended_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_users_created_by_actor_pair
        CHECK (
            (created_by_user_id IS NULL AND created_by_user_type IS NULL)
            OR
            (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_users_updated_by_actor_pair
        CHECK (
            (updated_by_user_id IS NULL AND updated_by_user_type IS NULL)
            OR
            (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_users_suspended_consistency
        CHECK (
            (status = 'SUSPENDED'
                AND suspended_at IS NOT NULL
                AND suspended_by_user_id IS NOT NULL
                AND suspended_by_user_type IS NOT NULL)
            OR
            (status IN ('INVITED', 'ACTIVE')
                AND suspended_at IS NULL
                AND suspended_by_user_id IS NULL
                AND suspended_by_user_type IS NULL)
        )
);


-- ----------------------------------------------------------------------------
-- Indexes beyond PK (which auto-creates an index)
-- ----------------------------------------------------------------------------

-- Email unique per tenant.
CREATE UNIQUE INDEX uq_tenant_users_tenant_email
    ON tenant_users (tenant_id, email);

-- auth0_sub unique per tenant when set; multiple NULLs allowed (multiple
-- INVITED users coexist before any has signed up).
CREATE UNIQUE INDEX uq_tenant_users_tenant_auth0_sub
    ON tenant_users (tenant_id, auth0_sub)
    WHERE auth0_sub IS NOT NULL;

-- Tenant-scoped queries (RLS path).
CREATE INDEX ix_tenant_users_tenant
    ON tenant_users (tenant_id);

-- Tenant + status scans ("all active users for this tenant").
CREATE INDEX ix_tenant_users_tenant_status
    ON tenant_users (tenant_id, status);


-- ----------------------------------------------------------------------------
-- Trigger: keep updated_at fresh on every UPDATE
-- ----------------------------------------------------------------------------

CREATE TRIGGER tg_tenant_users_set_updated_at
    BEFORE UPDATE ON tenant_users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();


-- ----------------------------------------------------------------------------
-- Row-Level Security
--
-- tenant_users is tenant-owned. The policy filters rows where tenant_id
-- matches the session-scoped app.tenant_id. Staff connections with
-- BYPASSRLS skip the policy entirely. FORCE RLS prevents the table
-- owner from also bypassing.
-- ----------------------------------------------------------------------------

ALTER TABLE tenant_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_users FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_users_tenant_isolation
    ON tenant_users
    FOR ALL
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- Note: current_setting('app.tenant_id', TRUE) returns NULL when the
-- session variable is unset. tenant_id = NULL evaluates to UNKNOWN, so
-- the policy filters out all rows by default.


-- ============================================================================
-- Application-layer invariants
--
-- Captured here for reference. Not enforced by DDL in v1; document in
-- CLAUDE.md and enforce in service code.
--
-- AI-TU-01: Status transition INVITED -> SUSPENDED is not allowed.
--           Cancel the invite instead (see AI-TU-02). The DB CHECK
--           constraint ck_tenant_users_auth0_sub_consistency rejects
--           SUSPENDED with auth0_sub IS NULL, so this transition fails
--           at the DB layer for an unaccepted invite. App layer should
--           reject it explicitly with a clear error.
--
-- AI-TU-02: Hard-delete of a tenant_user is permitted ONLY when
--           status = INVITED and the user has never been ACTIVE
--           (invitation_accepted_at IS NULL). Once a user has been
--           ACTIVE, they cannot be deleted; use SUSPENDED instead.
--           Documented exception to the project-wide "no hard delete
--           of users" rule, scoped to cancelled or never-accepted
--           invites only.
--
-- AI-TU-03: Pattern (b) actor columns require app-layer validation:
--           when inserting/updating any *_by_user_id + *_by_user_type
--           pair, app must verify the UUID exists in the table indicated
--           by user_type (platform_users for PLATFORM, tenant_users for
--           TENANT). The DB does not enforce this referential link.
--           See FN-AB-09 for migration path to pattern (a) if drift
--           becomes an issue.
--
-- AI-TU-04: Phase 1: only platform_users may be the actor in
--           created_by_user_id, updated_by_user_id, and
--           suspended_by_user_id (i.e., user_type must be 'PLATFORM').
--           Phase 2: tenant admins gain user-management permission;
--           user_type may then be 'TENANT'. The schema supports both
--           from day one; the constraint is enforced in the service
--           layer based on the active phase.
--
-- AI-TU-05: Self-suspension (suspended_by_user_id = id when
--           suspended_by_user_type = 'TENANT' and id matches the row's
--           own id) is structurally allowed but should be rejected at
--           the app layer.
--
-- AI-TU-06: Email values must be lowercased before insert. The
--           ck_tenant_users_email_lowercase CHECK rejects mixed-case
--           values.
--
-- AI-TU-07: Future evolution -- one-row-per-human across tenants
--           (currently one row per tenant per human). Migration path
--           involves making auth0_sub globally unique and decoupling
--           tenant_id to a separate membership table. Parked.
--
-- AI-TU-08: App must set updated_by_user_id and updated_by_user_type
--           on every UPDATE. The trigger refreshes updated_at
--           automatically; the actor pair is app's responsibility.
--           Without this, the actor pair retains its previous value
--           and audit fidelity is lost for that update.
-- ============================================================================
