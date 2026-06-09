"""
Enumerations grounded in the DPDP Act 2023.
Every member maps to a specific section or schedule entry.
"""

from enum import Enum, auto


class LawfulBasis(str, Enum):
    """
    The two lawful bases for processing personal data. §4(1).
    Every processing activity must declare exactly one.
    """
    CONSENT = "consent"                 # §4(1)(a) — Data Principal gave consent
    LEGITIMATE_USE = "legitimate_use"   # §4(1)(b) — one of the §7 legitimate uses


class LegitimateUse(str, Enum):
    """
    Exhaustive list of legitimate uses under §7.
    Only applies when LawfulBasis is LEGITIMATE_USE.
    """
    VOLUNTARY_DATA = "voluntary_data"           # §7(a) voluntarily provided, no objection
    STATE_BENEFIT = "state_benefit"             # §7(b) state subsidy/benefit/service
    STATE_FUNCTION = "state_function"           # §7(c) state function under law / sovereignty
    LEGAL_DISCLOSURE = "legal_disclosure"       # §7(d) obligation to disclose to state
    COURT_ORDER = "court_order"                 # §7(e) judgment / decree / court order
    MEDICAL_EMERGENCY = "medical_emergency"     # §7(f) threat to life or immediate health
    PUBLIC_HEALTH = "public_health"             # §7(g) epidemic / disease outbreak
    DISASTER_RELIEF = "disaster_relief"         # §7(h) disaster / breakdown of public order
    EMPLOYMENT = "employment"                   # §7(i) employment or employer protection


class ProcessingOperation(str, Enum):
    """
    Processing operations as enumerated in §2(x).
    Used to describe what a ProcessingActivity does.
    """
    COLLECTION = "collection"
    RECORDING = "recording"
    ORGANISATION = "organisation"
    STRUCTURING = "structuring"
    STORAGE = "storage"
    ADAPTATION = "adaptation"
    RETRIEVAL = "retrieval"
    USE = "use"
    ALIGNMENT = "alignment"
    COMBINATION = "combination"
    INDEXING = "indexing"
    SHARING = "sharing"
    DISCLOSURE = "disclosure"
    DISSEMINATION = "dissemination"
    RESTRICTION = "restriction"
    ERASURE = "erasure"
    DESTRUCTION = "destruction"


class DataSensitivity(str, Enum):
    """
    Sensitivity tiers used for penalty calculation (Schedule) and
    breach notification urgency. Not defined explicitly in the Act
    but implied by the Schedule and §9 (children's data).
    """
    STANDARD = "standard"       # General personal data
    CHILDREN = "children"       # Data of a child — triggers §9 obligations
    FINANCIAL = "financial"     # Financial information (§17(1)(f) context)
    HEALTH = "health"           # Health data — medical emergency / public health context
    BIOMETRIC = "biometric"     # Biometric identifiers
    IDENTITY_DOC = "identity"   # Government-issued IDs, unique identifiers


class FiduciaryClass(str, Enum):
    """
    Classification of a Data Fiduciary for obligation scoping.
    """
    STANDARD = "standard"                   # Default — §8 obligations apply
    SIGNIFICANT = "significant"             # Notified SDF — §10 additional obligations
    STARTUP_EXEMPT = "startup_exempt"       # Startup exempt under §17(3)
    STATE_INSTRUMENTALITY = "state"         # State / instrumentality — §17(4) carve-outs


class ConsentState(str, Enum):
    """
    Lifecycle states of a ConsentRecord.
    Transitions are enforced by the consent engine.
    """
    PENDING = "pending"         # Notice served, awaiting affirmative action
    ACTIVE = "active"           # Consent given, processing authorised
    WITHDRAWN = "withdrawn"     # Withdrawn under §6(4); cease processing
    EXPIRED = "expired"         # Purpose no longer served under §8(8)
    INVALID = "invalid"         # Consent was infringed under §6(2)


class RightType(str, Enum):
    """
    Rights available to a Data Principal. Chapter III.
    """
    ACCESS = "access"               # §11 — summary of data and processors
    CORRECTION = "correction"       # §12(1)(a) — correct inaccurate data
    COMPLETION = "completion"       # §12(1)(b) — complete incomplete data
    UPDATION = "updation"           # §12(1)(c) — update data
    ERASURE = "erasure"             # §12(3) — erase data
    GRIEVANCE = "grievance"         # §13 — raise a grievance
    NOMINATION = "nomination"       # §14 — nominate a successor


class BreachSeverity(str, Enum):
    """
    Breach severity used to determine notification urgency and
    penalty bracket under the Schedule.
    """
    CRITICAL = "critical"       # Affects confidentiality + integrity + availability
    HIGH = "high"               # Two of the three CIA properties affected
    MEDIUM = "medium"           # Single property; limited principals affected
    LOW = "low"                 # Minimal risk; no sensitive data involved


class ExemptionGround(str, Enum):
    """
    Grounds under which Chapter II / III obligations may not apply. §17.
    """
    LEGAL_CLAIM = "legal_claim"                 # §17(1)(a)
    JUDICIAL_FUNCTION = "judicial_function"     # §17(1)(b)
    OFFENCE_PREVENTION = "offence_prevention"   # §17(1)(c)
    CROSS_BORDER_CONTRACT = "cross_border"      # §17(1)(d) — non-India principal
    MERGER_AMALGAMATION = "merger"              # §17(1)(e)
    LOAN_DEFAULT = "loan_default"               # §17(1)(f)
    SOVEREIGNTY = "sovereignty"                 # §17(2)(a) — state instrumentality
    RESEARCH_ARCHIVAL = "research_archival"     # §17(2)(b)
    STARTUP_EXEMPTION = "startup"               # §17(3)
    CENTRAL_GOVT_NOTIFIED = "govt_notified"     # §17(5) transitional
