"""add BS-GARP fundamentals (peg, eps growth, fcf) and ema_21

Revision ID: a1c9f3e7b482
Revises: 5ddc59844735
Create Date: 2026-09-12 20:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c9f3e7b482'
down_revision: Union[str, None] = '5ddc59844735'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('fundamentals', sa.Column('peg', sa.Numeric(9, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('eps_diluted', sa.Numeric(12, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('eps_growth', sa.Numeric(9, 4), nullable=True))
    op.add_column('fundamentals', sa.Column('fcf_per_share', sa.Numeric(12, 4), nullable=True))
    # Derived, DB-enforced so it can never drift from the two raw inputs it's
    # entered alongside (BS-GARP protocol 2.1: FCF Conversion = Annual FCF per
    # Share / Annual Diluted EPS). NULLIF guards eps_diluted == 0.
    op.add_column(
        'fundamentals',
        sa.Column(
            'fcf_conversion',
            sa.Numeric(9, 4),
            sa.Computed('fcf_per_share / NULLIF(eps_diluted, 0) * 100', persisted=True),
            nullable=True,
        ),
    )
    op.add_column('indicators', sa.Column('ema_21', sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('indicators', 'ema_21')
    op.drop_column('fundamentals', 'fcf_conversion')
    op.drop_column('fundamentals', 'fcf_per_share')
    op.drop_column('fundamentals', 'eps_growth')
    op.drop_column('fundamentals', 'eps_diluted')
    op.drop_column('fundamentals', 'peg')
