"""Storage exceptions exposed to domain repositories, independent of adapters."""


class TransactionAborted(RuntimeError):
    """An earlier statement failed in a way that may have rolled back the
    whole transaction (deadlock, lock wait timeout, lost connection). The
    rest of it must not run or commit on its own."""


LOCK_CONFLICT_ERRNOS = (1205, 1213)


def is_lock_conflict(exc: BaseException | None) -> bool:
    """A deadlock victim (1213) or a lock wait timeout (1205), also when it
    surfaces as TransactionAborted on a later statement. The transaction was
    rolled back, so a caller that owns the whole transaction may run it
    again; anything else may have half-applied and must not be replayed."""
    for _ in range(4):
        if exc is None:
            return False
        args = getattr(exc, "args", ())
        code = args[0] if args else None
        text = str(exc)
        if code in LOCK_CONFLICT_ERRNOS or "Deadlock found" in text or "Lock wait timeout" in text:
            return True
        if not isinstance(exc, TransactionAborted):
            return False
        exc = exc.__cause__
    return False


class IntegrityError(Exception):
    """A uniqueness or relational constraint rejected a write."""

    def __init__(self, message: str = "", errno: int | None = None):
        super().__init__(message)
        # Backend constraint code (e.g. MariaDB 1062 duplicate key, 1452
        # foreign key) so callers can tell a duplicate from other failures
        # instead of mapping every violation to "already exists".
        self.errno = errno
