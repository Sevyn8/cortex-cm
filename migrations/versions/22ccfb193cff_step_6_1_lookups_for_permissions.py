"""step_6_1_lookups_for_permissions

Revision ID: 22ccfb193cff
Revises: 90cd038ae618
Create Date: 2026-05-05

Step 6.1 (file 2 of 2): seed display labels for the four
permission-tuple slots into ``lookups``. These back the ``*_label``
fields on the ``GET /api/v1/permission-matrix`` endpoint.

  list_name           rows
  ------------------  ----
  module                 4
  resource              12
  permission_action      6
  permission_scope       3
                       ----
                        25 rows total

Idempotent via ``ON CONFLICT (list_name, code) DO NOTHING`` so the
migration can be re-run safely (e.g., after a partial failure or
when verifying a fresh DB).

The list_name choices are deliberate:

  - ``module`` and ``resource`` mirror the column names on the
    ``permissions`` table; the matrix endpoint joins
    ``permissions.module`` against ``lookups.code`` where
    ``list_name='module'`` (likewise ``resource``).

  - ``permission_action`` and ``permission_scope`` are NOT named
    ``action`` and ``scope`` because those would collide with future
    list categories from other resource families (audit log scope,
    etc.). The compound names keep these label sets unambiguously
    bound to the permissions UI.

Display names follow Step 3.6's convention (title case for natural
words; ALL CAPS only when the original is an acronym).
``display_order`` matches the canonical order the matrix UI renders.

Schema qualification: unqualified table names per env.py's search_path
SET inside the alembic transaction (Step 3.6 precedent).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "22ccfb193cff"
down_revision: Union[str, Sequence[str], None] = "90cd038ae618"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Seed 25 rows into the lookups table for permission display labels."""
    op.execute(
        """
        INSERT INTO lookups (list_name, code, display_name, display_order, is_active)
        VALUES
            -- module (4)
            ('module', 'ADMIN',                 'Admin',                 1, TRUE),
            ('module', 'PRICING_OS',            'Pricing OS',            2, TRUE),
            ('module', 'PERISHABLES_ASSISTANT', 'Perishables Assistant', 3, TRUE),
            ('module', 'PROMOTIONS_ASSISTANT',  'Promotions Assistant',  4, TRUE),

            -- resource (12)
            ('resource', 'PRICING_RULES',     'Pricing Rules',     1, TRUE),
            ('resource', 'MARKDOWNS',         'Markdowns',         2, TRUE),
            ('resource', 'WASTE_LOG',         'Waste Log',         3, TRUE),
            ('resource', 'USERS',             'Users',             4, TRUE),
            ('resource', 'AUDIT_LOG',         'Audit Log',         5, TRUE),
            ('resource', 'EXPIRING_ITEMS',    'Expiring Items',    6, TRUE),
            ('resource', 'CAMPAIGNS',         'Campaigns',         7, TRUE),
            ('resource', 'DONATION_ROUTING',  'Donation Routing',  8, TRUE),
            ('resource', 'ROLES',             'Roles',             9, TRUE),
            ('resource', 'TENANTS',           'Tenants',          10, TRUE),
            ('resource', 'STORES',            'Stores',           11, TRUE),
            ('resource', 'ORG_NODES',         'Org Nodes',        12, TRUE),

            -- permission_action (6)
            ('permission_action', 'VIEW',      'View',      1, TRUE),
            ('permission_action', 'CONFIGURE', 'Configure', 2, TRUE),
            ('permission_action', 'AUDIT',     'Audit',     3, TRUE),
            ('permission_action', 'APPROVE',   'Approve',   4, TRUE),
            ('permission_action', 'OVERRIDE',  'Override',  5, TRUE),
            ('permission_action', 'EXECUTE',   'Execute',   6, TRUE),

            -- permission_scope (3)
            ('permission_scope', 'GLOBAL', 'Global', 1, TRUE),
            ('permission_scope', 'TENANT', 'Tenant', 2, TRUE),
            ('permission_scope', 'STORE',  'Store',  3, TRUE)
        ON CONFLICT (list_name, code) DO NOTHING
        """
    )


def downgrade() -> None:
    """Remove only the 25 rows this migration inserted.

    Explicit ``(list_name, code)`` pairs (rather than a looser
    ``WHERE list_name IN (...)``) preserve downgrade safety even if
    someone adds new lookup values into the same list_names later.
    """
    op.execute(
        """
        DELETE FROM lookups
        WHERE (list_name, code) IN (
            ('module', 'ADMIN'),
            ('module', 'PRICING_OS'),
            ('module', 'PERISHABLES_ASSISTANT'),
            ('module', 'PROMOTIONS_ASSISTANT'),
            ('resource', 'PRICING_RULES'),
            ('resource', 'MARKDOWNS'),
            ('resource', 'WASTE_LOG'),
            ('resource', 'USERS'),
            ('resource', 'AUDIT_LOG'),
            ('resource', 'EXPIRING_ITEMS'),
            ('resource', 'CAMPAIGNS'),
            ('resource', 'DONATION_ROUTING'),
            ('resource', 'ROLES'),
            ('resource', 'TENANTS'),
            ('resource', 'STORES'),
            ('resource', 'ORG_NODES'),
            ('permission_action', 'VIEW'),
            ('permission_action', 'CONFIGURE'),
            ('permission_action', 'AUDIT'),
            ('permission_action', 'APPROVE'),
            ('permission_action', 'OVERRIDE'),
            ('permission_action', 'EXECUTE'),
            ('permission_scope', 'GLOBAL'),
            ('permission_scope', 'TENANT'),
            ('permission_scope', 'STORE')
        )
        """
    )
