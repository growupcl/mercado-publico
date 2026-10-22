"""invitaciones al piloto enviadas desde el panel

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'invitaciones',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=80), nullable=False),
        sa.Column('empresa', sa.String(length=120), nullable=False),
        sa.Column('telefono', sa.String(length=20), nullable=False),
        sa.Column('vende', sa.String(length=300), nullable=False),
        sa.Column('region', sa.String(length=40), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('enviada_en', sa.DateTime(), nullable=False),
        sa.Column('seguimiento_en', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_invitaciones_telefono'), 'invitaciones', ['telefono'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_invitaciones_telefono'), table_name='invitaciones')
    op.drop_table('invitaciones')
