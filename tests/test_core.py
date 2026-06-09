"""
Tests for dpdp_comply.core

Covers:
- Model instantiation and validation
- Enum completeness against Act sections
- Validator logic (§6(1), §5(1), purpose limitation, data minimisation)
- PolicyRegistry resolution and obligation checks
- Exception messages carry section references
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta

from core.enums import (
    LawfulBasis, LegitimateUse, ProcessingOperation,
    FiduciaryClass, ConsentState, DataSensitivity,
)
from core.models import (
    DataPrincipal, ProcessingPurpose, PersonalDataCategory,
    ConsentRecord, NoticeRecord, DataFiduciaryProfile, PolicyConfig,
)
from core.validators import (
    validate_consent_requirements,
    validate_notice_requirements,
    validate_purpose_limitation,
    validate_data_minimisation,
    validate_withdrawal_channel_parity,
    assert_consent_valid,
)
from core.registry import PolicyRegistry
from core.exceptions import (
    ConsentInvalidError, NoticeRequiredError,
    PurposeLimitationError, DataMinimisationError,
)
from core.ids import new_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def utcnow():
    return datetime.now(timezone.utc)


@pytest.fixture
def purpose():
    return ProcessingPurpose(
        id="p1",
        name="KYC verification",
        description="Verify customer identity per RBI guidelines.",
        lawful_basis=LawfulBasis.CONSENT,
        operations=[ProcessingOperation.COLLECTION, ProcessingOperation.STORAGE],
        data_categories=["cat_name", "cat_dob", "cat_address"],
    )


@pytest.fixture
def notice(purpose):
    return NoticeRecord(
        id="n1",
        principal_id="u1",
        fiduciary_id="f1",
        purpose_ids=["p1"],
        language="en",
        content_hash="abc123",
        channel="in-app",
        served_at=utcnow() - timedelta(seconds=5),
    )


@pytest.fixture
def valid_consent(notice, purpose):
    return ConsentRecord(
        id="c1",
        principal_id="u1",
        fiduciary_id="f1",
        notice_id="n1",
        purpose_ids=["p1"],
        state=ConsentState.ACTIVE,
        is_free=True,
        is_specific=True,
        is_informed=True,
        is_unconditional=True,
        is_unambiguous=True,
        is_limited_to_necessary=True,
        given_at=utcnow(),
        consent_channel="in-app",
    )


@pytest.fixture
def fiduciary():
    return DataFiduciaryProfile(
        id="f1",
        name="Acme Bank",
        fiduciary_class=FiduciaryClass.STANDARD,
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestDataPrincipal:
    def test_adult_principal_created(self):
        p = DataPrincipal(external_ref="user_42")
        assert p.is_child is False
        assert p.guardian_id is None

    def test_child_requires_guardian(self):
        with pytest.raises(ValueError, match="guardian_id is required"):
            DataPrincipal(is_child=True)

    def test_child_with_guardian_ok(self):
        p = DataPrincipal(is_child=True, guardian_id="g1")
        assert p.guardian_id == "g1"

    def test_disability_requires_guardian(self):
        with pytest.raises(ValueError):
            DataPrincipal(has_disability=True)


class TestProcessingPurpose:
    def test_legitimate_use_requires_use_type(self):
        with pytest.raises(ValueError, match="legitimate_use must be specified"):
            ProcessingPurpose(
                name="State benefit",
                description="Provide maternity benefit.",
                lawful_basis=LawfulBasis.LEGITIMATE_USE,
                # legitimate_use omitted — should fail
            )

    def test_legitimate_use_with_type_ok(self):
        p = ProcessingPurpose(
            name="State benefit",
            description="Provide maternity benefit.",
            lawful_basis=LawfulBasis.LEGITIMATE_USE,
            legitimate_use=LegitimateUse.STATE_BENEFIT,
        )
        assert p.legitimate_use == LegitimateUse.STATE_BENEFIT

    def test_consent_basis_no_legitimate_use_needed(self, purpose):
        assert purpose.lawful_basis == LawfulBasis.CONSENT
        assert purpose.legitimate_use is None


class TestConsentRecord:
    def test_empty_purpose_ids_rejected(self):
        with pytest.raises(ValueError, match="at least one purpose"):
            ConsentRecord(
                principal_id="u1",
                fiduciary_id="f1",
                notice_id="n1",
                purpose_ids=[],
            )

    def test_is_valid_for_processing_all_flags_true(self, valid_consent):
        assert valid_consent.is_valid_for_processing() is True

    def test_is_valid_for_processing_fails_if_withdrawn(self, valid_consent):
        valid_consent.state = ConsentState.WITHDRAWN
        assert valid_consent.is_valid_for_processing() is False

    def test_is_valid_for_processing_fails_if_flag_missing(self, valid_consent):
        valid_consent.is_free = False
        assert valid_consent.is_valid_for_processing() is False


class TestNoticeRecord:
    def test_notice_is_frozen(self, notice):
        with pytest.raises(Exception):  # ValidationError or AttributeError
            notice.language = "hi"

    def test_retrospective_notice_flag(self):
        n = NoticeRecord(
            principal_id="u1",
            fiduciary_id="f1",
            purpose_ids=["p1"],
            language="en",
            content_hash="xyz",
            channel="email",
            is_retrospective=True,
        )
        assert n.is_retrospective is True


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------

class TestConsentValidator:
    def test_valid_consent_no_violations(self, valid_consent, notice, purpose):
        violations = validate_consent_requirements(valid_consent, notice, purpose)
        assert violations == []

    def test_not_free_raises_violation(self, valid_consent, notice, purpose):
        valid_consent.is_free = False
        v = validate_consent_requirements(valid_consent, notice, purpose)
        assert any("free" in s for s in v)

    def test_not_specific_raises_violation(self, valid_consent, notice, purpose):
        valid_consent.is_specific = False
        v = validate_consent_requirements(valid_consent, notice, purpose)
        assert any("specific" in s for s in v)

    def test_purpose_not_in_notice_raises_violation(self, valid_consent, notice, purpose):
        valid_consent.purpose_ids = ["p1", "p_unlisted"]
        v = validate_consent_requirements(valid_consent, notice, purpose)
        assert any("p_unlisted" in s for s in v)

    def test_assert_consent_valid_raises_on_failure(self, valid_consent, notice, purpose):
        valid_consent.is_free = False
        with pytest.raises(ConsentInvalidError) as exc:
            assert_consent_valid(valid_consent, notice, purpose)
        assert "6(1)" in str(exc.value)

    def test_notice_served_after_consent_is_violation(self, valid_consent, notice, purpose):
        valid_consent.given_at = utcnow() - timedelta(hours=1)
        notice_late = NoticeRecord(
            id="n_late",
            principal_id="u1",
            fiduciary_id="f1",
            purpose_ids=["p1"],
            language="en",
            content_hash="late",
            channel="in-app",
            served_at=utcnow(),  # after consent.given_at
        )
        violations = validate_notice_requirements(notice_late, valid_consent)
        assert any("AFTER" in v for v in violations)


class TestPurposeLimitation:
    def test_valid_purpose_passes(self, valid_consent):
        validate_purpose_limitation("p1", valid_consent)  # should not raise

    def test_unknown_purpose_raises(self, valid_consent):
        with pytest.raises(PurposeLimitationError) as exc:
            validate_purpose_limitation("p_unknown", valid_consent)
        assert "6(1)" in str(exc.value)


class TestDataMinimisation:
    def test_within_declared_categories_passes(self, purpose):
        validate_data_minimisation(["cat_name", "cat_dob"], purpose)  # no raise

    def test_excess_category_raises(self, purpose):
        with pytest.raises(DataMinimisationError) as exc:
            validate_data_minimisation(["cat_name", "cat_biometric"], purpose)
        assert "cat_biometric" in str(exc.value)
        assert "6(1)" in str(exc.value)


class TestWithdrawalChannelParity:
    def test_same_channel_no_violation(self, valid_consent):
        valid_consent.withdrawal_channel = "in-app"
        v = validate_withdrawal_channel_parity(valid_consent)
        assert v == []

    def test_harder_channel_is_violation(self, valid_consent):
        valid_consent.withdrawal_channel = "paper"
        v = validate_withdrawal_channel_parity(valid_consent)
        assert any("harder" in s for s in v)

    def test_easier_channel_ok(self, valid_consent):
        valid_consent.consent_channel = "email"
        valid_consent.withdrawal_channel = "in-app"
        v = validate_withdrawal_channel_parity(valid_consent)
        assert v == []

    def test_no_withdrawal_yet_no_violation(self, valid_consent):
        valid_consent.withdrawal_channel = None
        assert validate_withdrawal_channel_parity(valid_consent) == []


# ---------------------------------------------------------------------------
# PolicyRegistry tests
# ---------------------------------------------------------------------------

class TestPolicyRegistry:
    def test_standard_fiduciary_config(self, fiduciary):
        reg = PolicyRegistry()
        cfg = reg.resolve(fiduciary)
        assert cfg.notice_required is True
        assert cfg.sdf_obligations is False
        assert cfg.erasure_obligation is True

    def test_sdf_has_additional_obligations(self):
        reg = PolicyRegistry()
        sdf = DataFiduciaryProfile(
            id="f_sdf", name="Big Tech", fiduciary_class=FiduciaryClass.SIGNIFICANT
        )
        cfg = reg.resolve(sdf)
        assert cfg.sdf_obligations is True

    def test_startup_exempt_profile(self):
        reg = PolicyRegistry()
        startup = DataFiduciaryProfile(
            id="f_s", name="TinyApp", fiduciary_class=FiduciaryClass.STARTUP_EXEMPT
        )
        cfg = reg.resolve(startup)
        assert cfg.notice_required is False
        assert cfg.erasure_obligation is False
        assert cfg.access_right is False

    def test_state_instrumentality_no_erasure(self):
        reg = PolicyRegistry()
        govt = DataFiduciaryProfile(
            id="f_g", name="UIDAI", fiduciary_class=FiduciaryClass.STATE_INSTRUMENTALITY
        )
        cfg = reg.resolve(govt)
        assert cfg.erasure_obligation is False
        assert cfg.notice_required is True

    def test_per_fiduciary_override(self, fiduciary):
        reg = PolicyRegistry()
        custom = PolicyConfig(
            fiduciary_class=FiduciaryClass.STANDARD,
            grievance_response_days=7,
        )
        reg.set_override(fiduciary.id, custom)
        cfg = reg.resolve(fiduciary)
        assert cfg.grievance_response_days == 7

    def test_penalty_schedule_completeness(self):
        reg = PolicyRegistry()
        assert reg.max_penalty("security_safeguard_failure") == 250_00_00_000
        assert reg.max_penalty("breach_notification_failure") == 200_00_00_000
        assert reg.max_penalty("children_data_violation") == 200_00_00_000
        assert reg.max_penalty("sdf_obligation_breach") == 150_00_00_000
        assert reg.max_penalty("data_principal_duty_breach") == 10_000
        assert reg.max_penalty("voluntary_undertaking_breach") is None
        assert reg.max_penalty("other_breach") == 50_00_00_000

    def test_unknown_breach_falls_back_to_other(self):
        reg = PolicyRegistry()
        assert reg.max_penalty("something_random") == 50_00_00_000


# ---------------------------------------------------------------------------
# Exception message tests
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_consent_invalid_carries_section(self):
        e = ConsentInvalidError("bad consent", failed_checks=["not free"])
        assert "6(1)" in str(e)
        assert "not free" in str(e)

    def test_notice_required_carries_section(self):
        e = NoticeRequiredError("u1", "p1")
        assert "5(1)" in str(e)

    def test_purpose_limitation_carries_section(self):
        e = PurposeLimitationError("p_bad", "c1")
        assert "6(1)" in str(e)

    def test_data_minimisation_carries_section(self):
        e = DataMinimisationError(["cat_biometric"])
        assert "6(1)" in str(e)
        assert "cat_biometric" in str(e)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

class TestIds:
    def test_new_id_is_string(self):
        assert isinstance(new_id(), str)

    def test_new_ids_are_unique(self):
        ids = {new_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_ids_sort_chronologically(self):
        import time
        a = new_id()
        time.sleep(0.01)
        b = new_id()
        assert a < b  # ULIDs are lexicographically sortable by time
