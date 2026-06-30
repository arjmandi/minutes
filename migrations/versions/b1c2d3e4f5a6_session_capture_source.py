"""session capture source (tab | mic | upload)

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-06-30 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    capture_source = postgresql.ENUM(
        'tab', 'mic', 'upload', name='capture_source', create_type=False
    )
    capture_source.create(bind, checkfirst=True)
    # Existing rows default to 'tab' (every prior session was a single tab/meeting capture).
    op.add_column(
        'sessions', sa.Column('source', capture_source, nullable=False, server_default='tab')
    )
    op.create_index('ix_sessions_meeting_source', 'sessions', ['meeting_id', 'source'])
    # MANDATORY backfill: historical uploads carry platform_call_id 'upload:<job_id>'. Client
    # capture UUIDs never collide with that prefix, so this classifies them precisely.
    op.execute("UPDATE sessions SET source='upload' WHERE platform_call_id LIKE 'upload:%'")
    # Resolve pre-existing duplicate LIVE sessions per (meeting, source) before the unique index is
    # built — stale 'active'/'joining' rows from crashes/reconnects (all backfilled to 'tab') would
    # otherwise collide. Keep the most recently joined one; mark older duplicates 'ended'.
    op.execute(
        """
        UPDATE sessions s SET status='ended', left_at=now()
        WHERE s.status IN ('joining','active')
          AND EXISTS (
            SELECT 1 FROM sessions s2
            WHERE s2.meeting_id = s.meeting_id AND s2.source = s.source
              AND s2.status IN ('joining','active')
              AND (s2.joined_at > s.joined_at
                   OR (s2.joined_at = s.joined_at AND s2.id > s.id))
          )
        """
    )
    # At most one LIVE session per (meeting, source) — prevents timeline fragmentation.
    op.create_index(
        'uq_active_session_per_source',
        'sessions',
        ['meeting_id', 'source'],
        unique=True,
        postgresql_where=sa.text("status IN ('joining', 'active')"),
    )


def downgrade() -> None:
    op.drop_index('uq_active_session_per_source', table_name='sessions')
    op.drop_index('ix_sessions_meeting_source', table_name='sessions')
    op.drop_column('sessions', 'source')
    postgresql.ENUM(name='capture_source').drop(op.get_bind(), checkfirst=True)
