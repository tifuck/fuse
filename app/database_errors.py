"""Storage exceptions exposed to domain repositories, independent of adapters."""


class IntegrityError(Exception):
    """A uniqueness or relational constraint rejected a write."""

    def __init__(self, message: str = "", errno: int | None = None):
        super().__init__(message)
        # Backend constraint code (e.g. MariaDB 1062 duplicate key, 1452
        # foreign key) so callers can tell a duplicate from other failures
        # instead of mapping every violation to "already exists".
        self.errno = errno
