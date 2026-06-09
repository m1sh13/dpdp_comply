"""
Tests for dpdp_comply.consent_engine

Covers every consent lifecycle path including the critical invariant:
existing data is treated as NOT consented by default.

Sections exercised: §4, §5(1), §5(2), §6(1)–(10), §8(8), §9(1).
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta

from core.enums import (
    LawfulBasis, ProcessingOperation, FiduciaryClass, ConsentState, DataSensitivity,
)
from core.models import (
    DataPrincipal, ProcessingPurpose, DataFiduciaryProfile,
)
from core.exceptions import (
    ChildConsentError, ConsentInvalidError, NoticeRequiredError,
)
from consent_engine.engine import ConsentEngine
from consent_engine.store import InMemoryConsentStore
from consent_engine.dtos import ConsentRequest, WithdrawalRequest
from consent_engine.audit import AuditEventType


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def utcnow():
    return datetime.now(timezone.utc)


@pytest.fixture
def store():
    return InMemoryConsentStore()


@pytest.fixture
def engine(store):
    return ConsentEngine(store=store)


@pytest.fixture
def fiduciary():
    return DataFiduciaryProfile(
        id="f1", name="Acme Bank", fiduciary_class=FiduciaryClass.STANDARD
    )


@pytest.fixture
def adult_principal():
    return DataPrincipal(id="u1", external_ref="user_001")


@pytest.fixture
def child_principal():
    return DataPrincipal(id="u_child", is_child=True, guardian_id="g1")


@pytest.fixture
def kyc_purpose():
    return ProcessingPurpose(
        id="p_kyc",
        name="KYC verification",
        description="Verify customer identity per RBI guidelines.",
        lawful_basis=LawfulBasis.CONSENT,
        operations=[ProcessingOperation.COLLECTION, ProcessingOperation.STORAGE],
        data_categories=["cat_name", "cat_dob", "cat_address"],
    )


@pytest.fixture
def marketing_purpose():
    return ProcessingPurpose(
        id="p_mkt",
        name="Marketing communications",
        description="Send promotional emails.",
        lawful_basis=LawfulBasis.CONSENT,
        operations=[ProcessingOperation.USE, ProcessingOperation.DISCLOSURE],
        data_categories=["cat_email"],
    )


def _serve_notice_and_consent(
    engine, fiduciary, principal, purposes, channel="in-app"
):
    """Helper: serve notice then seek valid consent. Returns (notice, consent)."""
    purpose_ids = [p.id for p in purposes]
    data_cat_ids = [c for p in purposes for c in p.data_categories]

    notice = engine.serve_notice(
        principal_id=principal.id,
        fiduciary_id=fiduciary.id,
        purpose_ids=purpose_ids,
        notice_text="This notice describes your data and rights.",
        channel=channel,
    )
    req = ConsentRequest(
        principal_id=principal.id,
        fiduciary_id=fiduciary.id,
        notice_id=notice.id,
        purpose_ids=purpose_ids,
        data_category_ids=data_cat_ids,
        consent_channel=channel,
        collected_freely=True,
        collected_specifically=True,
        collected_via_affirmative_action=True,
        no_rights_waived=True,
    )
    consent = engine.seek_consent(req, principal, purposes, fiduciary)
    return notice, consent


# ===========================================================================
# 1. Pre-Act data default — existing data is NOT consented
# ===========================================================================

class TestPreActDefault:

    def test_unknown_principal_denied(self, engine, fiduciary):
        """A principal that has never interacted is denied by default."""
        assert engine.check_authorised("ghost_user", fiduciary.id, "p_kyc") is False

    def test_pre_act_bulk_flag_denies_processing(self, engine, store, fiduciary):
        """load_pre_act_data() marks principals as unconsented."""
        n = engine.load_pre_act_data([
            ("u_old1", fiduciary.id, ["p_kyc"]),
            ("u_old2", fiduciary.id, ["p_kyc"]),
        ])
        assert n == 2
        assert engine.check_authorised("u_old1", fiduciary.id, "p_kyc") is False
        assert engine.check_authorised("u_old2", fiduciary.id, "p_kyc") is False

    def test_pre_act_principal_allowed_after_fresh_consent(
        self, engine, store, fiduciary, adult_principal, kyc_purpose
    ):
        """Pre-Act principal is unblocked once a fresh consent is collected."""
        engine.load_pre_act_data([(adult_principal.id, fiduciary.id, [kyc_purpose.id])])
        assert engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id) is False

        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])

        assert engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id) is True

    def test_pre_act_flag_audit_event_written(self, engine, fiduciary):
        engine.load_pre_act_data([("u_old", fiduciary.id, ["p1"])])
        trail = engine.get_audit_trail(principal_id="u_old")
        assert any(e.event_type == AuditEventType.PRE_ACT_DATA_FLAGGED for e in trail)

    def test_retrospective_notice_does_not_activate_consent(
        self, engine, store, fiduciary, adult_principal, kyc_purpose
    ):
        """§5(2): serving a retrospective notice must NOT activate processing."""
        engine.load_pre_act_data([(adult_principal.id, fiduciary.id, [kyc_purpose.id])])
        engine.serve_retrospective_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="We hold your data. Here are your rights.",
            channel="email",
        )
        # Still blocked — no affirmative action taken
        assert engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id) is False

    def test_retro_notice_audit_event_type(self, engine, fiduciary, adult_principal, kyc_purpose):
        engine.serve_retrospective_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="Retrospective notice text.",
            channel="email",
        )
        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        assert any(e.event_type == AuditEventType.NOTICE_SERVED_RETRO for e in trail)


# ===========================================================================
# 2. Notice requirements — §5(1)
# ===========================================================================

class TestNoticeRequirements:

    def test_seek_consent_without_notice_raises(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Consent without a prior notice must raise NoticeRequiredError. §5(1)."""
        req = ConsentRequest(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            notice_id="nonexistent_notice",
            purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True,
            collected_specifically=True,
            collected_via_affirmative_action=True,
            no_rights_waived=True,
        )
        with pytest.raises(NoticeRequiredError) as exc:
            engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)
        assert "5(1)" in str(exc.value)

    def test_notice_content_is_hashed(self, engine, fiduciary, adult_principal, kyc_purpose):
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="Notice text here.",
            channel="web",
        )
        import hashlib
        expected = hashlib.sha256(b"Notice text here.").hexdigest()
        assert notice.content_hash == expected

    def test_notice_audit_event_written(self, engine, fiduciary, adult_principal, kyc_purpose):
        engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...",
            channel="in-app",
        )
        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        assert any(e.event_type == AuditEventType.NOTICE_SERVED for e in trail)

    def test_notice_is_immutable_after_serving(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...",
            channel="in-app",
        )
        with pytest.raises(Exception):
            notice.language = "hi"


# ===========================================================================
# 3. §6(1) — Consent validation
# ===========================================================================

class TestConsentValidation:

    def test_valid_consent_returns_active_record(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        assert consent.state == ConsentState.ACTIVE
        assert consent.is_valid_for_processing() is True

    def test_consent_never_starts_active(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Consent must begin PENDING — no record can skip straight to ACTIVE."""
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        trail = engine.get_audit_trail(consent_id=consent.id)
        event_types = [e.event_type for e in trail]
        assert AuditEventType.CONSENT_SOUGHT in event_types
        assert AuditEventType.CONSENT_GIVEN in event_types
        # SOUGHT must come before GIVEN
        sought_idx = event_types.index(AuditEventType.CONSENT_SOUGHT)
        given_idx = event_types.index(AuditEventType.CONSENT_GIVEN)
        assert sought_idx < given_idx

    def test_not_free_raises_consent_invalid(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            notice_id=notice.id,
            purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=False,        # ← fails §6(1)
            collected_specifically=True,
            collected_via_affirmative_action=True,
            no_rights_waived=True,
        )
        with pytest.raises(ConsentInvalidError) as exc:
            engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)
        assert "free" in str(exc.value).lower()

    def test_pre_ticked_box_rejected(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """collected_via_affirmative_action=False simulates a pre-ticked box. §6(1)."""
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            notice_id=notice.id,
            purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True,
            collected_specifically=True,
            collected_via_affirmative_action=False,    # ← pre-ticked
            no_rights_waived=True,
        )
        with pytest.raises(ConsentInvalidError):
            engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)

    def test_consent_for_purpose_not_in_notice_fails(
        self, engine, fiduciary, adult_principal, kyc_purpose, marketing_purpose
    ):
        """Consent covering a purpose the notice didn't describe must fail. §5(1)(i)."""
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],           # notice only covers KYC
            notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            notice_id=notice.id,
            purpose_ids=[kyc_purpose.id, marketing_purpose.id],  # consent adds marketing
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True,
            collected_specifically=True,
            collected_via_affirmative_action=True,
            no_rights_waived=True,
        )
        with pytest.raises(ConsentInvalidError):
            engine.seek_consent(req, adult_principal, [kyc_purpose, marketing_purpose], fiduciary)

    def test_excess_data_categories_fail_minimisation(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Consenting to data not needed for the purpose violates §6(1) minimisation."""
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            notice_id=notice.id,
            purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories + ["cat_biometric"],  # excess
            consent_channel="in-app",
            collected_freely=True,
            collected_specifically=True,
            collected_via_affirmative_action=True,
            no_rights_waived=True,
        )
        with pytest.raises(ConsentInvalidError):
            engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)

    def test_failed_consent_audit_event_written(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=False, collected_specifically=True,
            collected_via_affirmative_action=True, no_rights_waived=True,
        )
        with pytest.raises(ConsentInvalidError):
            engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)

        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        assert any(e.event_type == AuditEventType.CONSENT_VALIDATION_FAIL for e in trail)

    def test_multiple_purposes_in_one_consent(
        self, engine, fiduciary, adult_principal, kyc_purpose, marketing_purpose
    ):
        """Multiple purposes can be consented to if the notice covers all of them."""
        _, consent = _serve_notice_and_consent(
            engine, fiduciary, adult_principal, [kyc_purpose, marketing_purpose]
        )
        assert kyc_purpose.id in consent.purpose_ids
        assert marketing_purpose.id in consent.purpose_ids
        assert consent.state == ConsentState.ACTIVE


# ===========================================================================
# 4. §6(2) — Rights waiver in consent is invalid
# ===========================================================================

class TestRightsWaiver:

    def test_rights_waiver_blocks_consent(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Consent bundling a rights waiver must be refused. §6(2)."""
        notice = engine.serve_notice(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id], notice_text="...", channel="web",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="web",
            collected_freely=True, collected_specifically=True,
            collected_via_affirmative_action=True,
            no_rights_waived=False,     # ← waiver detected
        )
        with pytest.raises(ConsentInvalidError):
            engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)


# ===========================================================================
# 5. §6(4) — Withdrawal
# ===========================================================================

class TestWithdrawal:

    def test_withdrawal_transitions_to_withdrawn(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        req = WithdrawalRequest(
            consent_id=consent.id,
            principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        )
        updated = engine.withdraw_consent(req)
        assert updated.state == ConsentState.WITHDRAWN
        assert updated.withdrawn_at is not None

    def test_processing_denied_after_withdrawal(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.withdraw_consent(WithdrawalRequest(
            consent_id=consent.id,
            principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        ))
        assert engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id) is False

    def test_withdrawal_is_idempotent(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Withdrawing an already-withdrawn consent is a no-op, not an error."""
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        req = WithdrawalRequest(
            consent_id=consent.id, principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        )
        engine.withdraw_consent(req)
        result = engine.withdraw_consent(req)  # second call
        assert result.state == ConsentState.WITHDRAWN

    def test_withdrawal_channel_harder_than_consent_emits_warning(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """§6(4): withdrawal via paper when consent was in-app should trigger a warning audit event."""
        _, consent = _serve_notice_and_consent(
            engine, fiduciary, adult_principal, [kyc_purpose], channel="in-app"
        )
        engine.withdraw_consent(WithdrawalRequest(
            consent_id=consent.id, principal_id=adult_principal.id,
            withdrawal_channel="paper",     # harder channel
        ))
        trail = engine.get_audit_trail(consent_id=consent.id)
        assert any(e.event_type == AuditEventType.WITHDRAWAL_CHANNEL_WARN for e in trail)

    def test_withdrawal_emits_processors_notified_event(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """§6(6): processor notification event must be logged."""
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.withdraw_consent(WithdrawalRequest(
            consent_id=consent.id, principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        ))
        trail = engine.get_audit_trail(consent_id=consent.id)
        assert any(e.event_type == AuditEventType.PROCESSORS_NOTIFIED for e in trail)

    def test_withdrawal_calls_on_withdrawal_hook(
        self, fiduciary, adult_principal, kyc_purpose
    ):
        """§6(6): the on_withdrawal hook must be called once with the consent record."""
        calls = []
        eng = ConsentEngine(on_withdrawal=lambda c: calls.append(c.id))
        _, consent = _serve_notice_and_consent(eng, fiduciary, adult_principal, [kyc_purpose])
        eng.withdraw_consent(WithdrawalRequest(
            consent_id=consent.id, principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        ))
        assert calls == [consent.id]

    def test_withdrawal_wrong_principal_raises(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        with pytest.raises(ValueError, match="principal_id mismatch"):
            engine.withdraw_consent(WithdrawalRequest(
                consent_id=consent.id,
                principal_id="someone_else",
                withdrawal_channel="in-app",
            ))

    def test_withdrawal_unknown_consent_raises(self, engine, adult_principal):
        with pytest.raises(ValueError, match="not found"):
            engine.withdraw_consent(WithdrawalRequest(
                consent_id="nonexistent",
                principal_id=adult_principal.id,
                withdrawal_channel="in-app",
            ))


# ===========================================================================
# 6. §8(8) — Consent expiry (purpose no longer served)
# ===========================================================================

class TestConsentExpiry:

    def test_expire_consent_transitions_to_expired(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        updated = engine.expire_consent(consent.id)
        assert updated.state == ConsentState.EXPIRED
        assert updated.expired_at is not None

    def test_processing_denied_after_expiry(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.expire_consent(consent.id)
        assert engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id) is False

    def test_expire_audit_event_written(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.expire_consent(consent.id)
        trail = engine.get_audit_trail(consent_id=consent.id)
        assert any(e.event_type == AuditEventType.CONSENT_EXPIRED for e in trail)

    def test_expire_on_withdrawn_consent_is_noop(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.withdraw_consent(WithdrawalRequest(
            consent_id=consent.id, principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        ))
        result = engine.expire_consent(consent.id)
        assert result.state == ConsentState.WITHDRAWN   # unchanged

    def test_expire_unknown_consent_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.expire_consent("nonexistent_id")


# ===========================================================================
# 7. §9(1) — Children's data
# ===========================================================================

class TestChildrenData:

    def test_child_without_guardian_raises(
        self, engine, fiduciary, kyc_purpose
    ):
        """§9(1): child principal without guardian_id must raise ChildConsentError."""
        # DataPrincipal model enforces guardian_id at construction,
        # so we bypass it and test the engine guard directly.
        child_no_guardian = DataPrincipal.model_construct(
            id="u_bad_child", is_child=True, guardian_id=None
        )
        notice = engine.serve_notice(
            principal_id=child_no_guardian.id,
            fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id],
            notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=child_no_guardian.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True, collected_specifically=True,
            collected_via_affirmative_action=True, no_rights_waived=True,
        )
        with pytest.raises(ChildConsentError) as exc:
            engine.seek_consent(req, child_no_guardian, [kyc_purpose], fiduciary)
        assert "9(1)" in str(exc.value)

    def test_child_with_guardian_id_proceeds(
        self, engine, fiduciary, child_principal, kyc_purpose
    ):
        """§9(1): child with guardian_id set should allow consent to proceed."""
        _, consent = _serve_notice_and_consent(engine, fiduciary, child_principal, [kyc_purpose])
        assert consent.state == ConsentState.ACTIVE

    def test_child_consent_records_guardian_id(
        self, engine, fiduciary, child_principal, kyc_purpose
    ):
        _, consent = _serve_notice_and_consent(engine, fiduciary, child_principal, [kyc_purpose])
        assert consent.metadata.get("guardian_id") == child_principal.guardian_id


# ===========================================================================
# 8. §6(7) — Consent Manager flow
# ===========================================================================

class TestConsentManager:

    def test_consent_via_consent_manager_records_cm_id(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id], notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True, collected_specifically=True,
            collected_via_affirmative_action=True, no_rights_waived=True,
            consent_manager_id="cm_reg_001",
        )
        consent = engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)
        assert consent.consent_manager_id == "cm_reg_001"

    def test_cm_delegation_audit_event_written(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id], notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True, collected_specifically=True,
            collected_via_affirmative_action=True, no_rights_waived=True,
            consent_manager_id="cm_reg_001",
        )
        consent = engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)
        trail = engine.get_audit_trail(consent_id=consent.id)
        assert any(e.event_type == AuditEventType.CM_CONSENT_DELEGATED for e in trail)


# ===========================================================================
# 9. §6(10) — Proof obligation
# ===========================================================================

class TestProofObligation:

    def test_proof_reference_stored_on_consent(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id], notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True, collected_specifically=True,
            collected_via_affirmative_action=True, no_rights_waived=True,
            proof_reference="signed_jwt_abc123",
        )
        consent = engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)
        assert consent.proof_reference == "signed_jwt_abc123"

    def test_consent_given_audit_carries_proof_reference(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        notice = engine.serve_notice(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            purpose_ids=[kyc_purpose.id], notice_text="...", channel="in-app",
        )
        req = ConsentRequest(
            principal_id=adult_principal.id, fiduciary_id=fiduciary.id,
            notice_id=notice.id, purpose_ids=[kyc_purpose.id],
            data_category_ids=kyc_purpose.data_categories,
            consent_channel="in-app",
            collected_freely=True, collected_specifically=True,
            collected_via_affirmative_action=True, no_rights_waived=True,
            proof_reference="ref_xyz",
        )
        consent = engine.seek_consent(req, adult_principal, [kyc_purpose], fiduciary)
        trail = engine.get_audit_trail(consent_id=consent.id)
        given_event = next(e for e in trail if e.event_type == AuditEventType.CONSENT_GIVEN)
        assert "ref_xyz" in given_event.detail


# ===========================================================================
# 10. Authorisation gate — check_authorised()
# ===========================================================================

class TestAuthorisationGate:

    def test_authorised_after_valid_consent(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        assert engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id) is True

    def test_not_authorised_for_unconsented_purpose(
        self, engine, fiduciary, adult_principal, kyc_purpose, marketing_purpose
    ):
        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        # marketing_purpose was never consented to
        assert engine.check_authorised(adult_principal.id, fiduciary.id, marketing_purpose.id) is False

    def test_processing_denied_audit_event(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id)
        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        assert any(e.event_type == AuditEventType.PROCESSING_DENIED for e in trail)

    def test_processing_allowed_audit_event(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id)
        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        assert any(e.event_type == AuditEventType.PROCESSING_ALLOWED for e in trail)

    def test_different_fiduciary_cannot_use_consent(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Consent is scoped to a specific fiduciary — another fiduciary cannot use it."""
        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        assert engine.check_authorised(adult_principal.id, "other_fiduciary", kyc_purpose.id) is False


# ===========================================================================
# 11. Audit trail integrity
# ===========================================================================

class TestAuditTrail:

    def test_trail_is_chronologically_sorted(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        times = [e.occurred_at for e in trail]
        assert times == sorted(times)

    def test_audit_events_are_immutable(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        with pytest.raises(Exception):
            trail[0].detail = "tampered"

    def test_full_lifecycle_audit_sequence(
        self, engine, fiduciary, adult_principal, kyc_purpose
    ):
        """Happy path: notice → consent → allowed → withdraw → denied."""
        _, consent = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id)
        engine.withdraw_consent(WithdrawalRequest(
            consent_id=consent.id, principal_id=adult_principal.id,
            withdrawal_channel="in-app",
        ))
        engine.check_authorised(adult_principal.id, fiduciary.id, kyc_purpose.id)

        trail = engine.get_audit_trail(principal_id=adult_principal.id)
        event_types = [e.event_type for e in trail]

        assert AuditEventType.NOTICE_SERVED in event_types
        assert AuditEventType.CONSENT_SOUGHT in event_types
        assert AuditEventType.CONSENT_GIVEN in event_types
        assert AuditEventType.PROCESSING_ALLOWED in event_types
        assert AuditEventType.CONSENT_WITHDRAWN in event_types
        assert AuditEventType.PROCESSING_DENIED in event_types

    def test_filter_by_consent_id(
        self, engine, fiduciary, adult_principal, kyc_purpose, marketing_purpose
    ):
        _, c1 = _serve_notice_and_consent(engine, fiduciary, adult_principal, [kyc_purpose])
        principal2 = DataPrincipal(id="u2")
        _, c2 = _serve_notice_and_consent(engine, fiduciary, principal2, [marketing_purpose])

        trail_c1 = engine.get_audit_trail(consent_id=c1.id)
        assert all(e.consent_id == c1.id for e in trail_c1 if e.consent_id)
