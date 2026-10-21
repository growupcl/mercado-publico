"""cuenta de Mercado Pago de cada mandato, para no repetir la prueba gratis

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('mandatos_pago', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payer_id', sa.String(length=32), server_default='', nullable=False))
        batch_op.create_index(batch_op.f('ix_mandatos_pago_payer_id'), ['payer_id'], unique=False)
    with op.batch_alter_table('suscripciones', schema=None) as batch_op:
        batch_op.add_column(sa.Column('prueba_repetida', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('suscripciones', schema=None) as batch_op:
        batch_op.drop_column('prueba_repetida')
    with op.batch_alter_table('mandatos_pago', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mandatos_pago_payer_id'))
        batch_op.drop_column('payer_id')
