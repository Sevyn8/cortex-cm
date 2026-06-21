"""Smoke test for Ithina admin-backend schema invariants. Steps 1.5 + 2.2b
+ Step 6.8.1.

Self-contained Python script using psycopg3 sync API. Connects to local
Postgres via DATABASE_URL + DB_SCHEMA env vars (fails if either unset),
sets search_path explicitly per transaction, and runs assertions covering
tenant isolation under FORCE RLS, composite-FK same-tenant integrity on
tenant_user_role_assignments (post-Step-6.8.1 split — both
(tenant_id, tenant_user_id) and (tenant_id, org_node_id) composite FKs),
audience-check trigger rejection on the two new role-assignment tables
(Step 6.8.1), no-RLS structural assertion on
platform_user_role_assignments, status-consistency CHECK constraints,
domain CHECK constraints, the 6-table multi-tenant unconditional-OR
truth table (Step 3.0/3.4.5/6.8.1), the bootstrap platform_user pattern,
the UUIDv7 DEFAULT generation on metadata-table PKs, and a meta-
assertion that every multi-tenant table has RLS+FORCE+at-least-one-policy.

Each assertion (or tightly-coupled group) runs in its own
force_rollback transaction, so the DB returns to its starting state
when the script ends. Each transaction does its own setup; nothing
persists across transactions.

Usage:
    uv run python scripts/smoke_test.py

Env required:
    DATABASE_URL  psycopg-compatible connection string
    DB_SCHEMA     Postgres schema name (e.g., 'core')
"""

import os
import sys
import psycopg
from psycopg import sql


# ============================================================================
# Deterministic UUIDs for setup data.
#
# Hardcoded UUIDs let assertions reference rows by known ID. Note this
# means uuidv7() DEFAULT does NOT fire on rows inserted with explicit id
# values (the default is bypassed). Assertion 14 specifically tests the
# DEFAULT path by inserting one row WITHOUT an explicit id.
# ============================================================================

TENANT_A           = "00000000-0000-0000-0000-00000000aaaa"
TENANT_B           = "00000000-0000-0000-0000-00000000bbbb"
BOOTSTRAP_USER     = "00000000-0000-0000-0000-000000000001"
TENANT_A_USER      = "00000000-0000-0000-0000-00000000aaa1"
TENANT_B_USER      = "00000000-0000-0000-0000-00000000bbb1"
TENANT_A_ORG       = "00000000-0000-0000-0000-00000000a0a0"
TENANT_B_ORG       = "00000000-0000-0000-0000-00000000b0b0"
TENANT_A_STORE     = "00000000-0000-0000-0000-00000000a5a5"
TENANT_B_STORE     = "00000000-0000-0000-0000-00000000b5b5"
ROLE_PLATFORM      = "00000000-0000-0000-0000-0000000000a0"
ROLE_TENANT        = "00000000-0000-0000-0000-0000000000b0"
PERMISSION_VIEW    = "00000000-0000-0000-0000-0000000000c0"
ASSIGNMENT_PLATFORM = "00000000-0000-0000-0000-0000000000d0"
ASSIGNMENT_TENANT_A = "00000000-0000-0000-0000-0000000000e0"
ASSIGNMENT_TENANT_B = "00000000-0000-0000-0000-0000000000eb"

# Tenant C is used by test_16 (PLATFORM-INSERT) only. Distinct from
# TENANT_A/B so the assertion can run after the truth-table setup
# without UUID collisions inside its own force_rollback transaction.
TENANT_C           = "00000000-0000-0000-0000-00000000cccc"
TENANT_C_USER      = "00000000-0000-0000-0000-00000000ccc1"
TENANT_C_ORG       = "00000000-0000-0000-0000-00000000c0c0"
TENANT_C_STORE     = "00000000-0000-0000-0000-00000000c5c5"

# tenant_module_access rows (Step 3.4.5). One per tenant in the truth-
# table setup; one for Tenant C in test_16's INSERT assertion.
TENANT_A_TMA       = "00000000-0000-0000-0000-00000000a4a4"
TENANT_B_TMA       = "00000000-0000-0000-0000-00000000b4b4"
TENANT_C_TMA       = "00000000-0000-0000-0000-00000000c4c4"


# ============================================================================
# Result tracking
# ============================================================================

class Results:
    def __init__(self):
        self.entries = []
        self.notes = []

    def add(self, label, passed, error=None):
        self.entries.append((label, passed, error))
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} {label}")
        if not passed and error is not None:
            msg = str(error).strip().replace("\n", " | ")[:200]
            cls = type(error).__name__
            print(f"       {cls}: {msg}")

    def note(self, msg):
        self.notes.append(msg)

    def total(self):
        return len(self.entries)

    def passed_count(self):
        return sum(1 for _, p, _ in self.entries if p)

    def failed_count(self):
        return sum(1 for _, p, _ in self.entries if not p)


# ============================================================================
# Cursor helpers
# ============================================================================

def set_search_path(cur, db_schema):
    """SET LOCAL search_path. Parameterised via psycopg.sql.Identifier."""
    cur.execute(
        sql.SQL("SET LOCAL search_path TO {}, public").format(
            sql.Identifier(db_schema)
        )
    )


def set_tenant(cur, tenant_uuid):
    """Set app.tenant_id (transaction-local) before INSERTs/SELECTs on RLS-FORCEd tables.

    Uses set_config(name, value, is_local=true) instead of SET LOCAL because
    the SET command does not accept parameter binding via libpq's prepared-
    statement protocol. set_config is a regular function call and accepts
    parameters cleanly. Pass None for "unset"; the GUC ends up at empty
    string post-set on this connection (Postgres 15 placeholder-GUC
    behaviour), and the NULLIF wrapper in the RLS policies turns '' back
    into NULL for the tenant_id check (per D-27).
    """
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_uuid,))


def set_user_type(cur, user_type):
    """Set app.user_type (transaction-local).

    Used for the user_role_assignments OR-clause (FN-AB-14): when
    app.user_type='PLATFORM', PLATFORM-audience rows (tenant_id NULL)
    become visible. Pass None for "unset"; the empty-string result does
    not equal 'PLATFORM' so the OR-branch does not fire.
    """
    cur.execute("SELECT set_config('app.user_type', %s, true)", (user_type,))


# ============================================================================
# Setup helpers (each takes a cursor; assumes search_path and tenant context
# are already set by the caller as needed)
# ============================================================================

def insert_bootstrap_user(cur):
    """The first platform_user. Audit columns NULL (chicken-and-egg: no prior user)."""
    cur.execute(
        """
        INSERT INTO platform_users (
            id, email, full_name, status,
            created_by_user_id, updated_by_user_id, suspended_by_user_id
        )
        VALUES (
            %s, 'system@ithina.local', 'System Bootstrap', 'INVITED',
            NULL, NULL, NULL
        )
        """,
        (BOOTSTRAP_USER,),
    )


def insert_tenant(cur, tenant_id, name):
    """A tenant. Pattern (a) audit columns: FK to platform_users, no _user_type."""
    cur.execute(
        """
        INSERT INTO tenants (
            id, name, region, status,
            created_by_user_id, updated_by_user_id
        )
        VALUES (%s, %s, 'US', 'ACTIVE', %s, %s)
        """,
        (tenant_id, name, BOOTSTRAP_USER, BOOTSTRAP_USER),
    )


def insert_tenant_user(cur, user_id, tenant_id, email, full_name):
    """A tenant_user. Pattern (b) audit: UUID + actor_user_type_enum, no FK."""
    cur.execute(
        """
        INSERT INTO tenant_users (
            id, tenant_id, email, full_name, status,
            created_by_user_id, created_by_user_type,
            updated_by_user_id, updated_by_user_type
        )
        VALUES (
            %s, %s, %s, %s, 'INVITED',
            %s, 'PLATFORM', %s, 'PLATFORM'
        )
        """,
        (user_id, tenant_id, email, full_name, BOOTSTRAP_USER, BOOTSTRAP_USER),
    )


def insert_org_node(cur, node_id, tenant_id, parent_id, node_type, code, name, path):
    """An org_node. path is ltree; for TENANT root, single label."""
    cur.execute(
        """
        INSERT INTO org_nodes (
            id, tenant_id, parent_id, path, node_type, name, code, status,
            created_by_user_id, created_by_user_type,
            updated_by_user_id, updated_by_user_type
        )
        VALUES (
            %s, %s, %s, %s::ltree, %s, %s, %s, 'ACTIVE',
            %s, 'PLATFORM', %s, 'PLATFORM'
        )
        """,
        (node_id, tenant_id, parent_id, path, node_type, name, code,
         BOOTSTRAP_USER, BOOTSTRAP_USER),
    )


def insert_store(cur, store_id, tenant_id, org_node_id, name,
                 country='United States', timezone='America/New_York',
                 currency='USD', tax_treatment='EXCLUSIVE'):
    cur.execute(
        """
        INSERT INTO stores (
            id, tenant_id, org_node_id, name, country, timezone,
            currency, tax_treatment, status,
            created_by_user_id, created_by_user_type,
            updated_by_user_id, updated_by_user_type
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, 'ACTIVE',
            %s, 'PLATFORM', %s, 'PLATFORM'
        )
        """,
        (store_id, tenant_id, org_node_id, name, country, timezone,
         currency, tax_treatment, BOOTSTRAP_USER, BOOTSTRAP_USER),
    )


def insert_permission_view(cur):
    """A platform-global permission row (no RLS)."""
    cur.execute(
        """
        INSERT INTO permissions (id, module, resource, action, scope, code)
        VALUES (%s, 'ADMIN', 'TENANTS', 'VIEW', 'GLOBAL', 'ADMIN.TENANTS.VIEW.GLOBAL')
        """,
        (PERMISSION_VIEW,),
    )


def insert_role(cur, role_id, name, code, audience):
    """A platform-global role (no RLS). Pattern (b) audit columns."""
    cur.execute(
        """
        INSERT INTO roles (
            id, name, code, audience, status,
            created_by_user_id, created_by_user_type,
            updated_by_user_id, updated_by_user_type
        )
        VALUES (%s, %s, %s, %s, 'ACTIVE', %s, 'PLATFORM', %s, 'PLATFORM')
        """,
        (role_id, name, code, audience, BOOTSTRAP_USER, BOOTSTRAP_USER),
    )


def insert_tenant_module_access(cur, tma_id, tenant_id, module='GOAL_CONSOLE'):
    """A tenant_module_access row (Step 3.4.5).

    Pattern (a) audit-actors: typed FK direct to platform_users
    (BOOTSTRAP_USER); no *_by_user_type discriminator. Status is
    ENABLED so disabled_at / disabled_by_user_id stay NULL per the
    ck_tenant_module_access_status_consistency CHECK.
    """
    cur.execute(
        """
        INSERT INTO tenant_module_access (
            id, tenant_id, module, status,
            enabled_at, enabled_by_user_id,
            created_by_user_id, updated_by_user_id
        )
        VALUES (
            %s, %s, %s, 'ENABLED',
            NOW(), %s,
            %s, %s
        )
        """,
        (tma_id, tenant_id, module,
         BOOTSTRAP_USER, BOOTSTRAP_USER, BOOTSTRAP_USER),
    )


def setup_tenant_a_data(cur):
    """Set context to A; insert tenant A's tenant row + dependent rows."""
    set_tenant(cur, TENANT_A)
    insert_tenant(cur, TENANT_A, "Tenant A")
    insert_tenant_user(cur, TENANT_A_USER, TENANT_A, "user-a@a.test", "User A")
    insert_org_node(cur, TENANT_A_ORG, TENANT_A, None, "TENANT",
                    "tenanta", "Tenant A Root", "tenanta")
    insert_store(cur, TENANT_A_STORE, TENANT_A, TENANT_A_ORG, "Store A")
    insert_tenant_module_access(cur, TENANT_A_TMA, TENANT_A, 'GOAL_CONSOLE')


def setup_tenant_b_data(cur):
    set_tenant(cur, TENANT_B)
    insert_tenant(cur, TENANT_B, "Tenant B")
    insert_tenant_user(cur, TENANT_B_USER, TENANT_B, "user-b@b.test", "User B")
    insert_org_node(cur, TENANT_B_ORG, TENANT_B, None, "TENANT",
                    "tenantb", "Tenant B Root", "tenantb")
    insert_store(cur, TENANT_B_STORE, TENANT_B, TENANT_B_ORG, "Store B")
    insert_tenant_module_access(cur, TENANT_B_TMA, TENANT_B, 'GOAL_CONSOLE')


# ============================================================================
# Test functions: one per assertion (or tightly-coupled group)
#
# Each function opens its own force_rollback transaction. Setup happens
# inside that transaction; assertions happen inside that transaction;
# rollback at exit. Nothing persists.
# ============================================================================

def test_1_tenant_a_select(conn, db_schema, R):
    """Assertion 1: with app.tenant_id = TENANT_A, MT SELECTs return only A's rows."""
    label = "1: tenant A SELECT shows only A's rows"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            setup_tenant_a_data(cur)

            # Already under TENANT_A context. Verify each MT table.
            failures = []
            for table, expected in (
                ("tenants", 1),
                ("tenant_users", 1),
                ("org_nodes", 1),
                ("stores", 1),
            ):
                cur.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                )
                got = cur.fetchone()[0]
                if got != expected:
                    failures.append(f"{table}: expected {expected}, got {got}")

            cur.execute("SELECT id::text FROM tenants")
            tenant_ids = sorted(r[0] for r in cur.fetchall())
            if tenant_ids != [TENANT_A]:
                failures.append(f"tenants ids: expected [{TENANT_A}], got {tenant_ids}")

            if failures:
                R.add(label, False, RuntimeError("; ".join(failures)))
            else:
                R.add(label, True)
    except Exception as e:
        R.add(label, False, e)


def test_2_tenant_b_select(conn, db_schema, R):
    """Assertion 2: with app.tenant_id = TENANT_B, MT SELECTs return only B's rows."""
    label = "2: tenant B SELECT shows only B's rows"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            setup_tenant_b_data(cur)

            failures = []
            for table, expected in (
                ("tenants", 1),
                ("tenant_users", 1),
                ("org_nodes", 1),
                ("stores", 1),
            ):
                cur.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                )
                got = cur.fetchone()[0]
                if got != expected:
                    failures.append(f"{table}: expected {expected}, got {got}")

            cur.execute("SELECT id::text FROM tenants")
            tenant_ids = sorted(r[0] for r in cur.fetchall())
            if tenant_ids != [TENANT_B]:
                failures.append(f"tenants ids: expected [{TENANT_B}], got {tenant_ids}")

            if failures:
                R.add(label, False, RuntimeError("; ".join(failures)))
            else:
                R.add(label, True)
    except Exception as e:
        R.add(label, False, e)


def test_3_default_deny(conn, db_schema, R, database_url):
    """Assertion 3: with app.tenant_id NOT SET, MT SELECTs return 0 rows.

    Uses a FRESH CONNECTION because once any prior transaction in this
    session has SET app.tenant_id (even with is_local=true and even after
    rollback), the custom GUC stays REGISTERED for the connection's
    lifetime, with empty string '' as the post-rollback value rather than
    true NULL. Verified empirically in pre-script probes. Only a fresh
    connection that has never SET app.tenant_id yields a genuinely
    unregistered GUC where current_setting('app.tenant_id', TRUE) IS NULL.
    The Step 1.5 prompt (FORCE RLS gotcha section) calls out that the
    pre-verification of NULL is what makes the assertion meaningful.
    """
    label = "3: default-deny — MT SELECTs return 0 rows on unset session"
    fresh_conn = None
    try:
        # Open a connection that has never SET app.tenant_id.
        fresh_conn = psycopg.connect(database_url, autocommit=False)
        with fresh_conn.transaction(force_rollback=True):
            cur = fresh_conn.cursor()
            set_search_path(cur, db_schema)

            # Pre-check: GUC must be NULL on this fresh connection
            cur.execute(
                "SELECT current_setting('app.tenant_id', TRUE) IS NULL, "
                "       current_setting('app.tenant_id', TRUE)"
            )
            is_null, val = cur.fetchone()
            if not is_null:
                R.add(
                    "3 pre-check: app.tenant_id IS NULL on fresh connection",
                    False,
                    RuntimeError(f"expected NULL, got {val!r}"),
                )
                R.add(label, False, RuntimeError("pre-check failed"))
                return
            R.add("3 pre-check: app.tenant_id IS NULL on fresh connection", True)

            # SELECT each MT table; all should return 0 (default-deny).
            # Step 6.8.1: user_role_assignments split into
            # tenant_user_role_assignments (RLS+FORCE) and
            # platform_user_role_assignments (no RLS, omitted from this
            # default-deny check).
            failures = []
            for table in ("tenants", "tenant_users", "org_nodes", "stores",
                          "tenant_user_role_assignments"):
                try:
                    cur.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                    )
                    got = cur.fetchone()[0]
                    if got != 0:
                        failures.append(f"{table}: expected 0 rows, got {got}")
                except Exception as e:
                    failures.append(f"{table}: {type(e).__name__}: {str(e)[:100]}")

            if failures:
                R.add(label, False, RuntimeError("; ".join(failures)))
            else:
                R.add(label, True)
    except Exception as e:
        R.add(label, False, e)
    finally:
        if fresh_conn is not None:
            fresh_conn.close()


def test_4_cross_tenant_insert_rejected(conn, db_schema, R):
    """Assertion 4: with TENANT_A context, inserting a store with tenant_id=B is REJECTED."""
    label = "4: cross-tenant INSERT rejected by RLS WITH CHECK"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            setup_tenant_a_data(cur)
            setup_tenant_b_data(cur)
            # Switch context to A (last set_tenant call was TENANT_B)
            set_tenant(cur, TENANT_A)

            try:
                cur.execute(
                    """
                    INSERT INTO stores (
                        id, tenant_id, org_node_id, name, country, timezone,
                        currency, tax_treatment, status,
                        created_by_user_id, created_by_user_type,
                        updated_by_user_id, updated_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-0000beef0001', %s, %s, 'Bad Store',
                        'United States', 'America/New_York', 'USD', 'EXCLUSIVE',
                        'ACTIVE', %s, 'PLATFORM', %s, 'PLATFORM'
                    )
                    """,
                    (TENANT_B, TENANT_B_ORG, BOOTSTRAP_USER, BOOTSTRAP_USER),
                )
                R.add(label, False,
                      RuntimeError("INSERT succeeded; RLS WITH CHECK should have rejected"))
            except Exception as e:
                R.add(label, True, e)
    except Exception as e:
        R.add(label, False, e)


def test_5_stores_composite_fk(conn, db_schema, R):
    """Assertion 5: stores referencing org_node from a different tenant is REJECTED."""
    label = "5: stores composite-FK same-tenant rejection"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            setup_tenant_a_data(cur)
            setup_tenant_b_data(cur)
            # Already under TENANT_B. Try to insert a store with tenant_id=B
            # but org_node_id pointing at A's org_node (different tenant).
            try:
                cur.execute(
                    """
                    INSERT INTO stores (
                        id, tenant_id, org_node_id, name, country, timezone,
                        currency, tax_treatment, status,
                        created_by_user_id, created_by_user_type,
                        updated_by_user_id, updated_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-0000beef5555', %s, %s, 'Cross-tenant Store',
                        'United States', 'America/New_York', 'USD', 'EXCLUSIVE',
                        'ACTIVE', %s, 'PLATFORM', %s, 'PLATFORM'
                    )
                    """,
                    (TENANT_B, TENANT_A_ORG, BOOTSTRAP_USER, BOOTSTRAP_USER),
                )
                R.add(label, False,
                      RuntimeError("INSERT succeeded; composite FK should have rejected"))
            except Exception as e:
                R.add(label, True, e)
    except Exception as e:
        R.add(label, False, e)


def test_6_org_nodes_parent_composite_fk(conn, db_schema, R):
    """Assertion 6: org_nodes.parent_id pointing at a different tenant's node is REJECTED."""
    label = "6: org_nodes.parent_id composite-FK same-tenant rejection"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            setup_tenant_a_data(cur)
            setup_tenant_b_data(cur)
            # Already under TENANT_B. Try to insert an org_node under tenant B
            # whose parent_id points at A's TENANT_A_ORG (different tenant).
            try:
                cur.execute(
                    """
                    INSERT INTO org_nodes (
                        id, tenant_id, parent_id, path, node_type, name, code, status,
                        created_by_user_id, created_by_user_type,
                        updated_by_user_id, updated_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-0000beef6666', %s, %s,
                        'tenantb.cross'::ltree, 'REGION', 'Cross Region', 'crossreg', 'ACTIVE',
                        %s, 'PLATFORM', %s, 'PLATFORM'
                    )
                    """,
                    (TENANT_B, TENANT_A_ORG, BOOTSTRAP_USER, BOOTSTRAP_USER),
                )
                R.add(label, False,
                      RuntimeError("INSERT succeeded; composite FK should have rejected"))
            except Exception as e:
                R.add(label, True, e)
    except Exception as e:
        R.add(label, False, e)


def test_7_assignment_composite_fk_same_tenant(conn, db_schema, R):
    """Assertions 7a + 7b: tenant_user_role_assignments composite-FK
    same-tenant rejection on BOTH the org_node side AND the tenant_user
    side.

    Post-Step-6.8.1 split, ``tenant_user_role_assignments`` declares
    two composite FKs:
      * (tenant_id, tenant_user_id) -> tenant_users (tenant_id, id)
      * (tenant_id, org_node_id)    -> org_nodes (tenant_id, id)

    These are the structural-impossibility guarantee for AI-RBAC-06
    cross-tenant injection: a row cannot pair user-from-tenant-A with
    tenant_id=B, nor org_node-from-tenant-A with tenant_id=B.

    Pre-split, only the org_node composite FK existed (the tenant_user
    side was app-layer only per AI-RBAC-06's forward note).
    """
    label_a = "7a: tenant_user_role_assignments org_node composite-FK rejects mismatched tenant"
    label_b = "7b: tenant_user_role_assignments tenant_user composite-FK rejects mismatched tenant"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)

            # PLATFORM context so we can insert tenants A and B in setup.
            set_user_type(cur, "PLATFORM")
            setup_tenant_a_data(cur)
            setup_tenant_b_data(cur)
            insert_role(cur, ROLE_TENANT, "Owner", "OWNER", "TENANT")

            # 7a: org_node from tenant A but row tenant_id=B.
            try:
                with conn.transaction():  # savepoint
                    set_tenant(cur, TENANT_B)
                    cur.execute(
                        """
                        INSERT INTO tenant_user_role_assignments (
                            id, tenant_user_id, tenant_id, org_node_id,
                            role_id, status,
                            granted_by_user_id, granted_by_user_type
                        )
                        VALUES (
                            '00000000-0000-0000-0000-0000beef7777',
                            %s, %s, %s, %s, 'ACTIVE',
                            %s, 'PLATFORM'
                        )
                        """,
                        (TENANT_B_USER, TENANT_B, TENANT_A_ORG,
                         ROLE_TENANT, BOOTSTRAP_USER),
                    )
                R.add(label_a, False,
                      RuntimeError("INSERT succeeded; composite FK should have rejected"))
            except Exception as e:
                R.add(label_a, True, e)

            # 7b: tenant_user from tenant A but row tenant_id=B.
            try:
                with conn.transaction():  # savepoint
                    set_tenant(cur, TENANT_B)
                    cur.execute(
                        """
                        INSERT INTO tenant_user_role_assignments (
                            id, tenant_user_id, tenant_id, org_node_id,
                            role_id, status,
                            granted_by_user_id, granted_by_user_type
                        )
                        VALUES (
                            '00000000-0000-0000-0000-0000beef7778',
                            %s, %s, %s, %s, 'ACTIVE',
                            %s, 'PLATFORM'
                        )
                        """,
                        (TENANT_A_USER, TENANT_B, TENANT_B_ORG,
                         ROLE_TENANT, BOOTSTRAP_USER),
                    )
                R.add(label_b, False,
                      RuntimeError("INSERT succeeded; composite FK should have rejected"))
            except Exception as e:
                R.add(label_b, True, e)
    except Exception as e:
        R.add(label_a, False, e)
        R.add(label_b, False, e)


def test_8_tenants_terminated_consistency(conn, db_schema, R):
    """Assertions 8a + 8b: tenants status/terminated_at consistency CHECK."""
    label_a = "8a: tenants status=TERMINATED + terminated_at NULL rejected"
    label_b = "8b: tenants status!=TERMINATED + terminated_at set rejected"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)

            # 8a: status=TERMINATED but terminated_at NULL. Use a savepoint
            # so the failing INSERT doesn't poison this transaction.
            tenant_8a = "00000000-0000-0000-0000-0000000008aa"
            try:
                with conn.transaction():  # savepoint
                    set_tenant(cur, tenant_8a)
                    cur.execute(
                        """
                        INSERT INTO tenants (
                            id, name, region, status, terminated_at, terminated_by_user_id,
                            created_by_user_id, updated_by_user_id
                        )
                        VALUES (%s, '8a Tenant', 'US', 'TERMINATED', NULL, NULL, %s, %s)
                        """,
                        (tenant_8a, BOOTSTRAP_USER, BOOTSTRAP_USER),
                    )
                R.add(label_a, False,
                      RuntimeError("INSERT succeeded; status-consistency CHECK should have rejected"))
            except Exception as e:
                R.add(label_a, True, e)

            # 8b: status=ACTIVE but terminated_at set
            tenant_8b = "00000000-0000-0000-0000-0000000008bb"
            try:
                with conn.transaction():  # savepoint
                    set_tenant(cur, tenant_8b)
                    cur.execute(
                        """
                        INSERT INTO tenants (
                            id, name, region, status, terminated_at, terminated_by_user_id,
                            created_by_user_id, updated_by_user_id
                        )
                        VALUES (%s, '8b Tenant', 'US', 'ACTIVE', NOW(), %s, %s, %s)
                        """,
                        (tenant_8b, BOOTSTRAP_USER, BOOTSTRAP_USER, BOOTSTRAP_USER),
                    )
                R.add(label_b, False,
                      RuntimeError("INSERT succeeded; status-consistency CHECK should have rejected"))
            except Exception as e:
                R.add(label_b, True, e)
    except Exception as e:
        R.add(label_a, False, e)
        R.add(label_b, False, e)


def test_9_platform_user_suspended_consistency(conn, db_schema, R):
    """Assertion 9: UPDATE platform_users SET suspended_at without status=SUSPENDED is REJECTED."""
    label = "9: platform_users suspended_at requires status=SUSPENDED (CHECK)"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            # Bootstrap user has status=INVITED, suspended_at=NULL.
            # Try to set suspended_at while leaving status=INVITED.
            try:
                cur.execute(
                    """
                    UPDATE platform_users
                       SET suspended_at = NOW(),
                           suspended_by_user_id = %s
                     WHERE id = %s
                    """,
                    (BOOTSTRAP_USER, BOOTSTRAP_USER),
                )
                R.add(label, False,
                      RuntimeError("UPDATE succeeded; CHECK should have rejected"))
            except Exception as e:
                R.add(label, True, e)
    except Exception as e:
        R.add(label, False, e)


def test_10_currency_check(conn, db_schema, R):
    """Assertion 10: stores.currency lowercase fails the regex CHECK."""
    label = "10: stores.currency lowercase fails CHECK regex"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            setup_tenant_a_data(cur)
            try:
                cur.execute(
                    """
                    INSERT INTO stores (
                        id, tenant_id, org_node_id, name, country, timezone,
                        currency, tax_treatment, status,
                        created_by_user_id, created_by_user_type,
                        updated_by_user_id, updated_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-0000beef1010', %s, %s, 'Bad Currency',
                        'United States', 'America/New_York', 'usd', 'EXCLUSIVE',
                        'ACTIVE', %s, 'PLATFORM', %s, 'PLATFORM'
                    )
                    """,
                    (TENANT_A, TENANT_A_ORG, BOOTSTRAP_USER, BOOTSTRAP_USER),
                )
                R.add(label, False,
                      RuntimeError("INSERT succeeded; currency CHECK should have rejected"))
            except Exception as e:
                R.add(label, True, e)
    except Exception as e:
        R.add(label, False, e)


def test_11_role_assignment_split_invariants(conn, db_schema, R):
    """Assertions 11.a-11.d: structural invariants on the split
    role-assignment tables (Step 6.8.1).

    Pre-split this slot held a 9-row truth table on user_role_assignments
    documenting the FN-AB-14 IS-NULL-gated visibility behaviour. The
    split retired that table; tenant_user_role_assignments now uses the
    unconditional OR-branch (covered by test_15's 6-table truth table).

    Post-split this slot covers what's specific to the new shape:

      11a. platform_user_role_assignments has NO RLS (relrowsecurity=f,
           relforcerowsecurity=f). Every session sees every row;
           visibility is at the application layer.
      11b. tenant_user_role_assignments has RLS+FORCE (covered by
           test_12 meta-assertion, but explicit here for symmetry).
      11c. enforce_platform_role_audience trigger rejects an INSERT
           into platform_user_role_assignments with a TENANT-audience
           role.
      11d. enforce_tenant_role_audience trigger rejects an INSERT
           into tenant_user_role_assignments with a PLATFORM-audience
           role.
    """

    # 11a: platform_user_role_assignments no-RLS structural check.
    label_a = "11a: platform_user_role_assignments has no RLS (relrowsecurity=false)"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            cur.execute(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE c.relname = 'platform_user_role_assignments'
                  AND n.nspname = %s
                """,
                (db_schema,),
            )
            row = cur.fetchone()
            if row is not None and row[0] is False and row[1] is False:
                R.add(label_a, True)
            else:
                R.add(label_a, False, RuntimeError(
                    f"expected (relrowsecurity, relforcerowsecurity)=(False, False); got {row}"))
    except Exception as e:
        R.add(label_a, False, e)

    # 11b: tenant_user_role_assignments RLS+FORCE structural check.
    label_b = "11b: tenant_user_role_assignments has RLS+FORCE"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            cur.execute(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE c.relname = 'tenant_user_role_assignments'
                  AND n.nspname = %s
                """,
                (db_schema,),
            )
            row = cur.fetchone()
            if row is not None and row[0] is True and row[1] is True:
                R.add(label_b, True)
            else:
                R.add(label_b, False, RuntimeError(
                    f"expected (relrowsecurity, relforcerowsecurity)=(True, True); got {row}"))
    except Exception as e:
        R.add(label_b, False, e)

    # 11c: audience-check trigger on platform_user_role_assignments
    # rejects TENANT-audience role.
    label_c = "11c: enforce_platform_role_audience rejects TENANT-audience role"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            set_user_type(cur, "PLATFORM")
            insert_role(cur, ROLE_TENANT, "Owner", "OWNER", "TENANT")
            try:
                cur.execute(
                    """
                    INSERT INTO platform_user_role_assignments (
                        id, platform_user_id, role_id, status,
                        granted_by_user_id, granted_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-00000000110c',
                        %s, %s, 'ACTIVE', %s, 'PLATFORM'
                    )
                    """,
                    (BOOTSTRAP_USER, ROLE_TENANT, BOOTSTRAP_USER),
                )
                R.add(label_c, False,
                      RuntimeError("INSERT succeeded; audience-check trigger should have rejected"))
            except Exception as e:
                R.add(label_c, True, e)
    except Exception as e:
        R.add(label_c, False, e)

    # 11d: audience-check trigger on tenant_user_role_assignments
    # rejects PLATFORM-audience role.
    label_d = "11d: enforce_tenant_role_audience rejects PLATFORM-audience role"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            set_user_type(cur, "PLATFORM")
            setup_tenant_a_data(cur)
            insert_role(cur, ROLE_PLATFORM, "Super Admin", "SUPER_ADMIN", "PLATFORM")
            try:
                cur.execute(
                    """
                    INSERT INTO tenant_user_role_assignments (
                        id, tenant_user_id, tenant_id, org_node_id,
                        role_id, status,
                        granted_by_user_id, granted_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-00000000110d',
                        %s, %s, %s, %s, 'ACTIVE',
                        %s, 'PLATFORM'
                    )
                    """,
                    (TENANT_A_USER, TENANT_A, TENANT_A_ORG,
                     ROLE_PLATFORM, BOOTSTRAP_USER),
                )
                R.add(label_d, False,
                      RuntimeError("INSERT succeeded; audience-check trigger should have rejected"))
            except Exception as e:
                R.add(label_d, True, e)
    except Exception as e:
        R.add(label_d, False, e)


def test_12_meta_multi_tenant_tables_have_rls(conn, db_schema, R):
    """Assertion 12 (meta): every table with a tenant_id column has RLS
    enabled, FORCE enabled, and at least one policy.

    Catches future "added a multi-tenant table and forgot RLS" mistakes.
    Tables with id-as-tenant-id (only `tenants` itself) are not in
    scope of this query — they have no `tenant_id` column. The
    policy-existence check uses pg_policies regardless of policy name
    (tenants uses *_self_access; others use *_tenant_isolation).
    """
    label = "12 (meta): every table with tenant_id has RLS+FORCE+policy"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            cur.execute(
                """
                SELECT t.tablename
                  FROM pg_tables t
                  JOIN information_schema.columns c
                    ON c.table_schema = t.schemaname
                   AND c.table_name = t.tablename
                   AND c.column_name = 'tenant_id'
                 WHERE t.schemaname = %s
                   AND (
                     NOT EXISTS (
                       SELECT 1
                         FROM pg_class pc
                         JOIN pg_namespace pn ON pc.relnamespace = pn.oid
                        WHERE pc.relname = t.tablename
                          AND pn.nspname = t.schemaname
                          AND pc.relrowsecurity = TRUE
                          AND pc.relforcerowsecurity = TRUE
                     )
                     OR NOT EXISTS (
                       SELECT 1
                         FROM pg_policies pp
                        WHERE pp.schemaname = t.schemaname
                          AND pp.tablename = t.tablename
                     )
                   )
                """,
                (db_schema,),
            )
            offenders = [r[0] for r in cur.fetchall()]
            if offenders:
                R.add(label, False,
                      RuntimeError(
                          "missing RLS/FORCE/policy on: "
                          f"{', '.join(offenders)}"
                      ))
            else:
                R.add(label, True)
    except Exception as e:
        R.add(label, False, e)


def test_13_bootstrap_user_pattern(conn, db_schema, R):
    """Assertion 13: bootstrap platform_user inserts cleanly and is readable."""
    label = "13: bootstrap platform_user inserts and is readable"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)
            cur.execute(
                "SELECT id::text, email, status FROM platform_users WHERE id = %s",
                (BOOTSTRAP_USER,),
            )
            row = cur.fetchone()
            if (row is not None
                    and row[0] == BOOTSTRAP_USER
                    and row[1] == 'system@ithina.local'
                    and row[2] == 'INVITED'):
                R.add(label, True)
            else:
                R.add(label, False, RuntimeError(f"bootstrap row not as expected: {row}"))
    except Exception as e:
        R.add(label, False, e)


def test_15_multi_tenant_or_clause_truth_tables(conn, db_schema, R):
    """Assertions 15.<table>.<cell>: 9-row truth table on each of the
    four multi-tenant tables (tenants, tenant_users, org_nodes, stores).

    Step 3.0 lands the unconditional PLATFORM OR-clause on these four
    policies. Two rows are inserted per table (TENANT-A, TENANT-B) and
    9 (app.tenant_id, app.user_type) combinations are queried for
    visibility against the truth table:

    | tenant_id | user_type | A row | B row | total |
    |-----------|-----------|-------|-------|-------|
    | A         | TENANT    | yes   | no    | 1     |
    | B         | TENANT    | no    | yes   | 1     |
    | unset     | TENANT    | no    | no    | 0     |
    | A         | PLATFORM  | yes   | yes   | 2     |
    | B         | PLATFORM  | yes   | yes   | 2     |
    | unset     | PLATFORM  | yes   | yes   | 2     |
    | A         | unset     | yes   | no    | 1     |
    | B         | unset     | no    | yes   | 1     |
    | unset     | unset     | no    | no    | 0     |

    PLATFORM rows 4-6 see BOTH tenants regardless of app.tenant_id.
    This is the new behaviour Step 3.0 unlocks; under the pre-3.0
    policy, rows 4 and 5 saw 1 (tenant_id-clause only) and row 6 saw 0.

    The PLATFORM-row class from test_11 doesn't apply here: tenant_id
    (or in `tenants` case `id`) is NOT NULL on these four tables, so
    no PLATFORM-audience rows can exist.
    """
    truth_table = [
        # (tid_var, ut_var, expected_count, row_label)
        (TENANT_A, "TENANT",   1, "A/TENANT"),
        (TENANT_B, "TENANT",   1, "B/TENANT"),
        (None,     "TENANT",   0, "unset/TENANT"),
        (TENANT_A, "PLATFORM", 2, "A/PLATFORM"),
        (TENANT_B, "PLATFORM", 2, "B/PLATFORM"),
        (None,     "PLATFORM", 2, "unset/PLATFORM"),
        (TENANT_A, None,       1, "A/unset"),
        (TENANT_B, None,       1, "B/unset"),
        (None,     None,       0, "unset/unset"),
    ]
    # tenant_module_access (added at Step 3.4.5) follows the same
    # NOT-NULL-tenant_id, unconditional D-29 OR-clause pattern as the
    # original four. Its truth table is structurally identical: A row
    # + B row, no PLATFORM-audience row class.
    #
    # tenant_user_role_assignments (added at Step 6.8.1; replaces the
    # split user_role_assignments' TENANT-side rows) joins this set:
    # NOT-NULL tenant_id, unconditional D-29 OR-branch policy. Same
    # truth-table shape as the other 5.
    tables = (
        "tenants",
        "tenant_users",
        "org_nodes",
        "stores",
        "tenant_module_access",
        "tenant_user_role_assignments",
    )

    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)

            # Setup runs as PLATFORM so the new OR-clause permits the
            # INSERTs whose `app.tenant_id = TENANT_X` first-clause
            # match would also pass it. PLATFORM here is belt-and-
            # suspenders: it ensures the test stays green even if the
            # first-clause path is broken on a future regression.
            set_user_type(cur, "PLATFORM")

            setup_tenant_a_data(cur)
            setup_tenant_b_data(cur)

            # For tenant_user_role_assignments, also insert a TENANT-
            # audience role + one assignment per tenant so the truth
            # table has 2 rows (one per tenant) just like the other 5
            # tables. Roles are platform-global (no RLS); insertable
            # under any session.
            insert_role(cur, ROLE_TENANT, "Owner", "OWNER", "TENANT")

            # TENANT_A's assignment
            set_tenant(cur, TENANT_A)
            cur.execute(
                """
                INSERT INTO tenant_user_role_assignments (
                    id, tenant_user_id, tenant_id, org_node_id,
                    role_id, status,
                    granted_by_user_id, granted_by_user_type
                )
                VALUES (
                    %s, %s, %s, %s, %s, 'ACTIVE',
                    %s, 'PLATFORM'
                )
                """,
                (ASSIGNMENT_TENANT_A, TENANT_A_USER, TENANT_A, TENANT_A_ORG,
                 ROLE_TENANT, BOOTSTRAP_USER),
            )

            # TENANT_B's assignment
            set_tenant(cur, TENANT_B)
            cur.execute(
                """
                INSERT INTO tenant_user_role_assignments (
                    id, tenant_user_id, tenant_id, org_node_id,
                    role_id, status,
                    granted_by_user_id, granted_by_user_type
                )
                VALUES (
                    %s, %s, %s, %s, %s, 'ACTIVE',
                    %s, 'PLATFORM'
                )
                """,
                (ASSIGNMENT_TENANT_B, TENANT_B_USER, TENANT_B, TENANT_B_ORG,
                 ROLE_TENANT, BOOTSTRAP_USER),
            )

            for table in tables:
                for tid_var, ut_var, expected, row_label in truth_table:
                    set_tenant(cur, tid_var)
                    set_user_type(cur, ut_var)
                    cur.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(
                            sql.Identifier(table)
                        )
                    )
                    got = cur.fetchone()[0]
                    label = (
                        f"15.{table}.{row_label}: tenant_id="
                        f"{'A' if tid_var == TENANT_A else 'B' if tid_var == TENANT_B else 'unset'}"
                        f", user_type={ut_var or 'unset'} -> "
                        f"expect count={expected}"
                    )
                    if got == expected:
                        R.add(label, True)
                    else:
                        R.add(label, False,
                              RuntimeError(f"got count={got}"))
    except Exception as e:
        # If setup itself raised, every cell becomes a failure with
        # the same underlying error. Better than a single opaque skip.
        for table in tables:
            for _, _, _, row_label in truth_table:
                R.add(f"15.{table}.{row_label}", False, e)


def test_16_platform_can_insert_into_multi_tenant_tables(conn, db_schema, R):
    """Assertions 16.<table>: a PLATFORM session can INSERT into each
    of the multi-tenant tables.

    Step 3.0's WITH CHECK predicate matters: pre-3.0, a PLATFORM session
    (app.tenant_id = NULL, app.user_type = 'PLATFORM') could not INSERT
    rows where tenant_id is set, because the WITH CHECK predicate
    `tenant_id = NULLIF(NULL, '')::uuid` evaluated to UNKNOWN. Step 3.0
    adds the unconditional `OR app.user_type = 'PLATFORM'` branch, which
    short-circuits to TRUE and lets the INSERT through.

    Without this, test fixtures (Step 3.2 conftest factories) and seed
    scripts (Step 6.3) would have no way to insert tenant rows from the
    NOSUPERUSER NOBYPASSRLS application role. Inserting via a privileged
    role is exactly what the project rejected.

    Step 3.4.5 added tenant_module_access — same D-29 unconditional
    OR-clause, same INSERT-side property under PLATFORM context.

    Step 6.8.1 added two new tables:
      * platform_user_role_assignments — no RLS; INSERT trivially works
        from any session.
      * tenant_user_role_assignments — D-29 unconditional OR-branch;
        same property as the other multi-tenant tables.

    Tenant C is a fresh tenant so this assertion does not collide with
    any existing setup data inside its own force_rollback transaction.
    """
    targets = (
        ("tenants",                       "INSERT tenants"),
        ("tenant_users",                  "INSERT tenant_users"),
        ("org_nodes",                     "INSERT org_nodes"),
        ("stores",                        "INSERT stores"),
        ("tenant_module_access",          "INSERT tenant_module_access"),
        ("platform_user_role_assignments","INSERT platform_user_role_assignments"),
        ("tenant_user_role_assignments",  "INSERT tenant_user_role_assignments"),
    )
    # If the inserts chain (each depending on the previous), a single
    # try/except around the whole sequence would record only the first
    # failure. The structure below records each INSERT individually so
    # a regression on, say, `stores` is flagged even if `tenants` passes.
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            insert_bootstrap_user(cur)

            # PLATFORM session: app.tenant_id NULL, app.user_type PLATFORM.
            set_tenant(cur, None)
            set_user_type(cur, "PLATFORM")

            # tenants
            try:
                insert_tenant(cur, TENANT_C, "Tenant C")
                R.add("16.tenants: PLATFORM session can INSERT", True)
            except Exception as e:
                R.add("16.tenants: PLATFORM session can INSERT", False, e)
                # Without the parent tenant row, the dependent inserts
                # would all fail on FK; record them once with the same
                # cause and stop.
                for tname, _ in targets[1:]:
                    R.add(
                        f"16.{tname}: PLATFORM session can INSERT",
                        False,
                        RuntimeError(
                            "skipped: parent tenants INSERT failed"),
                    )
                return

            # tenant_users
            try:
                insert_tenant_user(
                    cur, TENANT_C_USER, TENANT_C,
                    "user-c@c.test", "User C",
                )
                R.add("16.tenant_users: PLATFORM session can INSERT", True)
            except Exception as e:
                R.add("16.tenant_users: PLATFORM session can INSERT",
                      False, e)

            # org_nodes
            try:
                insert_org_node(
                    cur, TENANT_C_ORG, TENANT_C, None, "TENANT",
                    "tenantc", "Tenant C Root", "tenantc",
                )
                R.add("16.org_nodes: PLATFORM session can INSERT", True)
            except Exception as e:
                R.add("16.org_nodes: PLATFORM session can INSERT",
                      False, e)
                R.add(
                    "16.stores: PLATFORM session can INSERT",
                    False,
                    RuntimeError(
                        "skipped: parent org_nodes INSERT failed"),
                )
                return

            # stores
            try:
                insert_store(
                    cur, TENANT_C_STORE, TENANT_C, TENANT_C_ORG, "Store C")
                R.add("16.stores: PLATFORM session can INSERT", True)
            except Exception as e:
                R.add("16.stores: PLATFORM session can INSERT", False, e)

            # tenant_module_access (Step 3.4.5). Audit-actor FK is
            # BOOTSTRAP_USER (a real platform_user already inserted at
            # the top of the test).
            try:
                insert_tenant_module_access(
                    cur, TENANT_C_TMA, TENANT_C, 'GOAL_CONSOLE')
                R.add(
                    "16.tenant_module_access: PLATFORM session can INSERT",
                    True,
                )
            except Exception as e:
                R.add(
                    "16.tenant_module_access: PLATFORM session can INSERT",
                    False, e,
                )

            # platform_user_role_assignments (Step 6.8.1). No RLS;
            # PLATFORM session inserts trivially. The audience-check
            # trigger requires role.audience='PLATFORM' — insert a
            # PLATFORM-audience role first.
            try:
                insert_role(
                    cur, ROLE_PLATFORM, "Super Admin", "SUPER_ADMIN", "PLATFORM",
                )
                cur.execute(
                    """
                    INSERT INTO platform_user_role_assignments (
                        id, platform_user_id, role_id, status,
                        granted_by_user_id, granted_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-00000016c111',
                        %s, %s, 'ACTIVE', %s, 'PLATFORM'
                    )
                    """,
                    (BOOTSTRAP_USER, ROLE_PLATFORM, BOOTSTRAP_USER),
                )
                R.add(
                    "16.platform_user_role_assignments: PLATFORM session can INSERT",
                    True,
                )
            except Exception as e:
                R.add(
                    "16.platform_user_role_assignments: PLATFORM session can INSERT",
                    False, e,
                )

            # tenant_user_role_assignments (Step 6.8.1). RLS+FORCE
            # with unconditional OR-branch; PLATFORM session writes
            # under the OR-branch without impersonation. Composite
            # FKs to (tenant_id, tenant_user_id) -> tenant_users and
            # (tenant_id, org_node_id) -> org_nodes validate same-
            # tenant integrity at INSERT time.
            try:
                insert_role(
                    cur, ROLE_TENANT, "Owner", "OWNER", "TENANT",
                )
                cur.execute(
                    """
                    INSERT INTO tenant_user_role_assignments (
                        id, tenant_user_id, tenant_id, org_node_id,
                        role_id, status,
                        granted_by_user_id, granted_by_user_type
                    )
                    VALUES (
                        '00000000-0000-0000-0000-00000016c222',
                        %s, %s, %s, %s, 'ACTIVE',
                        %s, 'PLATFORM'
                    )
                    """,
                    (TENANT_C_USER, TENANT_C, TENANT_C_ORG, ROLE_TENANT,
                     BOOTSTRAP_USER),
                )
                R.add(
                    "16.tenant_user_role_assignments: PLATFORM session can INSERT",
                    True,
                )
            except Exception as e:
                R.add(
                    "16.tenant_user_role_assignments: PLATFORM session can INSERT",
                    False, e,
                )
    except Exception as e:
        # Top-level failure: bootstrap, search_path, or context setup blew up.
        for tname, _ in targets:
            R.add(f"16.{tname}: PLATFORM session can INSERT", False, e)


def test_14_uuidv7_default(conn, db_schema, R):
    """Assertion 14: INSERT into platform_users without id triggers uuidv7() DEFAULT.

    Verifies the v7 version nibble (13th hex char) and the variant
    nibble (19th hex char). This is the only assertion that exercises
    the uuidv7() DEFAULT we landed in Step C; without it, the smoke
    test has zero coverage on the v7 work.
    """
    label = "14: uuidv7() DEFAULT generates v7 UUID with correct version+variant"
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.cursor()
            set_search_path(cur, db_schema)
            cur.execute(
                """
                INSERT INTO platform_users (
                    email, full_name, status,
                    created_by_user_id, updated_by_user_id, suspended_by_user_id
                )
                VALUES (
                    'uuidv7-test@ithina.test', 'UUIDv7 Test', 'INVITED',
                    NULL, NULL, NULL
                )
                RETURNING id
                """
            )
            generated_id = cur.fetchone()[0]
            uuid_text = str(generated_id)
            version_char = uuid_text[14]   # 13th 0-indexed hex char
            variant_char = uuid_text[19]   # 19th 0-indexed (after 3 hyphens)

            errs = []
            if version_char != '7':
                errs.append(f"version_char={version_char!r} (expected '7')")
            if variant_char not in {'8', '9', 'a', 'b'}:
                errs.append(
                    f"variant_char={variant_char!r} (expected one of 8, 9, a, b)"
                )

            if errs:
                R.add(label + f" (got {uuid_text})", False,
                      RuntimeError("; ".join(errs)))
            else:
                R.add(label + f" ({uuid_text})", True)
    except Exception as e:
        R.add(label, False, e)


# ============================================================================
# Main
# ============================================================================

def main():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    DB_SCHEMA = os.environ.get("DB_SCHEMA")
    if not DATABASE_URL or not DB_SCHEMA:
        print(
            "ERROR: DATABASE_URL and DB_SCHEMA must both be set in the environment.",
            file=sys.stderr,
        )
        sys.exit(2)

    # The .env keeps DATABASE_URL in SQLAlchemy form (`postgresql+psycopg://...`).
    # psycopg3 (libpq under the hood) rejects the `+psycopg` driver suffix on
    # connect, so strip it. This mirrors scripts/check_setup.sh:212. The prompt
    # anticipated this fallback; we do not silently mangle the URL otherwise.
    if DATABASE_URL.startswith("postgresql+psycopg://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgresql+psycopg://"):]

    R = Results()

    # Test order is logical. Each test is independent (own transaction,
    # own setup, own rollback). No shared state between tests.
    tests = [
        test_1_tenant_a_select,
        test_2_tenant_b_select,
        test_3_default_deny,
        test_4_cross_tenant_insert_rejected,
        test_5_stores_composite_fk,
        test_6_org_nodes_parent_composite_fk,
        test_7_assignment_composite_fk_same_tenant,
        test_8_tenants_terminated_consistency,
        test_9_platform_user_suspended_consistency,
        test_10_currency_check,
        test_11_role_assignment_split_invariants,
        test_12_meta_multi_tenant_tables_have_rls,
        test_13_bootstrap_user_pattern,
        test_14_uuidv7_default,
        test_15_multi_tenant_or_clause_truth_tables,
        test_16_platform_can_insert_into_multi_tenant_tables,
    ]

    # Tests that need to open their own fresh connection (because their
    # assertion depends on a never-SET app.tenant_id GUC, which is only
    # achievable on a connection that has never SET that GUC).
    needs_fresh_connection = {test_3_default_deny}

    conn = psycopg.connect(DATABASE_URL, autocommit=False)
    try:
        for fn in tests:
            if fn in needs_fresh_connection:
                fn(conn, DB_SCHEMA, R, DATABASE_URL)
            else:
                fn(conn, DB_SCHEMA, R)
    finally:
        conn.close()

    print()
    print(
        f"Total: {R.total()} assertions, "
        f"{R.passed_count()} passed, {R.failed_count()} failed"
    )

    if R.notes:
        print()
        print("Notes / architectural findings:")
        for n in R.notes:
            print(f"  - {n}")

    sys.exit(1 if R.failed_count() > 0 else 0)


if __name__ == "__main__":
    main()
