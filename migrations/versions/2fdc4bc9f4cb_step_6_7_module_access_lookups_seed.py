"""step_6_7_module_access_lookups_seed

Revision ID: 2fdc4bc9f4cb
Revises: cec8fae734e0
Create Date: 2026-05-06

Step 6.7 supports the new label-handling convention: every enum-coded
field in a Step-6.7+ response carries a sibling ``<field>_label`` resolved
server-side via JOIN against ``lookups``. The Module Access read endpoints
need three label sets:

  list_name      Source-of-truth      Status pre-step                Action
  -------------  -------------------  -----------------------------  ------------------
  module_code    seeded Step 3.4.5    6 rows with screenshot-out-    UPDATE display_order
                                      of-order display_order         to match the locked
                                                                     screenshot order.
  tenant_tier    seeded Step 3.6      4 rows, vocabulary matches     INSERT (no-op via
                                      locked spec.                   ON CONFLICT).
  tenant_status  seeded Step 3.6      5 rows, vocabulary matches     INSERT (no-op via
                                      locked spec.                   ON CONFLICT).

The two INSERT blocks are kept (idempotent via ON CONFLICT) for
fresh-DB bring-up safety: a clean Cloud SQL whose state somehow lacks
these rows still ends up consistent after this migration runs.

The ``module_code`` UPDATE block is the load-bearing change. Pre-step,
``display_order`` had GOAL_CONSOLE at position 5; the locked screenshot
sequence (ROOS → Goal Console → Pricing OS → Perishables → Promotions →
Admin) places it at position 2. ``/module-access/modules`` and
``/module-access/matrix`` order rows by ``lookups.display_order`` (per
Step 6.6's sort-stability decision), so the UPDATE is the single source
of truth for the rendered order.

Idempotency posture:
  - INSERTs use ``ON CONFLICT (list_name, code) DO NOTHING`` (the
    pattern Step 6.1 / Step 3.6 established).
  - The UPDATE is naturally idempotent — running it twice produces the
    same final state.

Forward-only per the project's irreversible-cleanup convention. Mirrors
Step 6.6's ``cec8fae734e0`` and Step 6.1's ``90cd038ae618``: the
display_order UPDATE clobbers the prior values; reconstructing the
exact prior order requires an external data source. Restore from
backup if rollback is required.

Schema qualification: unqualified ``lookups`` per env.py's search_path
SET inside the alembic transaction (Step 3.6 / Step 6.1 precedent).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2fdc4bc9f4cb'
down_revision: Union[str, Sequence[str], None] = 'cec8fae734e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Reorder ``module_code`` display_order; idempotent INSERTs for
    ``tenant_tier`` and ``tenant_status``."""

    # 1. Reorder module_code rows to match the locked screenshot
    #    sequence. Pre-step: GOAL_CONSOLE was at display_order=5;
    #    post-step it's at 2. The CASE expression sets every code
    #    explicitly so the result is deterministic regardless of the
    #    pre-step values.
    op.execute(
        """
        UPDATE lookups SET display_order = CASE code
            WHEN 'ROOS'                  THEN 1
            WHEN 'GOAL_CONSOLE'          THEN 2
            WHEN 'PRICING_OS'            THEN 3
            WHEN 'PERISHABLES_ASSISTANT' THEN 4
            WHEN 'PROMOTIONS_ASSISTANT'  THEN 5
            WHEN 'ADMIN'                 THEN 6
        END
        WHERE list_name = 'module_code'
          AND code IN (
            'ROOS', 'GOAL_CONSOLE', 'PRICING_OS',
            'PERISHABLES_ASSISTANT', 'PROMOTIONS_ASSISTANT', 'ADMIN'
        )
        """
    )

    # 2. Idempotent INSERTs for tenant_tier (4) and tenant_status (5).
    #    Step 3.6 already seeded these; the ON CONFLICT clause makes
    #    re-insertion a no-op and guards against any future fresh-DB
    #    state where Step 3.6's seed didn't run.
    op.execute(
        """
        INSERT INTO lookups (list_name, code, display_name, display_order, is_active)
        VALUES
            -- tenant_tier (4)
            ('tenant_tier', 'ENTERPRISE',   'Enterprise',   1, TRUE),
            ('tenant_tier', 'MID_MARKET',   'Mid-Market',   2, TRUE),
            ('tenant_tier', 'SMB',          'SMB',          3, TRUE),
            ('tenant_tier', 'SINGLE_STORE', 'Single Store', 4, TRUE),

            -- tenant_status (5) — lifecycle order
            ('tenant_status', 'ONBOARDING', 'Onboarding', 1, TRUE),
            ('tenant_status', 'TRIAL',      'Trial',      2, TRUE),
            ('tenant_status', 'ACTIVE',     'Active',     3, TRUE),
            ('tenant_status', 'SUSPENDED',  'Suspended',  4, TRUE),
            ('tenant_status', 'TERMINATED', 'Terminated', 5, TRUE)
        ON CONFLICT (list_name, code) DO NOTHING
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "step_6_7_module_access_lookups_seed is forward-only. The "
        "module_code display_order UPDATE clobbers the prior values; "
        "reconstructing the exact prior order would require an external "
        "data source. Restore from backup if rollback is required. "
        "Mirrors Step 6.6's cec8fae734e0 and Step 6.1's 90cd038ae618."
    )
