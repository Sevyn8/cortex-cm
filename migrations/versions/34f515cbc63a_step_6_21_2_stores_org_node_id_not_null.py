"""Step 6.21.2: stores.org_node_id NOT NULL

Revision ID: 34f515cbc63a
Revises: c530346032dd
Create Date: 2026-05-21 14:52:54.831881

Tightens ``core.stores.org_node_id`` from nullable to NOT NULL. Closes
the schema invariant promised by Step 6.21.2's atomic-pair write
architecture: every store row has a paired STORE-type org_node row,
linked via ``stores.org_node_id`` (1:1, enforced by the pre-existing
partial unique index ``uq_stores_org_node_id``).

Pre-migration cleanup of dev orphans (NULL ``org_node_id`` rows in
Buc-ee's) is operator workflow at Phase 6 deploy. Local DB has zero
NULL rows per pre-flight Check #12.

The pre-existing partial UNIQUE index
``uq_stores_org_node_id ... WHERE org_node_id IS NOT NULL`` becomes
equivalent to a total UNIQUE constraint once the column is NOT NULL.
The partial form is preserved (no DDL change to the index) for
migration simplicity.

Schema-qualified per CSD-03; ``current_schema()`` capture mirrors
``a0982a86985b``'s posture.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '34f515cbc63a'
down_revision: Union[str, Sequence[str], None] = 'c530346032dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Tighten stores.org_node_id to NOT NULL."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
    op.execute(
        sa.text(
            f"ALTER TABLE {schema}.stores "
            "ALTER COLUMN org_node_id SET NOT NULL"
        )
    )


def downgrade() -> None:
    """Restore stores.org_node_id to nullable."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
    op.execute(
        sa.text(
            f"ALTER TABLE {schema}.stores "
            "ALTER COLUMN org_node_id DROP NOT NULL"
        )
    )
