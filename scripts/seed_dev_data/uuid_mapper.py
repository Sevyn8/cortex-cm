"""UUID mapper: tracks per-sheet excel-id-to-db-id correspondence.

Honours D-21: every INSERT strips the Excel's v4 UUID and lets the DB's
``DEFAULT uuidv7()`` fire. The mapper captures the assigned UUIDv7
keyed by the original Excel UUID so subsequent sheets can resolve
their FK columns by looking up the originally-referenced ID and
substituting the now-known db_id.
"""
from __future__ import annotations

from uuid import UUID


class UnresolvedFKError(KeyError):
    """Raised when an FK references an excel_id that wasn't registered.

    Common causes:
      - Sheet load order is wrong (a sheet referenced before its
        dependency loaded).
      - Self-referential FK in a sheet that should bypass _base
        (platform_users) or use multi-pass loading (org_nodes).
      - Excel data error (FK points to a non-existent ID).
    """


class UUIDMapper:
    """Per-sheet excel_id -> db_id mapping."""

    def __init__(self) -> None:
        self._maps: dict[str, dict[UUID, UUID]] = {}

    def register(
        self, sheet: str, excel_id: UUID | str, db_id: UUID
    ) -> None:
        """Record that ``excel_id`` on ``sheet`` got assigned ``db_id``
        by ``uuidv7()``.
        """
        if sheet not in self._maps:
            self._maps[sheet] = {}
        if isinstance(excel_id, str):
            excel_id = UUID(excel_id)
        self._maps[sheet][excel_id] = db_id

    def lookup(
        self, sheet: str, excel_id: UUID | str | None
    ) -> UUID | None:
        """Look up the db_id for an excel_id on a given sheet.

        Returns None if ``excel_id`` is None (NULL column in the
        Excel — the reader already translated 'NULL' / '' / whitespace
        to None). Raises ``UnresolvedFKError`` with ``sheet+excel_id``
        if not registered.
        """
        if excel_id is None:
            return None
        if isinstance(excel_id, str):
            try:
                excel_id = UUID(excel_id)
            except ValueError as e:
                raise UnresolvedFKError(
                    f"FK on sheet '{sheet}' has malformed UUID: "
                    f"{excel_id!r}"
                ) from e
        try:
            return self._maps[sheet][excel_id]
        except KeyError as e:
            raise UnresolvedFKError(
                f"FK target not registered: sheet='{sheet}' "
                f"excel_id={excel_id}"
            ) from e

    def is_mapped(self, sheet: str, excel_id: UUID | str) -> bool:
        """True if (sheet, excel_id) is in the mapper.

        Used by org_nodes' multi-pass loader to test whether a parent
        is ready before insert (it can't use ``lookup``, which would
        raise ``UnresolvedFKError`` and abort).
        """
        if isinstance(excel_id, str):
            try:
                excel_id = UUID(excel_id)
            except ValueError:
                return False
        return sheet in self._maps and excel_id in self._maps[sheet]
