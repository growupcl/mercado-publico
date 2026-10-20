"""valoración de los calces por la empresa (👍/👎)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('calces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('valoracion', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('motivo_valoracion', sa.String(length=20), server_default='', nullable=False))
        batch_op.add_column(sa.Column('valorado_en', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('calces', schema=None) as batch_op:
        batch_op.drop_column('valorado_en')
        batch_op.drop_column('motivo_valoracion')
        batch_op.drop_column('valoracion')
