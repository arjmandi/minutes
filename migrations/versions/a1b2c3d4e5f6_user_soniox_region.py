"""user soniox_region

Revision ID: a1b2c3d4e5f6
Revises: e5f6a7b8c9d0
Create Date: 2026-06-29 10:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-user Soniox data-residency region ("us" | "eu"); NULL -> "us". Travels with the user's key.
    op.add_column('users', sa.Column('soniox_region', sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'soniox_region')
