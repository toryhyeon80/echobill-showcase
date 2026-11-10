"""merchant signup consent — terms/privacy versions, agreed time, AI 개선 동의 + per-feedback consent (DL-32)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # existing rows (old first-login) keep terms_agreed_at NULL → they see the terms screen once
    with op.batch_alter_table('merchants', schema=None) as batch_op:
        batch_op.add_column(sa.Column('terms_version', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('privacy_version', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('terms_agreed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('ai_consent', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('ai_consent_at', sa.DateTime(timezone=True), nullable=True))
    # existing feedback rows were made without consent → false (never exported)
    with op.batch_alter_table('feedback_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_consent', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('feedback_events', schema=None) as batch_op:
        batch_op.drop_column('ai_consent')
    with op.batch_alter_table('merchants', schema=None) as batch_op:
        batch_op.drop_column('ai_consent_at')
        batch_op.drop_column('ai_consent')
        batch_op.drop_column('terms_agreed_at')
        batch_op.drop_column('privacy_version')
        batch_op.drop_column('terms_version')
