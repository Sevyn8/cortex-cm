"""Verify Cloud SQL schema after Alembic bring-up. Step 4.1.

Self-contained Python script using psycopg3 sync API. Connects via
DATABASE_URL + DB_SCHEMA env vars and prints three blocks for human
audit (output goes to Cloud Logging when run as a Cloud Run Job):

  1. Tables in the configured schema (expected: 13 — the 12 application
     tables for v0 plus `alembic_version` which Alembic creates itself.
     The 12 are the 10 from raw_ddl plus tenant_module_access from
     Step 3.4.5 plus the two role-assignment tables from Step 6.8.1
     — platform_user_role_assignments and tenant_user_role_assignments
     — replacing user_role_assignments which Step 6.8.1 dropped;
     audit_logs lands at Step 6.2 and is not in scope here).

  2. Tables with forcerowsecurity = true (expected: 6 multi-tenant
     tables — tenants, tenant_users, org_nodes, stores,
     tenant_user_role_assignments, tenant_module_access). Note:
     platform_user_role_assignments has no RLS by design — it's
     platform-global (mirrors platform_users' posture per D-12).

  3. Alembic head revision from the alembic_version table (expected
     to match local head: 0644a4186e48 as of Step 3.6 lookups seed).

The script exits 0 on success and 1 on any DB error. Acceptance is
determined by reading the printed output, not by the exit code alone:
the counts above must match.

Usage (local):
    uv run python scripts/verify_cloud_schema.py

Usage (Cloud Run Job):
    gcloud run jobs update <job> \\
      --command=python --args=/app/scripts/verify_cloud_schema.py
    gcloud run jobs execute <job> --wait

Env required:
    DATABASE_URL  psycopg-compatible connection string
    DB_SCHEMA     Postgres schema name (e.g., 'core')
"""

import os
import sys

import psycopg
from psycopg import sql


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    db_schema = os.environ.get("DB_SCHEMA")
    if not database_url or not db_schema:
        print(
            "ERROR: DATABASE_URL and DB_SCHEMA must both be set in the environment.",
            file=sys.stderr,
        )
        return 1

    # Strip the SQLAlchemy +psycopg suffix that .env / Settings carry.
    # libpq under psycopg3 rejects it; smoke_test.py does the same.
    if database_url.startswith("postgresql+psycopg://"):
        database_url = "postgresql://" + database_url[len("postgresql+psycopg://"):]

    print("=" * 72)
    print(f"Cloud SQL schema verification — schema = {db_schema!r}")
    print("=" * 72)

    with psycopg.connect(database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SET LOCAL search_path TO {}, public").format(
                    sql.Identifier(db_schema)
                )
            )

            # Block 1: tables in schema
            print()
            print(f"--- Block 1: tables in schema {db_schema!r} ---")
            cur.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = %s
                ORDER BY tablename
                """,
                (db_schema,),
            )
            tables = [r[0] for r in cur.fetchall()]
            print(f"count: {len(tables)}")
            for t in tables:
                print(f"  - {t}")

            # Block 2: tables with FORCE RLS on
            print()
            print("--- Block 2: tables with forcerowsecurity = true ---")
            cur.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relkind = 'r'
                  AND c.relforcerowsecurity = true
                ORDER BY c.relname
                """,
                (db_schema,),
            )
            force_rls_tables = [r[0] for r in cur.fetchall()]
            print(f"count: {len(force_rls_tables)}")
            for t in force_rls_tables:
                print(f"  - {t}")

            # Block 3: Alembic head revision
            print()
            print("--- Block 3: alembic_version ---")
            cur.execute("SELECT version_num FROM alembic_version")
            rows = cur.fetchall()
            if not rows:
                print("ERROR: alembic_version table empty")
                return 1
            for r in rows:
                print(f"  head: {r[0]}")

        # Read-only verification; rollback to leave no side effects.
        conn.rollback()

    print()
    print("=" * 72)
    print("verify_cloud_schema: OK")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
