"""
Audit trail for the consent lifecycle.

Every state transition and validation outcome appends an immutable
ConsentAuditEvent.  This log is the primary evidence for §6(10)
proof obligations and Board proceedings under §27(1)(b).

The log is append-only.  Events are never updated or deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from core.ids import new_id


class AuditEventType(str, Enum):
    # Notice flow
    NOTICE_SERVED          = "notice_served"           # §5(1) — notice dispatched
    NOTICE_SERVED_RETRO    = "notice_served_retro"     # §5(2) — retrospective notice

    # Consent flow
    CONSENT_SOUGHT         = "consent_sought"          # §6 — request presented to principal
    CONSENT_GIVEN          = "consent_given"           # §6(1) — affirmative action recorded
    CONSENT_VALIDATION_FAIL= "consent_validation_fail" # §6(1) — one or more checks failed
    CONSENT_INVALID_PART   = "consent_invalid_part"    # §6(2) — part of consent void (rights waiver)

    # Withdrawal flow
    WITHDRAWAL_REQUESTED   = "withdrawal_requested"    # §6(4) — principal initiated withdrawal
    WITHDRAWAL_CHANNEL_WARN= "withdrawal_channel_warn" # §6(4) — channel harder than consent
    CONSENT_WITHDRAWN      = "consent_withdrawn"       # §6(4)/(6) — withdrawal finalised
    PROCESSORS_NOTIFIED    = "processors_notified"     # §6(6) — downstream processors told to cease

    # Expiry flow
    CONSENT_EXPIRED        = "consent_expired"         # §8(8) — purpose no longer served

    # Consent Manager flow
    CM_CONSENT_DELEGATED   = "cm_consent_delegated"    # §6(7) — routed through Consent Manager
    CM_WITHDRAWAL_DELEGATED= "cm_withdrawal_delegated" # §6(7) — withdrawal via Consent Manager

    # Guard checks
    PROCESSING_ALLOWED     = "processing_allowed"      # authorisation check passed
    PROCESSING_DENIED      = "processing_denied"       # authorisation check failed

    # Pre-Act data
    PRE_ACT_DATA_FLAGGED   = "pre_act_data_flagged"    # existing data marked unconsented


class ConsentAuditEvent(BaseModel):
    """
    Immutable audit log entry.  Every field is set at creation time.
    """
    id: str = Field(default_factory=new_id)
    event_type: AuditEventType
    consent_id: str | None = None       # None for events before a ConsentRecord exists
    principal_id: str
    fiduciary_id: str
    purpose_ids: list[str] = Field(default_factory=list)
    actor: str = Field(
        description=(
            "Who triggered this event: 'principal', 'fiduciary', 'consent_manager', 'system'."
        )
    )
    detail: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config = {"frozen": True}
