"""qué falta para empezar la prueba: tarjeta o confirmar el WhatsApp

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-05 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('suscripciones', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pendiente_de', sa.String(length=10), server_default='', nullable=False))
    # Hasta ahora, toda cuenta pendiente esperaba la tarjeta.
    op.execute("UPDATE suscripciones SET pendiente_de = 'tarjeta' WHERE estado = 'pendiente'")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('suscripciones', schema=None) as batch_op:
        batch_op.drop_column('pendiente_de')
