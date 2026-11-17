"""agenda "Voy a postular": licitaciones que cada empresa sigue hasta el resultado

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'postulaciones',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('licitacion_codigo', sa.String(length=40), nullable=False),
        sa.Column('activa', sa.Boolean(), nullable=False),
        sa.Column('avisos', sa.JSON(), nullable=False),
        sa.Column('creada_en', sa.DateTime(), nullable=False),
        sa.Column('revisada_en', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id', 'licitacion_codigo'),
    )
    op.create_index(op.f('ix_postulaciones_empresa_id'), 'postulaciones', ['empresa_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_postulaciones_empresa_id'), table_name='postulaciones')
    op.drop_table('postulaciones')
