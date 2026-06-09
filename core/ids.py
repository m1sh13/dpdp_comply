"""
ID generation for dpdp_comply domain objects.

Uses ULID (Universally Unique Lexicographically Sortable Identifier)
so records sort chronologically in PostgreSQL without a separate
created_at index scan.
"""

import ulid as _ulid


def new_id() -> str:
    """Return a new ULID string, e.g. '01ARZ3NDEKTSV4RRFFQ69G5FAV'."""
    return str(_ulid.new())
