"""
Data Transfer Objects for the consent engine.

These are the inputs callers pass to ConsentEngine methods.
They are intentionally separate from the domain models so that
the engine can apply Act-level checks before constructing a
ConsentRecord.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ConsentRequest(BaseModel):
    """
    Input DTO for ConsentEngine.seek_consent().

    The caller asserts the collection conditions; the engine
    validates them against §6(1) and the preceding notice.

    Fields map directly to the six §6(1) requirements:
      free, specific, informed, unconditional, unambiguous,
      limited_to_necessary.
    """

    principal_id: str
    fiduciary_id: str
    notice_id: str = Field(
        description="ID of the NoticeRecord already served to this principal. §5(1)."
    )
    purpose_ids: list[str] = Field(
        description="Purposes the principal is consenting to. Must be ⊆ notice.purpose_ids."
    )
    data_category_ids: list[str] = Field(
        description="Data categories the principal is consenting to. Checked for minimisation."
    )
    consent_channel: str = Field(
        description="Channel used to present the consent request, e.g. 'in-app', 'web', 'email'."
    )

    # §6(1) collection attestations — the caller (UI / API layer) asserts these.
    # Engine re-validates each one structurally where possible.
    collected_freely: bool = Field(
        description=(
            "True if consent was not made a condition of service where the data is unnecessary, "
            "and was not obtained under any coercion or deceptive practice. §6(1)."
        )
    )
    collected_specifically: bool = Field(
        description=(
            "True if this request covers only one purpose (or a set of closely related purposes "
            "each described individually). Bundled, omnibus consents must be False. §6(1)."
        )
    )
    collected_via_affirmative_action: bool = Field(
        description=(
            "True if the principal took a clear, active step (e.g. ticked a box, pressed confirm). "
            "Pre-ticked boxes, silence, and inaction must be False. §6(1)."
        )
    )
    no_rights_waived: bool = Field(
        description=(
            "True if the consent does not bundle a waiver of any Data Principal right "
            "(e.g. right to file Board complaint). §6(2)."
        )
    )

    # Optional — used when consent flows through a Consent Manager §6(7)
    consent_manager_id: str | None = None

    # Proof reference for §6(10) — fiduciary must be able to prove consent was given
    proof_reference: str | None = Field(
        default=None,
        description=(
            "Reference to evidence that consent was given "
            "(e.g. signed JWT, audit log entry ID, screenshot URL). §6(10)."
        ),
    )

    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def purpose_ids_not_empty(self) -> ConsentRequest:
        if not self.purpose_ids:
            raise ValueError("purpose_ids must contain at least one entry.")
        return self


class WithdrawalRequest(BaseModel):
    """
    Input DTO for ConsentEngine.withdraw_consent().

    §6(4): the ease of withdrawal must be comparable to the ease
    of giving consent.  The engine enforces channel parity.
    """

    consent_id: str
    principal_id: str = Field(
        description="Must match the principal_id on the ConsentRecord being withdrawn."
    )
    withdrawal_channel: str = Field(
        description=(
            "Channel used to collect the withdrawal. Must not be materially harder "
            "than the channel used to collect consent. §6(4)."
        )
    )
    reason: str | None = Field(
        default=None,
        description="Optional reason — stored for audit; not required by the Act.",
    )
    withdrawn_at: datetime | None = Field(
        default=None,
        description="Timestamp of withdrawal. Defaults to now if not provided.",
    )
