"""
Pure validation functions.  Each function returns a list of violation
strings (empty = passes).  The consent engine and other modules call
these and raise the appropriate exceptions.

No I/O.  No side effects.  All inputs are domain model instances.
"""

from __future__ import annotations

from .enums import ConsentState, LawfulBasis
from .models import ConsentRecord, NoticeRecord, ProcessingPurpose
from .exceptions import (
    ConsentInvalidError,
    DataMinimisationError,
    NoticeRequiredError,
    PurposeLimitationError,
)


# ---------------------------------------------------------------------------
# §6(1) consent validation
# ---------------------------------------------------------------------------

def validate_consent_requirements(
    consent: ConsentRecord,
    notice: NoticeRecord,
    purpose: ProcessingPurpose,
) -> list[str]:
    """
    Validates the six §6(1) requirements for a consent record.

    Returns a list of human-readable violation strings.
    An empty list means the consent is valid.

    Raises nothing — callers decide whether to raise ConsentInvalidError.
    """
    violations: list[str] = []

    # Free — cannot be bundled with terms that are unfair or coercive.
    # We check for the flag set by the collection layer.
    if not consent.is_free:
        violations.append(
            "Consent is not 'free' — it may have been obtained under coercion or as a "
            "condition of service where data processing is unnecessary. §6(1)."
        )

    # Specific — each purpose must be separately consented to.
    if not consent.is_specific:
        violations.append(
            "Consent is not 'specific' — it bundles unrelated purposes. "
            "Each purpose requires a separate, distinct consent. §6(1)."
        )

    # Informed — notice must precede consent, and consent purposes must
    # be a subset of the notice's purposes.
    if not consent.is_informed:
        violations.append(
            "Consent is not 'informed' — no preceding notice found or notice "
            "did not cover these purposes. §5(1), §6(1)."
        )

    # Cross-check: consent purposes ⊆ notice purposes
    uncovered = set(consent.purpose_ids) - set(notice.purpose_ids)
    if uncovered:
        violations.append(
            f"Consent covers purposes not described in the notice: {uncovered}. "
            "Notice must describe all purposes before consent is sought. §5(1)(i)."
        )

    # Unconditional — no waiver of rights bundled into consent.
    if not consent.is_unconditional:
        violations.append(
            "Consent is not 'unconditional' — it appears to bundle a waiver of Data "
            "Principal rights (e.g. waiver of Board complaint right). §6(2)."
        )

    # Unambiguous — must be a clear affirmative action, not pre-ticked or inferred.
    if not consent.is_unambiguous:
        violations.append(
            "Consent is not 'unambiguous' — it was not obtained through a clear "
            "affirmative action (e.g. pre-ticked box, inferred from inaction). §6(1)."
        )

    # Limited to necessary — data categories must not exceed what is needed.
    if not consent.is_limited_to_necessary:
        violations.append(
            "Consent is not limited to data necessary for the specified purpose. §6(1)."
        )

    return violations


def assert_consent_valid(
    consent: ConsentRecord,
    notice: NoticeRecord,
    purpose: ProcessingPurpose,
) -> None:
    """Raises ConsentInvalidError if any §6(1) requirement fails."""
    violations = validate_consent_requirements(consent, notice, purpose)
    if violations:
        raise ConsentInvalidError(
            "Consent record fails §6(1) requirements.",
            failed_checks=violations,
        )


# ---------------------------------------------------------------------------
# §5(1) notice validation
# ---------------------------------------------------------------------------

def validate_notice_requirements(
    notice: NoticeRecord,
    consent: ConsentRecord,
) -> list[str]:
    """
    Validates that a notice satisfies §5(1):
      (i)  describes the personal data and purpose
      (ii) describes how rights under §6(4) and §13 can be exercised
      (iii) describes how to complain to the Board

    In practice we validate structural presence; content audits are
    left to the notice manager module.
    """
    violations: list[str] = []

    # Notice must predate or accompany the consent
    if consent.given_at and notice.served_at > consent.given_at:
        violations.append(
            "Notice was served AFTER consent was given. Notice must accompany or "
            "precede the consent request. §5(1)."
        )

    # The notice must cover all purposes in the consent
    uncovered = set(consent.purpose_ids) - set(notice.purpose_ids)
    if uncovered:
        violations.append(
            f"Notice does not cover all consented purposes: {uncovered}. §5(1)(i)."
        )

    # Notice must be in English or an Eighth Schedule language
    # We allow any ISO 639-1 code; strict enforcement requires a lookup table.
    if not notice.language:
        violations.append(
            "Notice language not specified. Must be in English or an Eighth Schedule "
            "language. §5(3)."
        )

    return violations


# ---------------------------------------------------------------------------
# Purpose limitation  (§6(1) — consent limited to specified purpose)
# ---------------------------------------------------------------------------

def validate_purpose_limitation(
    requested_purpose_id: str,
    consent: ConsentRecord,
) -> None:
    """
    Raises PurposeLimitationError if the requested purpose is not covered
    by the consent record.
    """
    if requested_purpose_id not in consent.purpose_ids:
        raise PurposeLimitationError(requested_purpose_id, consent.id)


# ---------------------------------------------------------------------------
# Data minimisation (§6(1) — limited to necessary data)
# ---------------------------------------------------------------------------

def validate_data_minimisation(
    requested_category_ids: list[str],
    purpose: ProcessingPurpose,
) -> None:
    """
    Raises DataMinimisationError if any requested data category is not
    declared as necessary for the purpose.
    """
    allowed = set(purpose.data_categories)
    excess = [c for c in requested_category_ids if c not in allowed]
    if excess:
        raise DataMinimisationError(excess)


# ---------------------------------------------------------------------------
# Withdrawal ease parity (§6(4))
# ---------------------------------------------------------------------------

def validate_withdrawal_channel_parity(consent: ConsentRecord) -> list[str]:
    """
    §6(4): withdrawal ease must be comparable to consent ease.
    Returns a violation string if the withdrawal channel is materially
    harder than the consent channel (e.g. paper form vs. in-app toggle).
    """
    CHANNEL_DIFFICULTY: dict[str, int] = {
        "in-app": 1,
        "web": 1,
        "email": 2,
        "sms": 2,
        "phone": 3,
        "paper": 4,
        "in-person": 4,
    }

    violations: list[str] = []
    if not consent.withdrawal_channel:
        return violations  # Not yet withdrawn; nothing to check.

    consent_diff = CHANNEL_DIFFICULTY.get(consent.consent_channel, 2)
    withdrawal_diff = CHANNEL_DIFFICULTY.get(consent.withdrawal_channel, 2)

    if withdrawal_diff > consent_diff:
        violations.append(
            f"Withdrawal channel '{consent.withdrawal_channel}' is harder than "
            f"consent channel '{consent.consent_channel}'. Withdrawal ease must be "
            "comparable to consent ease. §6(4)."
        )
    return violations
