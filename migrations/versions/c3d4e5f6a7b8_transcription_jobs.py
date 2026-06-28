"""upload platform + transcription_jobs

Revision ID: c3d4e5f6a7b8
Revises: b7e2c9f1a3d4
Create Date: 2026-06-28 19:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b7e2c9f1a3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

job_status = postgresql.ENUM(
    'queued', 'processing', 'done', 'failed', 'canceled', name='job_status', create_type=False
)


def upgrade() -> None:
    # New 'upload' platform value (additive; not used as data within this migration -> txn-safe).
    op.execute("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'upload'")

    bind = op.get_bind()
    job_status.create(bind, checkfirst=True)

    op.create_table(
        'transcription_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('meeting_id', sa.Uuid(), nullable=False),
        sa.Column('s3_key', sa.String(length=1024), nullable=False),
        sa.Column('original_filename', sa.Text(), nullable=True),
        sa.Column('content_type', sa.String(length=128), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('status', job_status, nullable=False, server_default='queued'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('run_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_transcription_jobs_owner_id'), 'transcription_jobs', ['owner_id'], unique=False
    )
    op.create_index(
        op.f('ix_transcription_jobs_meeting_id'), 'transcription_jobs', ['meeting_id'], unique=False
    )
    op.create_index(
        op.f('ix_transcription_jobs_status'), 'transcription_jobs', ['status'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_transcription_jobs_status'), table_name='transcription_jobs')
    op.drop_index(op.f('ix_transcription_jobs_meeting_id'), table_name='transcription_jobs')
    op.drop_index(op.f('ix_transcription_jobs_owner_id'), table_name='transcription_jobs')
    op.drop_table('transcription_jobs')
    bind = op.get_bind()
    job_status.drop(bind, checkfirst=True)
    # Note: Postgres cannot DROP a value from an enum; 'upload' on `platform` is left in place.
