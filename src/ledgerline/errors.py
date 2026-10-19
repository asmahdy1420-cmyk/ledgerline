from __future__ import annotations


class LedgerError(Exception):
    """Base class for domain errors. ``status_code`` maps the error to HTTP."""

    status_code = 400
    code = "ledger_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(LedgerError):
    status_code = 404
    code = "not_found"


class ValidationFailed(LedgerError):
    status_code = 422
    code = "validation_failed"


class UnbalancedEntry(LedgerError):
    status_code = 422
    code = "unbalanced_entry"


class Conflict(LedgerError):
    status_code = 409
    code = "conflict"


class AlreadyReversed(Conflict):
    code = "already_reversed"


class IdempotencyKeyReused(LedgerError):
    status_code = 422
    code = "idempotency_key_reused"


class ImmutableRecord(LedgerError):
    status_code = 409
    code = "immutable_record"
