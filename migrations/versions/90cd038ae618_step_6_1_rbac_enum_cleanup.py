"""step_6_1_rbac_enum_cleanup

Revision ID: 90cd038ae618
Revises: 0644a4186e48
Create Date: 2026-05-05

Step 6.1 (file 1 of 2): narrow two RBAC enums to the locked product
vocabulary, and delete the seed permission rows that referenced the
dropped values.

  - module_enum:           drops ROOS, GOAL_CONSOLE  (4 values remain)
  - permission_scope_enum: drops REGION              (3 values remain)

resource_enum and action_enum already match the locked vocabulary in
the DDL; not touched here.

Postgres has no ALTER TYPE DROP VALUE, so the canonical narrowing dance
is rename-recreate: rename old enum to *_legacy, CREATE TYPE with the
locked values, ALTER COLUMN TYPE via USING cast through text, DROP TYPE
the legacy. Anything still pointing at the old type prevents the DROP,
which is exactly the safety we want.

Forward-only on data. Live state at this point holds 1 permission row
(scope='REGION', code='PRICING_OS.MARKDOWNS.APPROVE.REGION') plus 4
role_permissions referencing it. The upgrade DELETEs them; the
downgrade re-creates the legacy enum types but cannot reconstruct the
deleted permission rows from any source other than the seed Excel.
Documenting irreversibility loudly via NotImplementedError in
downgrade() per the project's convention for such cleanups.

ROOS and GOAL_CONSOLE may be added back as additive ALTER TYPE
migrations later if/when those modules ship (see "Known follow-ups
(RBAC)" in BUILD_PLAN's Step 6.1, FN: MODULES-EXT).

Schema qualification: unqualified table names per env.py's
search_path SET inside the alembic transaction (Step 3.0+ precedent).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "90cd038ae618"
down_revision: Union[str, Sequence[str], None] = "0644a4186e48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop legacy permission rows; narrow module_enum and permission_scope_enum."""

    # 1. Drop legacy seed rows BEFORE altering enum types. role_permissions
    #    has FK RESTRICT to permissions, so junction rows go first.
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE module::text IN ('ROOS', 'GOAL_CONSOLE')
               OR scope::text  = 'REGION'
        )
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE module::text IN ('ROOS', 'GOAL_CONSOLE')
           OR scope::text  = 'REGION'
        """
    )

    # 2. Rename legacy enum types out of the way.
    op.execute("ALTER TYPE module_enum RENAME TO module_enum_legacy")
    op.execute(
        "ALTER TYPE permission_scope_enum RENAME TO permission_scope_enum_legacy"
    )

    # 3. Create new enums with the locked vocabulary.
    op.execute(
        """
        CREATE TYPE module_enum AS ENUM (
            'ADMIN',
            'PRICING_OS',
            'PERISHABLES_ASSISTANT',
            'PROMOTIONS_ASSISTANT'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE permission_scope_enum AS ENUM (
            'GLOBAL',
            'TENANT',
            'STORE'
        )
        """
    )

    # 4. ALTER COLUMN TYPEs via USING cast through text. The legacy
    #    column types were *_legacy after step 2; flip them onto the new
    #    types in one ALTER TABLE so the table is rewritten only once.
    op.execute(
        """
        ALTER TABLE permissions
            ALTER COLUMN module TYPE module_enum
                USING module::text::module_enum,
            ALTER COLUMN scope  TYPE permission_scope_enum
                USING scope::text::permission_scope_enum
        """
    )

    # 5. Drop legacy enum types now that nothing references them.
    op.execute("DROP TYPE module_enum_legacy")
    op.execute("DROP TYPE permission_scope_enum_legacy")


def downgrade() -> None:
    """Forward-only: deleted permission rows cannot be reconstructed.

    Re-widening the enums (adding ROOS / GOAL_CONSOLE / REGION back)
    would be reversible, but the row deletion in step (1) of upgrade()
    is not — there is no audit log of what was removed. Refusing to
    pretend a downgrade is clean is the correct posture for a
    structural cleanup of this shape.
    """
    raise NotImplementedError(
        "step_6_1_rbac_enum_cleanup is forward-only: legacy permission "
        "rows (module='ROOS'/'GOAL_CONSOLE' or scope='REGION') were "
        "deleted in upgrade(). Restoring them requires re-running the "
        "seed loader against the Excel that still carries them, not "
        "Alembic downgrade."
    )
