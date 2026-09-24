"""Storage exceptions exposed to domain repositories, independent of adapters."""


class IntegrityError(Exception):
    """A uniqueness or relational constraint rejected a write."""
