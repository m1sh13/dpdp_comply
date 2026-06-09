"""
Core domain models for the DPDP Act 2023.

All models use Pydantic v2 for validation.  Fields are documented
with the Act section they implement.  Immutable (frozen) models are
used wherever the Act intends a record to be tamper-evident.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .enums import (
    LawfulBasis, LegitimateUse, ProcessingOperation,
    DataSensitivity, FiduciaryClass, ConsentState,
    RightType, BreachSeverity, ExemptionGround,
)
from .ids import new_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Data Principal
# ---------------------------------------------------------------------------

class DataPrincipal(BaseModel):
    """
    Represents the individual to whom personal data relates. §2(j).
    Where the individual is a child (< 18 yrs), is_child must be True
    and guardian_id must be set — §9(1).
    """
    id: str = Field(default_factory=new_id)
    external_ref: str | None = Field(
        default=None,
        description="Your system's user/customer ID for this principal.",
    )
    is_child: bool = Field(
        default=False,
        description="True if the individual has not completed 18 years. §2(f).",
    )
    has_disability: bool = Field(
        default=False,
        description="True if the principal has a disability and has a lawful guardian. §2(j)(ii).",
    )
    guardian_id: str | None = Field(
        default=None,
        description="ID of the parent/lawful guardian. Required when is_child=True or has_disability=True. §9(1).",
    )
    nominated_successor_id: str | None = Field(
        default=None,
        description="Principal nominated under §14 to exercise rights on death/incapacity.",
    )
    created_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def guardian_required_for_child(self) -> DataPrincipal:
        if (self.is_child or self.has_disability) and not self.guardian_id:
            raise ValueError(
                "guardian_id is required when is_child=True or has_disability=True. §9(1)."
            )
        return self

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Personal data category
# ---------------------------------------------------------------------------

class PersonalDataCategory(BaseModel):
    """
    Describes a category of personal data collected by a fiduciary.
    Sensitivity drives obligation scoping and penalty brackets.
    """
    id: str = Field(default_factory=new_id)
    name: str = Field(description="Human-readable name, e.g. 'mobile phone number'.")
    description: str = Field(default="")
    sensitivity: DataSensitivity = Field(default=DataSensitivity.STANDARD)
    examples: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Processing purpose
# ---------------------------------------------------------------------------

class ProcessingPurpose(BaseModel):
    """
    The 'specified purpose' as defined in §2(za) and required by §5(1)(i).
    A purpose must be concrete, limited, and expressed in plain language.
    """
    id: str = Field(default_factory=new_id)
    name: str = Field(description="Short label, e.g. 'KYC verification'.")
    description: str = Field(
        description="Plain-language description shown to the Data Principal. §5(1)(i).",
    )
    lawful_basis: LawfulBasis
    legitimate_use: LegitimateUse | None = Field(
        default=None,
        description="Required when lawful_basis=LEGITIMATE_USE. §7.",
    )
    operations: list[ProcessingOperation] = Field(
        default_factory=list,
        description="Processing operations performed for this purpose. §2(x).",
    )
    data_categories: list[str] = Field(
        default_factory=list,
        description="IDs of PersonalDataCategory instances used for this purpose.",
    )
    retention_days: int | None = Field(
        default=None,
        description="Days to retain data after purpose lapses. None = legal retention applies.",
    )
    cross_border_transfer: bool = Field(
        default=False,
        description="True if data is transferred outside India for this purpose. §16.",
    )
    exemptions: list[ExemptionGround] = Field(
        default_factory=list,
        description="Any §17 exemptions that apply to this purpose.",
    )

    @model_validator(mode="after")
    def legitimate_use_required(self) -> ProcessingPurpose:
        if self.lawful_basis == LawfulBasis.LEGITIMATE_USE and not self.legitimate_use:
            raise ValueError(
                "legitimate_use must be specified when lawful_basis=LEGITIMATE_USE. §4(1)(b)."
            )
        return self


# ---------------------------------------------------------------------------
# Notice record
# ---------------------------------------------------------------------------

class NoticeRecord(BaseModel):
    """
    Immutable record of a §5 notice served to a Data Principal.
    Must be created before or alongside a consent request. §5(1).
    """
    id: str = Field(default_factory=new_id)
    principal_id: str
    fiduciary_id: str
    purpose_ids: list[str] = Field(
        description="IDs of ProcessingPurpose entries this notice covers.",
    )
    language: str = Field(
        default="en",
        description="BCP-47 language tag. Must be English or an Eighth Schedule language. §5(3).",
    )
    content_hash: str = Field(
        description="SHA-256 hex digest of the notice text served — for tamper evidence.",
    )
    channel: str = Field(
        description="Delivery channel, e.g. 'email', 'in-app', 'sms', 'web'.",
    )
    served_at: datetime = Field(default_factory=_utcnow)
    # For retrospective notices under §5(2) (pre-Act consent)
    is_retrospective: bool = Field(default=False)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Consent record
# ---------------------------------------------------------------------------

class ConsentRecord(BaseModel):
    """
    Authoritative record of a Data Principal's consent. §6.

    The six §6(1) properties (free, specific, informed, unconditional,
    unambiguous, limited) are validated at creation time by the consent
    engine.  This model stores the outcome + full audit metadata.
    """
    id: str = Field(default_factory=new_id)
    principal_id: str
    fiduciary_id: str
    notice_id: str = Field(
        description="The NoticeRecord that preceded this consent. §5(1).",
    )
    purpose_ids: list[str] = Field(
        description="Purposes consented to. Must be a subset of notice purpose_ids.",
    )
    state: ConsentState = Field(default=ConsentState.PENDING)

    # §6(1) attestation flags — set by the consent engine after validation
    is_free: bool = False
    is_specific: bool = False
    is_informed: bool = False
    is_unconditional: bool = False
    is_unambiguous: bool = False
    is_limited_to_necessary: bool = False

    given_at: datetime | None = None
    withdrawn_at: datetime | None = None
    expired_at: datetime | None = None

    # Withdrawal channel must mirror the consent channel — §6(4)
    consent_channel: str = Field(default="", description="Channel used to collect consent.")
    withdrawal_channel: str | None = None

    # Used when consent flows through a Consent Manager — §6(7)
    consent_manager_id: str | None = None

    # Proof obligation falls on the fiduciary — §6(10)
    proof_reference: str | None = Field(
        default=None,
        description="Reference to stored evidence (e.g. signed token, screenshot URL) for §6(10) proof.",
    )

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("purpose_ids")
    @classmethod
    def at_least_one_purpose(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("A consent record must cover at least one purpose.")
        return v

    def is_valid_for_processing(self) -> bool:
        """Returns True only when all §6(1) conditions are met and state is ACTIVE."""
        return (
            self.state == ConsentState.ACTIVE
            and self.is_free
            and self.is_specific
            and self.is_informed
            and self.is_unconditional
            and self.is_unambiguous
            and self.is_limited_to_necessary
        )


# ---------------------------------------------------------------------------
# Data Fiduciary profile
# ---------------------------------------------------------------------------

class DataFiduciaryProfile(BaseModel):
    """
    Configuration profile for a Data Fiduciary entity.
    Drives which obligations, exemptions and retention rules apply.
    """
    id: str = Field(default_factory=new_id)
    name: str
    fiduciary_class: FiduciaryClass = Field(default=FiduciaryClass.STANDARD)

    # SDF-specific — §10(2)(a)
    dpo_name: str | None = None
    dpo_contact: str | None = None
    dpo_based_in_india: bool = True

    # Contact for §8(9) publication requirement
    published_contact: str | None = None

    # Whether this fiduciary has obtained a Central Govt age exemption — §9(5)
    child_age_threshold: int = Field(
        default=18,
        description="Effective age threshold for child consent. Default 18 per §2(f). May be lowered by §9(5) notification.",
    )

    exemptions: list[ExemptionGround] = Field(default_factory=list)
    processing_activities: list[str] = Field(
        default_factory=list,
        description="IDs of ProcessingActivity entries owned by this fiduciary.",
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Processing activity
# ---------------------------------------------------------------------------

class ProcessingActivity(BaseModel):
    """
    A Record of Processing Activity (RoPA) entry.
    Links a fiduciary, purpose, data categories, and any processors.
    """
    id: str = Field(default_factory=new_id)
    fiduciary_id: str
    purpose_id: str
    processor_ids: list[str] = Field(
        default_factory=list,
        description="IDs of Data Processors engaged under §8(2) contracts.",
    )
    data_category_ids: list[str]
    operations: list[ProcessingOperation]
    involves_children: bool = Field(default=False)
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
    last_reviewed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Policy config
# ---------------------------------------------------------------------------

class PolicyConfig(BaseModel):
    """
    Per-fiduciary-class policy configuration.
    Consumed by the PolicyRegistry to resolve obligations at runtime.
    """
    fiduciary_class: FiduciaryClass

    # §8(8) — days of inactivity after which purpose is deemed lapsed
    purpose_lapse_days: int = Field(
        default=365,
        description="Days before purpose-served presumption kicks in per §8(8).",
    )

    # §13(2) — grievance response SLA
    grievance_response_days: int = Field(
        default=30,
        description="Days within which grievances must be addressed. §13(2).",
    )

    # §29(2) — appeal window
    appeal_window_days: int = Field(
        default=60,
        description="Days to appeal a Board order. §29(2).",
    )

    # SDF-specific
    dpia_interval_days: int = Field(
        default=365,
        description="Maximum interval between DPIAs. Only relevant for SDF. §10(2)(c)(i).",
    )
    audit_interval_days: int = Field(
        default=365,
        description="Maximum interval between independent audits. SDF only. §10(2)(c)(ii).",
    )

    # Whether full §5/§8(3)/§8(7)/§10/§11 obligations apply (disabled for startups §17(3))
    notice_required: bool = True
    accuracy_obligation: bool = True
    erasure_obligation: bool = True
    sdf_obligations: bool = False
    access_right: bool = True
