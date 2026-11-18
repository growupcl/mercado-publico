"""cómo se hizo cada compra (licitación, Compra Ágil, Convenio Marco, trato directo), de qué licitación salió y los
avisos del radar "Se viene"

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-18 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('precios', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origen', sa.String(length=20), server_default='', nullable=False))
        batch_op.add_column(sa.Column('codigo_licitacion', sa.String(length=40), server_default='', nullable=False))
        batch_op.create_index(batch_op.f('ix_precios_codigo_licitacion'), ['codigo_licitacion'], unique=False)
    # Las adjudicaciones y las cotizaciones de Compra Ágil ya se sabe de dónde vienen.
    op.execute("UPDATE precios SET origen = 'licitacion', codigo_licitacion = referencia WHERE fuente = 'adjudicacion'")
    op.execute("UPDATE precios SET origen = 'compra_agil', codigo_licitacion = referencia WHERE fuente = 'cotizacion_ca'")
    op.create_table(
        'avisos_radar',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('clave', sa.String(length=200), nullable=False),
        sa.Column('enviado_en', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id', 'clave'),
    )
    op.create_index(op.f('ix_avisos_radar_empresa_id'), 'avisos_radar', ['empresa_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_avisos_radar_empresa_id'), table_name='avisos_radar')
    op.drop_table('avisos_radar')
    with op.batch_alter_table('precios', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_precios_codigo_licitacion'))
        batch_op.drop_column('codigo_licitacion')
        batch_op.drop_column('origen')
