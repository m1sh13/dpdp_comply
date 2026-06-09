"""
Exception hierarchy for dpdp_comply.
All exceptions carry the Act section that was violated.
"""


class DPDPError(Exception):
    """Base exception for all DPDP compliance errors."""

    def __init__(self, message: str, section: str = "") -> None:
        self.section = section
        super().__init__(f"[DPDP §{section}] {message}" if section else message)


class ConsentInvalidError(DPDPError):
    """
    Raised when a consent record fails one or more of the §6(1) requirements:
    free, specific, informed, unconditional, unambiguous, limited.
    """
    def __init__(self, message: str, failed_checks: list[str] | None = None) -> None:
        self.failed_checks = failed_checks or []
        detail = f"{message} (failed: {', '.join(self.failed_checks)})" if self.failed_checks else message
        super().__init__(detail, section="6(1)")


class NoticeRequiredError(DPDPError):
    """Raised when processing is attempted without a preceding §5 notice."""
    def __init__(self, principal_id: str, purpose_id: str) -> None:
        super().__init__(
            f"No notice on record for principal '{principal_id}' and purpose '{purpose_id}'. "
            "A notice must precede or accompany every consent request.",
            section="5(1)",
        )


class PurposeLimitationError(DPDPError):
    """
    Raised when processing is attempted for a purpose beyond what was
    consented to or notified. §6(1) — consent must be limited to the
    specified purpose.
    """
    def __init__(self, purpose_id: str, consent_id: str) -> None:
        super().__init__(
            f"Purpose '{purpose_id}' is not covered by consent '{consent_id}'. "
            "Processing must be limited to the specified purpose.",
            section="6(1)",
        )


class DataMinimisationError(DPDPError):
    """
    Raised when a consent request or processing activity includes data
    categories not necessary for the specified purpose. §6(1).
    """
    def __init__(self, excess_categories: list[str]) -> None:
        self.excess_categories = excess_categories
        super().__init__(
            f"Consent includes data categories not necessary for the purpose: "
            f"{', '.join(excess_categories)}. Consent must be limited to necessary data.",
            section="6(1)",
        )


class UnlawfulProcessingError(DPDPError):
    """Raised when no valid lawful basis exists for a processing activity. §4."""
    def __init__(self, activity_id: str) -> None:
        super().__init__(
            f"Processing activity '{activity_id}' has no valid lawful basis. "
            "Processing must be either consented (§4(1)(a)) or a legitimate use (§4(1)(b)).",
            section="4",
        )


class RetentionViolationError(DPDPError):
    """
    Raised when personal data is retained beyond the permitted period
    after consent withdrawal or purpose lapse. §8(7).
    """
    def __init__(self, principal_id: str, purpose_id: str) -> None:
        super().__init__(
            f"Personal data for principal '{principal_id}' under purpose '{purpose_id}' "
            "must be erased — consent withdrawn or purpose no longer served.",
            section="8(7)",
        )


class ChildConsentError(DPDPError):
    """Raised when verifiable parental consent is absent for a child's data. §9(1)."""
    def __init__(self, principal_id: str) -> None:
        super().__init__(
            f"Principal '{principal_id}' is a child. Verifiable consent of the parent "
            "or lawful guardian is required before processing.",
            section="9(1)",
        )


class BreachNotificationError(DPDPError):
    """Raised when a breach notification cannot be dispatched. §8(6)."""
    def __init__(self, breach_id: str, reason: str) -> None:
        super().__init__(
            f"Breach '{breach_id}' notification failed: {reason}",
            section="8(6)",
        )


class GrievanceSLAError(DPDPError):
    """Raised when a grievance is not resolved within the prescribed period. §13(2)."""
    def __init__(self, grievance_id: str, days_overdue: int) -> None:
        super().__init__(
            f"Grievance '{grievance_id}' is {days_overdue} day(s) overdue. "
            "Grievances must be addressed within the prescribed period.",
            section="13(2)",
        )
