"""add fundamentals quality ratios

Revision ID: 73d7d835369e
Revises: 0c3700b29aa2
Create Date: 2026-09-17 11:48:55.826422

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '73d7d835369e'
down_revision: Union[str, None] = '0c3700b29aa2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Raw manual-entry columns, same policy as every other fundamentals field
    # (no API/job writes them -- entered by hand alongside pe/pb/roce/etc.).
    # Numeric(20, 4) matches market_cap's sizing: these are aggregate Rs-crore
    # figures, not per-share values like eps_diluted/fcf_per_share.
    op.add_column('fundamentals', sa.Column('net_profit', sa.Numeric(20, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('equity', sa.Numeric(20, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('receivables', sa.Numeric(20, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('payables', sa.Numeric(20, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('inventory', sa.Numeric(20, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('sales', sa.Numeric(20, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('cogs', sa.Numeric(20, 4), nullable=True))

    # Derived, DB-enforced (same fcf_conversion precedent as the BS-GARP
    # migration) so these can never drift from the raw inputs they're
    # entered alongside. NULLIF guards every denominator against /0.
    op.add_column(
        'fundamentals',
        sa.Column('roe', sa.Numeric(9, 4), sa.Computed('net_profit / NULLIF(equity, 0) * 100', persisted=True), nullable=True),
    )
    op.add_column(
        'fundamentals',
        sa.Column('debtor_days', sa.Numeric(9, 4), sa.Computed('receivables / NULLIF(sales, 0) * 365', persisted=True), nullable=True),
    )
    op.add_column(
        'fundamentals',
        sa.Column('payable_days', sa.Numeric(9, 4), sa.Computed('payables / NULLIF(cogs, 0) * 365', persisted=True), nullable=True),
    )
    op.add_column(
        'fundamentals',
        sa.Column('inventory_days', sa.Numeric(9, 4), sa.Computed('inventory / NULLIF(cogs, 0) * 365', persisted=True), nullable=True),
    )
    # Postgres generated columns can't reference other generated columns, so
    # this repeats the inventory_days/debtor_days/payable_days formulas
    # inline rather than adding them as columns.
    op.add_column(
        'fundamentals',
        sa.Column(
            'cash_conversion_cycle',
            sa.Numeric(9, 4),
            sa.Computed(
                '(inventory / NULLIF(cogs, 0) * 365) + (receivables / NULLIF(sales, 0) * 365) '
                '- (payables / NULLIF(cogs, 0) * 365)',
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        'fundamentals',
        sa.Column(
            'working_capital_days',
            sa.Numeric(9, 4),
            sa.Computed('(receivables + inventory - payables) / NULLIF(sales, 0) * 365', persisted=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('fundamentals', 'working_capital_days')
    op.drop_column('fundamentals', 'cash_conversion_cycle')
    op.drop_column('fundamentals', 'inventory_days')
    op.drop_column('fundamentals', 'payable_days')
    op.drop_column('fundamentals', 'debtor_days')
    op.drop_column('fundamentals', 'roe')
    op.drop_column('fundamentals', 'cogs')
    op.drop_column('fundamentals', 'sales')
    op.drop_column('fundamentals', 'inventory')
    op.drop_column('fundamentals', 'payables')
    op.drop_column('fundamentals', 'receivables')
    op.drop_column('fundamentals', 'equity')
    op.drop_column('fundamentals', 'net_profit')
