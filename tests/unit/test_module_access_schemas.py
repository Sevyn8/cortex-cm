"""Unit tests for the Step 6.15 ``ModuleAccessRead`` schema.

S1 — enum fields serialise as canonical string values (``module`` /
``status``) rather than ``repr``-style strings, mirroring the
``TenantRead`` enum-serialisation contract.

S2 — audit-actor IDs are NOT accepted as input fields (``extra='forbid'``
on the model rejects them at construction time). The DB row carries
those columns; the schema deliberately hides them per the H1 convention.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from admin_backend.schemas.modules_access import ModuleAccessRead


def _valid_payload() -> dict[str, object]:
    """Minimal valid wire dict for ModuleAccessRead.model_validate."""
    return {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "module": "PRICING_OS",
        "status": "ENABLED",
        "enabled_at": datetime.now(tz=timezone.utc),
        "disabled_at": None,
        "created_at": datetime.now(tz=timezone.utc),
        "updated_at": datetime.now(tz=timezone.utc),
    }


def test_s1_module_and_status_serialise_as_canonical_strings() -> None:
    """``model.module_dump(mode='json')`` yields canonical enum-value strings."""
    read = ModuleAccessRead.model_validate(_valid_payload())
    dumped = read.model_dump(mode="json")
    assert dumped["module"] == "PRICING_OS"
    assert dumped["status"] == "ENABLED"
    # ``disabled_at`` round-trips as None.
    assert dumped["disabled_at"] is None


def test_s2_extra_forbid_rejects_audit_actor_id_fields() -> None:
    """``ModuleAccessRead`` rejects audit-actor IDs (extra='forbid')."""
    payload = _valid_payload()
    payload["enabled_by_user_id"] = uuid.uuid4()
    with pytest.raises(ValidationError) as exc_info:
        ModuleAccessRead.model_validate(payload)
    # Each forbidden field yields a Pydantic error of type ``extra_forbidden``.
    errs = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errs)
