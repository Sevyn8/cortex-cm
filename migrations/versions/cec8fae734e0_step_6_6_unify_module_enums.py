"""step_6_6_unify_module_enums

Revision ID: cec8fae734e0
Revises: 22ccfb193cff
Create Date: 2026-05-06

Step 6.6 — Path B (unification): retire ``module_enum``, re-point
``permissions.module`` at ``module_code_enum``, consolidate the two
``lookups.list_name`` entries into a single canonical reference.

Closes the **MODULES-EXT** forward note from Step 6.1's "Known
follow-ups (RBAC)". Path A (additive ``ALTER TYPE module_enum ADD
VALUE``) is superseded — the two-enum duplication is the root cause
of the drift between Step 3.4.5 and Step 6.1, and adding back values
would only restore symmetry without fixing the underlying duplication.

Forward-only — irreversible. ``downgrade()`` raises NotImplementedError
per the project's irreversible-cleanup convention (matching Step 6.1's
``90cd038ae618``). Recreating ``module_enum`` and reverting
``permissions.module`` would lose any post-step permissions targeting
ROOS or GOAL_CONSOLE; restore from backup if rollback is required.

Safety analysis:

  - Every value in the narrow ``module_enum`` (4 values: ADMIN,
    PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT — post
    Step 6.1's narrowing) is also present in the wider
    ``module_code_enum`` (6 values: same 4 + ROOS + GOAL_CONSOLE).
    The USING text-cast cannot encounter a value that fails the
    target enum's validation.

  - ``ALTER COLUMN TYPE`` automatically rebuilds dependent indexes
    and constraints. The ``uq_permissions_tuple`` UNIQUE index
    over ``(module, resource, action, scope)`` is rebuilt as part
    of the column re-typing.

  - ``permissions`` is small (23 rows post Step 6.1 cleanup); the
    column rewrite is sub-second.

  - ``DROP TYPE module_enum`` succeeds because nothing else
    references it post the column re-type. Pre-flight verified
    via ``pg_attribute`` that ``permissions.module`` is the only
    consumer.

The migration emits three statements (not combined — Postgres rejects
some combinations of ALTER TABLE + DROP TYPE in a single statement):

  1. ``ALTER TABLE permissions ALTER COLUMN module TYPE
     module_code_enum USING module::text::module_code_enum``
  2. ``DROP TYPE module_enum``
  3. ``DELETE FROM lookups WHERE list_name = 'module'`` (with
     defensive row-count assertion — see step 3 in upgrade)

Schema qualification: unqualified table names per env.py's
search_path SET inside the alembic transaction (Step 3.0+ precedent).

Defensive lookups DELETE: the migration asserts the deleted codes
are exactly ``{ADMIN, PRICING_OS, PERISHABLES_ASSISTANT,
PROMOTIONS_ASSISTANT}``. If a future hand-edit added rows under
``list_name='module'`` between Step 6.1's seed and this migration's
run, the assertion catches it loudly rather than silently deleting
unexpected data.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "cec8fae734e0"
down_revision: Union[str, Sequence[str], None] = "22ccfb193cff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EXPECTED_DELETED_LOOKUP_CODES = sorted(
    [
        "ADMIN",
        "PRICING_OS",
        "PERISHABLES_ASSISTANT",
        "PROMOTIONS_ASSISTANT",
    ]
)


def upgrade() -> None:
    """Re-type permissions.module; drop module_enum; delete redundant lookups."""

    bind = op.get_bind()

    # 1. Re-type the column. USING text-cast is safe because every
    #    value in module_enum (4 vals) is also in module_code_enum (6).
    #    ALTER COLUMN TYPE rebuilds the uq_permissions_tuple UNIQUE
    #    index automatically.
    op.execute(
        """
        ALTER TABLE permissions
            ALTER COLUMN module TYPE module_code_enum
                USING module::text::module_code_enum
        """
    )

    # 2. Drop the now-orphaned module_enum type.
    op.execute("DROP TYPE module_enum")

    # 3. Delete the redundant lookups rows. list_name='module_code'
    #    already covers the same 4 display labels (with 2 extras for
    #    ROOS and GOAL_CONSOLE). Defensive row-count assertion: the
    #    deleted set must be exactly {ADMIN, PRICING_OS,
    #    PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT}; any deviation
    #    is a sign that someone hand-edited the lookups table since
    #    Step 6.1's seed and the migration must surface it loudly.
    result = bind.execute(
        sa.text(
            "DELETE FROM lookups "
            "WHERE list_name = 'module' "
            "RETURNING code"
        )
    )
    deleted_codes = sorted(row[0] for row in result.fetchall())
    if deleted_codes != _EXPECTED_DELETED_LOOKUP_CODES:
        raise RuntimeError(
            "Step 6.6 migration: lookups DELETE removed unexpected "
            f"rows. Expected codes {_EXPECTED_DELETED_LOOKUP_CODES}; "
            f"actually deleted {deleted_codes}. Investigate before "
            "proceeding — there may be hand-edited rows under "
            "list_name='module' that need explicit handling."
        )


def downgrade() -> None:
    raise NotImplementedError(
        "step_6_6_unify_module_enums is forward-only. Recreating "
        "module_enum and reverting permissions.module would lose any "
        "post-step permissions targeting ROOS or GOAL_CONSOLE. "
        "Restore from backup if rollback is required. The decision "
        "to make this irreversible mirrors Step 6.1's "
        "90cd038ae618 (rbac_enum_cleanup) which also drops enum "
        "values that cannot be reconstructed without an external "
        "data source."
    )
