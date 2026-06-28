"""meeting translation config + translation status/source

Revision ID: b7e2c9f1a3d4
Revises: ed95ddce895f
Create Date: 2026-06-28 18:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7e2c9f1a3d4'
down_revision: Union[str, None] = 'ed95ddce895f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

translation_status = postgresql.ENUM(
    'pending', 'ok', 'failed', name='translation_status', create_type=False
)
translation_source = postgresql.ENUM(
    'auto', 'manual', name='translation_source', create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    translation_status.create(bind, checkfirst=True)
    translation_source.create(bind, checkfirst=True)

    # First-class per-meeting translation config. server_default backfills existing rows for the
    # NOT NULL columns; the ORM supplies its own Python-side defaults going forward.
    op.add_column(
        'meetings',
        sa.Column(
            'translation_enabled', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        'meetings', sa.Column('translation_output_language', sa.String(length=16), nullable=True)
    )
    op.add_column(
        'meetings',
        sa.Column(
            'translation_input_language',
            sa.String(length=16),
            nullable=False,
            server_default='detect',
        ),
    )
    op.add_column('meetings', sa.Column('translation_prompt', sa.Text(), nullable=True))
    op.add_column(
        'meetings', sa.Column('translation_model', sa.String(length=128), nullable=True)
    )

    op.add_column(
        'translations',
        sa.Column('status', translation_status, nullable=False, server_default='ok'),
    )
    op.add_column(
        'translations',
        sa.Column('source', translation_source, nullable=False, server_default='auto'),
    )


def downgrade() -> None:
    op.drop_column('translations', 'source')
    op.drop_column('translations', 'status')
    op.drop_column('meetings', 'translation_model')
    op.drop_column('meetings', 'translation_prompt')
    op.drop_column('meetings', 'translation_input_language')
    op.drop_column('meetings', 'translation_output_language')
    op.drop_column('meetings', 'translation_enabled')

    bind = op.get_bind()
    translation_source.drop(bind, checkfirst=True)
    translation_status.drop(bind, checkfirst=True)
