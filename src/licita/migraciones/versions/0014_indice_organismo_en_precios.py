"""índice por organismo en precios, para el calendario de cada organismo en su página pública

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-20 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Una base creada con create_all (sin Alembic) ya trae el índice: se marca 0013 y no hay que crearlo de nuevo.
    existentes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes('precios')}
    if 'ix_precios_organismo' not in existentes:
        op.create_index(op.f('ix_precios_organismo'), 'precios', ['organismo'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_precios_organismo'), table_name='precios')
