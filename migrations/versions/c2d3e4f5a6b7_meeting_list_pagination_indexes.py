"""meeting list keyset-pagination indexes

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-09 10:00:00.000000

Index-only migration — it adds no column, changes no row, and drops nothing, so it cannot lose
or rewrite existing meetings. It backs the paged + searchable meeting list, which orders by
(created_at, id) DESC: plain ASCENDING indexes serve that, because the whole ORDER BY is uniformly
descending and Postgres just scans the btree backwards.

Locking note for the live box: these are ordinary (non-CONCURRENT) CREATE INDEX statements, because
Alembic runs a revision inside a transaction and CREATE INDEX CONCURRENTLY cannot run in one. They
take a brief ACCESS EXCLUSIVE lock on `meetings` — a table with one row per meeting, so it builds
in well under a second at self-host scale. If a deployment ever has a very large `meetings` table,
build these by hand with CREATE INDEX CONCURRENTLY IF NOT EXISTS (same names) before deploying;
this revision then finds them present and its checkfirst-free create would fail, so in that case
stamp it instead.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Owner-scoped list (the normal case): filter by owner, then walk newest-first.
    op.create_index('ix_meetings_owner_created', 'meetings', ['owner_id', 'created_at', 'id'])
    # Admin "all meetings" list: no owner filter, same ordering.
    op.create_index('ix_meetings_created', 'meetings', ['created_at', 'id'])


def downgrade() -> None:
    op.drop_index('ix_meetings_created', table_name='meetings')
    op.drop_index('ix_meetings_owner_created', table_name='meetings')
