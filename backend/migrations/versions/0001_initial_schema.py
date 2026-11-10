"""initial schema — every table in app.models as of 2026-10-06 (D04/D09 draft, not frozen)

Revision ID: 0001
Revises: (none)
Create Date: 2026-10-06 10:30:10.264172
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=False),
    sa.Column('before_data', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('after_data', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('feedback_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('ledger_entry_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('corrected_data', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_feedback_events_ledger_entry_id', 'feedback_events', ['ledger_entry_id'], unique=False)
    op.create_index('ix_feedback_events_user_id', 'feedback_events', ['user_id'], unique=False)

    op.create_table('idempotency_records',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('response_body', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'key', name='uq_idempotency_user_key')
    )
    op.create_table('ledger_entries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('entry_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('counterparty', sa.String(length=200), nullable=True),
    sa.Column('items', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('total_amount', sa.Integer(), nullable=True),
    sa.Column('credit', sa.Boolean(), nullable=True),
    sa.Column('utterance_text', sa.Text(), nullable=True),
    sa.Column('parser_type', sa.String(length=32), nullable=True),
    sa.Column('ai_model_used', sa.String(length=64), nullable=True),
    sa.Column('is_fallback_used', sa.Boolean(), nullable=False),
    sa.Column('processing_time_ms', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('tax_status', sa.String(length=32), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ledger_entries_entry_id', 'ledger_entries', ['entry_id'], unique=True)
    op.create_index('ix_ledger_entries_idempotency_key', 'ledger_entries', ['idempotency_key'], unique=False)
    op.create_index('ix_ledger_entries_user_id', 'ledger_entries', ['user_id'], unique=False)

    op.create_table('merchants',
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('kakao_provider_id', sa.String(length=128), nullable=True),
    sa.Column('business_name', sa.String(length=200), nullable=True),
    sa.Column('business_number_enc', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('user_id'),
    sa.UniqueConstraint('kakao_provider_id')
    )
    op.create_table('payment_records',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('counterparty', sa.String(length=200), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_payment_records_user_id', 'payment_records', ['user_id'], unique=False)

    op.create_table('tax_invoice_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('ledger_entry_id', sa.Integer(), nullable=False),
    sa.Column('nts_state', sa.String(length=32), nullable=False),
    sa.Column('fail_reason', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_tax_invoice_events_ledger_entry_id', 'tax_invoice_events', ['ledger_entry_id'], unique=False)
    op.create_index('ix_tax_invoice_events_user_id', 'tax_invoice_events', ['user_id'], unique=False)



def downgrade() -> None:
    op.drop_index('ix_tax_invoice_events_user_id', table_name='tax_invoice_events')
    op.drop_index('ix_tax_invoice_events_ledger_entry_id', table_name='tax_invoice_events')

    op.drop_table('tax_invoice_events')
    op.drop_index('ix_payment_records_user_id', table_name='payment_records')

    op.drop_table('payment_records')
    op.drop_table('merchants')
    op.drop_index('ix_ledger_entries_user_id', table_name='ledger_entries')
    op.drop_index('ix_ledger_entries_idempotency_key', table_name='ledger_entries')
    op.drop_index('ix_ledger_entries_entry_id', table_name='ledger_entries')

    op.drop_table('ledger_entries')
    op.drop_table('idempotency_records')
    op.drop_index('ix_feedback_events_user_id', table_name='feedback_events')
    op.drop_index('ix_feedback_events_ledger_entry_id', table_name='feedback_events')

    op.drop_table('feedback_events')
    op.drop_table('audit_logs')
