"""ledger postprocess trace — pp version + applied rules per candidate, trace on idempotency records for repair_of (PM #23)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08 11:20:16.528846
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('ledger_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('postprocess_version', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('postprocess_applied', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))
    # repair_of link: rules behind a parse 400, kept with its 24h idempotency record
    with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('postprocess_trace', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('idempotency_records', schema=None) as batch_op:
        batch_op.drop_column('postprocess_trace')
    with op.batch_alter_table('ledger_entries', schema=None) as batch_op:
        batch_op.drop_column('postprocess_applied')
        batch_op.drop_column('postprocess_version')

