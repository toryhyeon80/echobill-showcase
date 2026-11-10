"""ORM import smoke — models load without DB."""

from app.models import (
    AuditLog,
    Base,
    FeedbackEvent,
    LedgerEntry,
    Merchant,
    PaymentRecord,
    TaxInvoiceEvent,
)


def test_models_importable() -> None:
    """Declarative models register on Base metadata."""
    names = {t.name for t in Base.metadata.tables.values()}
    assert "merchants" in names
    assert "ledger_entries" in names
    assert "feedback_events" in names
    assert "payment_records" in names
    assert "tax_invoice_events" in names
    assert "audit_logs" in names
    assert LedgerEntry.__tablename__ == "ledger_entries"
    assert Merchant.__tablename__ == "merchants"
    assert FeedbackEvent.__tablename__ == "feedback_events"
    assert PaymentRecord.__tablename__ == "payment_records"
    assert TaxInvoiceEvent.__tablename__ == "tax_invoice_events"
    assert AuditLog.__tablename__ == "audit_logs"
