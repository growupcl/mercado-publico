"""avisos de fin de prueba y de suscripción terminada

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('suscripciones', schema=None) as batch_op:
        batch_op.add_column(sa.Column('aviso_fin_prueba_en', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('aviso_vencida_en', sa.DateTime(), nullable=True))
    # Las que ya vencieron antes de esta versión no deben recibir un aviso atrasado.
    op.execute("UPDATE suscripciones SET aviso_vencida_en = CURRENT_TIMESTAMP WHERE estado = 'vencida'")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('suscripciones', schema=None) as batch_op:
        batch_op.drop_column('aviso_vencida_en')
        batch_op.drop_column('aviso_fin_prueba_en')
