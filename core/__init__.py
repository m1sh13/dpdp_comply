"""
dpdp_comply.core
~~~~~~~~~~~~~~~~
Foundation types, enums, validators and policy registry for the
Digital Personal Data Protection Act, 2023 (Act No. 22 of 2023).

All section references are to the Act unless stated otherwise.
"""

from .enums import (
    LawfulBasis,
    LegitimateUse,
    ProcessingOperation,
    DataSensitivity,
    FiduciaryClass,
    ConsentState,
    RightType,
    BreachSeverity,
    ExemptionGround,
)
from .models import (
    DataPrincipal,
    ProcessingPurpose,
    PersonalDataCategory,
    ConsentRecord,
    NoticeRecord,
    DataFiduciaryProfile,
    ProcessingActivity,
    PolicyConfig,
)
from .validators import (
    validate_consent_requirements,
    validate_notice_requirements,
    validate_purpose_limitation,
    validate_data_minimisation,
)
from .registry import PolicyRegistry
from .ids import new_id
from .exceptions import (
    DPDPError,
    ConsentInvalidError,
    NoticeRequiredError,
    PurposeLimitationError,
    DataMinimisationError,
    UnlawfulProcessingError,
    RetentionViolationError,
)

__all__ = [
    # enums
    "LawfulBasis", "LegitimateUse", "ProcessingOperation",
    "DataSensitivity", "FiduciaryClass", "ConsentState",
    "RightType", "BreachSeverity", "ExemptionGround",
    # models
    "DataPrincipal", "ProcessingPurpose", "PersonalDataCategory",
    "ConsentRecord", "NoticeRecord", "DataFiduciaryProfile",
    "ProcessingActivity", "PolicyConfig",
    # validators
    "validate_consent_requirements", "validate_notice_requirements",
    "validate_purpose_limitation", "validate_data_minimisation",
    # registry
    "PolicyRegistry",
    # utils
    "new_id",
    # exceptions
    "DPDPError", "ConsentInvalidError", "NoticeRequiredError",
    "PurposeLimitationError", "DataMinimisationError",
    "UnlawfulProcessingError", "RetentionViolationError",
]
