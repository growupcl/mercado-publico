"""resumen diario por correo

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resumen_por_correo', sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.add_column(sa.Column('token_correo', sa.String(length=64), server_default='', nullable=False))
        batch_op.create_index(batch_op.f('ix_empresas_token_correo'), ['token_correo'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_empresas_token_correo'))
        batch_op.drop_column('token_correo')
        batch_op.drop_column('resumen_por_correo')
