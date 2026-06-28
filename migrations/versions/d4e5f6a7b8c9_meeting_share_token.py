"""meeting public share token

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-28 20:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('meetings', sa.Column('share_token', sa.String(length=64), nullable=True))
    op.create_index(
        op.f('ix_meetings_share_token'), 'meetings', ['share_token'], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_meetings_share_token'), table_name='meetings')
    op.drop_column('meetings', 'share_token')
