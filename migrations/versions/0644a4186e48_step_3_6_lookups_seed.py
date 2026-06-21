"""step_3_6_lookups_seed

Revision ID: 0644a4186e48
Revises: cd2a02e452ae
Create Date: 2026-05-03 14:15:57.398335

Seeds 17 rows into ``lookups`` for the four PG-enum-backed categories
the tenants UI needs for filter dropdowns:

  - tenant_tier      (4 rows)
  - tenant_region    (2 rows)
  - tenant_status    (5 rows)
  - tenant_industry  (6 rows)

``module_code`` was already seeded by ``cd2a02e452ae`` (Step 3.4.5);
not re-seeded here.

Display names follow the convention used at Step 3.4.5: title case
for natural words, ALL CAPS only when the original is an acronym
(SMB, EU). ``display_order`` is sequential within each list_name.

Country deliberately NOT seeded. The dev seed Excel's
``tenants.country`` column carries mixed-case literals (``Canada``,
``France``, ``Poland``) which violate the lookups
``ck_lookups_code_format`` CHECK (``^[A-Z][A-Z0-9_]*$``). Aligning
either side requires a deliberate design decision (ISO 3166 codes,
UPPER-cased literals + frontend normalisation, or a country-aware
re-seed of tenants); deferred. Frontend hardcodes the 5 country
values for first integration. The endpoint remains country-tolerant
— ``?lists=country`` returns an empty array (predictable-empty shape
per the prompt's contract); future country lookup data populates
without endpoint changes.

Schema qualification: unqualified table names per the established
precedent (env.py sets search_path inside the alembic transaction;
Step 3.4.5's migration uses the same form).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0644a4186e48"
down_revision: Union[str, Sequence[str], None] = "cd2a02e452ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Seed 17 rows into the lookups table (country deferred)."""
    op.execute(
        """
        INSERT INTO lookups (list_name, code, display_name, display_order, is_active)
        VALUES
            -- tenant_tier (4)
            ('tenant_tier', 'ENTERPRISE',   'Enterprise',    1, TRUE),
            ('tenant_tier', 'MID_MARKET',   'Mid-Market',    2, TRUE),
            ('tenant_tier', 'SMB',          'SMB',           3, TRUE),
            ('tenant_tier', 'SINGLE_STORE', 'Single Store',  4, TRUE),

            -- tenant_region (2)
            ('tenant_region', 'US', 'United States',  1, TRUE),
            ('tenant_region', 'EU', 'European Union', 2, TRUE),

            -- tenant_status (5)
            ('tenant_status', 'ONBOARDING', 'Onboarding', 1, TRUE),
            ('tenant_status', 'TRIAL',      'Trial',      2, TRUE),
            ('tenant_status', 'ACTIVE',     'Active',     3, TRUE),
            ('tenant_status', 'SUSPENDED',  'Suspended',  4, TRUE),
            ('tenant_status', 'TERMINATED', 'Terminated', 5, TRUE),

            -- tenant_industry (6)
            ('tenant_industry', 'CONVENIENCE_FUEL',   'Convenience & Fuel', 1, TRUE),
            ('tenant_industry', 'CONVENIENCE',        'Convenience',        2, TRUE),
            ('tenant_industry', 'GROCERY',            'Grocery',            3, TRUE),
            ('tenant_industry', 'HYPERMART',          'Hypermart',          4, TRUE),
            ('tenant_industry', 'SPECIALITY_GROCERY', 'Speciality Grocery', 5, TRUE),
            ('tenant_industry', 'ORGANIC_GROCERY',    'Organic Grocery',    6, TRUE)
        """
    )


def downgrade() -> None:
    """Remove only the 17 rows this migration inserted.

    Looser ``DELETE WHERE list_name IN (...)`` would also delete rows
    added later in the same list_names by other migrations or manual
    edits. Explicit ``(list_name, code)`` pairs preserve downgrade
    safety even if someone adds new lookup values before downgrading.
    """
    op.execute(
        """
        DELETE FROM lookups
        WHERE (list_name, code) IN (
            ('tenant_tier', 'ENTERPRISE'),
            ('tenant_tier', 'MID_MARKET'),
            ('tenant_tier', 'SMB'),
            ('tenant_tier', 'SINGLE_STORE'),
            ('tenant_region', 'US'),
            ('tenant_region', 'EU'),
            ('tenant_status', 'ONBOARDING'),
            ('tenant_status', 'TRIAL'),
            ('tenant_status', 'ACTIVE'),
            ('tenant_status', 'SUSPENDED'),
            ('tenant_status', 'TERMINATED'),
            ('tenant_industry', 'CONVENIENCE_FUEL'),
            ('tenant_industry', 'CONVENIENCE'),
            ('tenant_industry', 'GROCERY'),
            ('tenant_industry', 'HYPERMART'),
            ('tenant_industry', 'SPECIALITY_GROCERY'),
            ('tenant_industry', 'ORGANIC_GROCERY')
        )
        """
    )
