"""idempotency repair_entry_id — one repaired candidate per failed request (repair_of, PM 10/15)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # entry_id of the candidate the first repair submit made — kept with the failed request's 24h record
    with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repair_entry_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
        batch_op.drop_column('repair_entry_id')
