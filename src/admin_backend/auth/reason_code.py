"""ReasonCode: the third element of has_permission()'s return tuple.

Binary in v0: ``GRANT_MATCHED`` on allowed, ``NO_MATCHING_GRANT_OR_OUT_OF_SCOPE``
on denied. Granular codes (cascade vs module-suspended vs no-match)
deferred until Step 6.16's audit log writes need to differentiate.

Public contract: 6.9.2 will import this enum, and Step 6.16 audit log
schemas may reference its values. v0 values stay stable; future
narrowing or rename requires a coordinated migration.
"""
from enum import StrEnum


class ReasonCode(StrEnum):
    """Permission decision reason codes."""

    GRANT_MATCHED = "GRANT_MATCHED"
    NO_MATCHING_GRANT_OR_OUT_OF_SCOPE = "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE"
