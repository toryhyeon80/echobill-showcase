"""idempotency scope and rate limit indexes — per-user caps on /parse·/parse-audio (PM #14)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06 12:29:28.340427
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scope', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_idempotency_created_at', ['created_at'], unique=False)
        batch_op.create_index('ix_idempotency_user_scope_created', ['user_id', 'scope', 'created_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
        batch_op.drop_index('ix_idempotency_user_scope_created')
        batch_op.drop_index('ix_idempotency_created_at')
        batch_op.drop_column('scope')

