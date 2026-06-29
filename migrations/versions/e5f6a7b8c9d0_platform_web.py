"""add 'web' platform (generic tab-audio capture)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-29 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive enum value (not used as data within this migration -> txn-safe on PG 12+).
    op.execute("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'web'")


def downgrade() -> None:
    # Postgres cannot DROP an enum value; leave 'web' in place.
    pass
