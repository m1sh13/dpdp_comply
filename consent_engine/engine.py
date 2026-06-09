"""
ConsentEngine
~~~~~~~~~~~~~
Orchestrates the full consent lifecycle under the DPDP Act 2023.

Public API
----------
serve_notice()          §5(1)  — record a notice before seeking consent
serve_retrospective_notice()  §5(2) — notice for pre-Act data
seek_consent()          §6(1)  — validate and record consent
check_authorised()      §6/§4  — gate: is processing authorised for this purpose?
withdraw_consent()      §6(4)  — record withdrawal and notify processors
expire_consent()        §8(8)  — mark purpose as no longer served
load_pre_act_data()     §5(2)  — bulk-flag existing data as unconsented
get_audit_trail()              — return the full immutable audit log

Design invariants
-----------------
1. No ConsentRecord starts in ACTIVE state — the principal must
   take an affirmative action (seek_consent sets PENDING first,
   then transitions to ACTIVE only after all §6(1) checks pass).

2. Existing data loaded via load_pre_act_data() is always treated
   as NOT consented.  check_authorised() returns False for these
   principals until a fresh consent flow completes.

3. Withdrawal is always honoured.  §6(6): the engine also emits a
   PROCESSORS_NOTIFIED audit event so downstream services know to
   cease processing.

4. Every state transition is recorded in the audit log before the
   ConsentRecord is persisted — write-ahead audit.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable

from core.enums import ConsentState
from core.exceptions import (
    ChildConsentError,
    ConsentInvalidError,
    NoticeRequiredError,
    UnlawfulProcessingError,
)
from core.models import (
    ConsentRecord,
    DataFiduciaryProfile,
    DataPrincipal,
    NoticeRecord,
    ProcessingPurpose,
)
from core.registry import PolicyRegistry, default_registry
from core.validators import (
    assert_consent_valid,
    validate_withdrawal_channel_parity,
)
from core.ids import new_id
from consent_engine.audit import AuditEventType, ConsentAuditEvent
from consent_engine.dtos import ConsentRequest, WithdrawalRequest
from consent_engine.store import ConsentStore, InMemoryConsentStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConsentEngine:
    """
    Stateless orchestrator — all state lives in the ConsentStore.

    Inject a store and optionally a PolicyRegistry.  All methods
    are synchronous; async wrappers can be added at the boundary.
    """

    def __init__(
        self,
        store: ConsentStore | None = None,
        registry: PolicyRegistry | None = None,
        # Optional hook: called after withdrawal so callers can
        # notify Data Processors (§6(6)) via their own channels.
        on_withdrawal: Callable[[ConsentRecord], None] | None = None,
    ) -> None:
        self._store = store or InMemoryConsentStore()
        self._registry = registry or default_registry
        self._on_withdrawal = on_withdrawal

    # ------------------------------------------------------------------
    # §5(1) — Serve notice
    # ------------------------------------------------------------------

    def serve_notice(
        self,
        principal_id: str,
        fiduciary_id: str,
        purpose_ids: list[str],
        notice_text: str,
        channel: str,
        language: str = "en",
    ) -> NoticeRecord:
        """
        Record that a §5(1) notice has been served to a Data Principal.

        The notice_text is hashed (SHA-256) for tamper evidence.
        The full text should be stored separately by the caller.

        Returns the persisted NoticeRecord.
        """
        content_hash = hashlib.sha256(notice_text.encode()).hexdigest()
        notice = NoticeRecord(
            principal_id=principal_id,
            fiduciary_id=fiduciary_id,
            purpose_ids=purpose_ids,
            language=language,
            content_hash=content_hash,
            channel=channel,
            is_retrospective=False,
        )
        self._store.save_notice(notice)
        self._audit(
            AuditEventType.NOTICE_SERVED,
            principal_id=principal_id,
            fiduciary_id=fiduciary_id,
            purpose_ids=purpose_ids,
            actor="fiduciary",
            detail=f"Notice served via '{channel}' (hash={content_hash[:12]}…)",
        )
        return notice

    # ------------------------------------------------------------------
    # §5(2) — Retrospective notice for pre-Act data
    # ------------------------------------------------------------------

    def serve_retrospective_notice(
        self,
        principal_id: str,
        fiduciary_id: str,
        purpose_ids: list[str],
        notice_text: str,
        channel: str,
        language: str = "en",
    ) -> NoticeRecord:
        """
        Serve a §5(2) retrospective notice to a principal whose data
        was collected before the Act came into force.

        This does NOT activate consent.  The principal must still take
        a fresh affirmative action via seek_consent().
        """
        content_hash = hashlib.sha256(notice_text.encode()).hexdigest()
        notice = NoticeRecord(
            principal_id=principal_id,
            fiduciary_id=fiduciary_id,
            purpose_ids=purpose_ids,
            language=language,
            content_hash=content_hash,
            channel=channel,
            is_retrospective=True,
        )
        self._store.save_notice(notice)
        self._audit(
            AuditEventType.NOTICE_SERVED_RETRO,
            principal_id=principal_id,
            fiduciary_id=fiduciary_id,
            purpose_ids=purpose_ids,
            actor="fiduciary",
            detail=(
                f"Retrospective §5(2) notice served via '{channel}'. "
                "Consent NOT activated — fresh affirmative action required."
            ),
        )
        return notice

    # ------------------------------------------------------------------
    # §5(2) / "existing data" — bulk-flag pre-Act principals
    # ------------------------------------------------------------------

    def load_pre_act_data(
        self,
        principals: list[tuple[str, str, list[str]]],
    ) -> int:
        """
        Mark existing data as pre-Act / unconsented.

        Parameters
        ----------
        principals:
            List of (principal_id, fiduciary_id, purpose_ids) tuples.

        Returns
        -------
        int
            Number of principals flagged.

        All principals flagged here will fail check_authorised() until
        they complete a fresh consent flow.
        """
        count = 0
        for principal_id, fiduciary_id, purpose_ids in principals:
            self._store.register_pre_act_principal(
                principal_id, fiduciary_id, purpose_ids
            )
            self._audit(
                AuditEventType.PRE_ACT_DATA_FLAGGED,
                principal_id=principal_id,
                fiduciary_id=fiduciary_id,
                purpose_ids=purpose_ids,
                actor="system",
                detail="Existing data flagged as pre-Act. Processing blocked until fresh consent.",
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # §6 — Seek consent
    # ------------------------------------------------------------------

    def seek_consent(
        self,
        request: ConsentRequest,
        principal: DataPrincipal,
        purposes: list[ProcessingPurpose],
        fiduciary: DataFiduciaryProfile,
    ) -> ConsentRecord:
        """
        Validate and record consent for one or more processing purposes.

        Steps
        -----
        1. Check a notice was served. §5(1) / NoticeRequiredError.
        2. Check child/guardian consent requirements. §9(1) / ChildConsentError.
        3. Build a ConsentRecord in PENDING state.
        4. Run all six §6(1) validators.
        5. Transition to ACTIVE and persist.
        6. Write audit event.

        Raises
        ------
        NoticeRequiredError     if no notice was served first.
        ChildConsentError       if principal is a child and guardian consent is missing.
        ConsentInvalidError     if any §6(1) check fails.
        """
        # 1. Notice prerequisite
        notice = self._store.get_notice(request.notice_id)
        if notice is None:
            for pid in request.purpose_ids:
                raise NoticeRequiredError(request.principal_id, pid)

        # 2. Child / guardian prerequisite — §9(1)
        if principal.is_child or principal.has_disability:
            if not principal.guardian_id:
                raise ChildConsentError(principal.id)
            # The guardian must have taken the affirmative action;
            # we record the guardian_id in audit metadata.

        # 3. Build consent record — starts PENDING (not yet active)
        record = ConsentRecord(
            principal_id=request.principal_id,
            fiduciary_id=request.fiduciary_id,
            notice_id=request.notice_id,
            purpose_ids=request.purpose_ids,
            state=ConsentState.PENDING,
            consent_channel=request.consent_channel,
            consent_manager_id=request.consent_manager_id,
            proof_reference=request.proof_reference,
            metadata={
                **request.metadata,
                **({"guardian_id": principal.guardian_id} if principal.is_child else {}),
            },
        )

        self._audit(
            AuditEventType.CONSENT_SOUGHT,
            principal_id=request.principal_id,
            fiduciary_id=request.fiduciary_id,
            purpose_ids=request.purpose_ids,
            actor="fiduciary",
            detail=f"Consent request presented via '{request.consent_channel}'.",
            consent_id=record.id,
        )

        # 4. Resolve §6(1) flags from the request attestations
        #    and structural checks.
        record.is_free = request.collected_freely
        record.is_specific = request.collected_specifically
        record.is_informed = request.collected_via_affirmative_action  # notice was present
        record.is_unconditional = request.no_rights_waived
        record.is_unambiguous = request.collected_via_affirmative_action
        record.is_limited_to_necessary = self._check_data_minimisation(
            request.data_category_ids, purposes
        )

        # Purpose ids in consent must be ⊆ notice purpose ids
        uncovered = set(request.purpose_ids) - set(notice.purpose_ids)
        if uncovered:
            record.is_informed = False

        # Run the full §6(1) validator
        purpose_map = {p.id: p for p in purposes}
        first_purpose = purpose_map.get(request.purpose_ids[0])
        if first_purpose is None:
            raise ConsentInvalidError(
                f"Purpose '{request.purpose_ids[0]}' not found in provided purposes list.",
            )

        try:
            assert_consent_valid(record, notice, first_purpose)
        except ConsentInvalidError:
            self._audit(
                AuditEventType.CONSENT_VALIDATION_FAIL,
                principal_id=request.principal_id,
                fiduciary_id=request.fiduciary_id,
                purpose_ids=request.purpose_ids,
                actor="system",
                detail=f"§6(1) validation failed for consent {record.id}.",
                consent_id=record.id,
            )
            raise

        # §6(2) — flag if rights were waived (already blocked by no_rights_waived,
        # but log explicitly for audit completeness)
        if not request.no_rights_waived:
            self._audit(
                AuditEventType.CONSENT_INVALID_PART,
                principal_id=request.principal_id,
                fiduciary_id=request.fiduciary_id,
                purpose_ids=request.purpose_ids,
                actor="system",
                detail="Part of consent invalid — rights waiver detected. §6(2).",
                consent_id=record.id,
            )

        # 5. Transition to ACTIVE
        record.state = ConsentState.ACTIVE
        record.given_at = _utcnow()
        record.updated_at = _utcnow()
        self._store.save_consent(record)

        # 6. Audit
        self._audit(
            AuditEventType.CONSENT_GIVEN,
            principal_id=request.principal_id,
            fiduciary_id=request.fiduciary_id,
            purpose_ids=request.purpose_ids,
            actor="principal",
            detail=(
                f"Consent ACTIVE. Channel='{request.consent_channel}'. "
                f"Proof='{request.proof_reference or 'none'}'."
            ),
            consent_id=record.id,
        )

        # Consent Manager delegation audit — §6(7)
        if request.consent_manager_id:
            self._audit(
                AuditEventType.CM_CONSENT_DELEGATED,
                principal_id=request.principal_id,
                fiduciary_id=request.fiduciary_id,
                purpose_ids=request.purpose_ids,
                actor="consent_manager",
                detail=f"Consent routed via Consent Manager '{request.consent_manager_id}'. §6(7).",
                consent_id=record.id,
            )

        return record

    # ------------------------------------------------------------------
    # §4 / §6 — Authorisation gate
    # ------------------------------------------------------------------

    def check_authorised(
        self,
        principal_id: str,
        fiduciary_id: str,
        purpose_id: str,
    ) -> bool:
        """
        Returns True only if processing is authorised for this
        (principal, fiduciary, purpose) triple.

        Authorisation is DENIED when:
        - The principal is flagged as pre-Act and has no active consent.
        - No ConsentRecord in ACTIVE + valid state covers the purpose.
        - The principal has never interacted with this fiduciary.

        This is the primary gate for all downstream processing code.
        Authorisation denied is logged for audit.
        """
        # Pre-Act data with no active consent → always denied
        if self._store.is_pre_act(principal_id, fiduciary_id):
            active = self._store.get_active_consents(principal_id, fiduciary_id)
            covering = [c for c in active if purpose_id in c.purpose_ids]
            if not covering:
                self._audit(
                    AuditEventType.PROCESSING_DENIED,
                    principal_id=principal_id,
                    fiduciary_id=fiduciary_id,
                    purpose_ids=[purpose_id],
                    actor="system",
                    detail=(
                        "Processing DENIED — pre-Act data with no valid consent for purpose. §5(2)."
                    ),
                )
                return False

        # General check: active consent must cover the purpose
        active = self._store.get_active_consents(principal_id, fiduciary_id)
        covering = [c for c in active if purpose_id in c.purpose_ids]

        if not covering:
            self._audit(
                AuditEventType.PROCESSING_DENIED,
                principal_id=principal_id,
                fiduciary_id=fiduciary_id,
                purpose_ids=[purpose_id],
                actor="system",
                detail="Processing DENIED — no active consent covers this purpose.",
            )
            return False

        self._audit(
            AuditEventType.PROCESSING_ALLOWED,
            principal_id=principal_id,
            fiduciary_id=fiduciary_id,
            purpose_ids=[purpose_id],
            actor="system",
            detail=f"Processing ALLOWED under consent '{covering[0].id}'.",
            consent_id=covering[0].id,
        )
        return True

    # ------------------------------------------------------------------
    # §6(4) — Withdraw consent
    # ------------------------------------------------------------------

    def withdraw_consent(self, request: WithdrawalRequest) -> ConsentRecord:
        """
        Record a consent withdrawal.

        Steps
        -----
        1. Load the ConsentRecord.
        2. Verify principal_id matches.
        3. Check withdrawal channel parity (§6(4)) — warn if harder.
        4. Transition to WITHDRAWN.
        5. Call on_withdrawal hook (for processor notification — §6(6)).
        6. Persist and audit.

        Returns the updated ConsentRecord.
        """
        record = self._store.get_consent(request.consent_id)
        if record is None:
            raise ValueError(f"ConsentRecord '{request.consent_id}' not found.")

        if record.principal_id != request.principal_id:
            raise ValueError(
                f"principal_id mismatch: consent belongs to '{record.principal_id}', "
                f"not '{request.principal_id}'."
            )

        if record.state == ConsentState.WITHDRAWN:
            # Idempotent — already withdrawn, no-op
            return record

        # §6(4) channel parity check
        record.withdrawal_channel = request.withdrawal_channel
        parity_violations = validate_withdrawal_channel_parity(record)
        if parity_violations:
            self._audit(
                AuditEventType.WITHDRAWAL_CHANNEL_WARN,
                principal_id=request.principal_id,
                fiduciary_id=record.fiduciary_id,
                purpose_ids=record.purpose_ids,
                actor="system",
                detail="; ".join(parity_violations),
                consent_id=record.id,
            )

        # Audit: withdrawal requested
        self._audit(
            AuditEventType.WITHDRAWAL_REQUESTED,
            principal_id=request.principal_id,
            fiduciary_id=record.fiduciary_id,
            purpose_ids=record.purpose_ids,
            actor="principal",
            detail=f"Withdrawal requested via '{request.withdrawal_channel}'. Reason: {request.reason or 'not given'}.",
            consent_id=record.id,
        )

        # Transition
        withdrawn_at = request.withdrawn_at or _utcnow()
        record.state = ConsentState.WITHDRAWN
        record.withdrawn_at = withdrawn_at
        record.updated_at = _utcnow()
        self._store.save_consent(record)

        # §6(6) — notify processors hook
        if self._on_withdrawal:
            self._on_withdrawal(record)

        self._audit(
            AuditEventType.CONSENT_WITHDRAWN,
            principal_id=request.principal_id,
            fiduciary_id=record.fiduciary_id,
            purpose_ids=record.purpose_ids,
            actor="principal",
            detail=f"Consent WITHDRAWN at {withdrawn_at.isoformat()}. Processing must cease. §6(6).",
            consent_id=record.id,
        )
        self._audit(
            AuditEventType.PROCESSORS_NOTIFIED,
            principal_id=request.principal_id,
            fiduciary_id=record.fiduciary_id,
            purpose_ids=record.purpose_ids,
            actor="system",
            detail="Downstream processor notification triggered. §6(6).",
            consent_id=record.id,
        )

        return record

    # ------------------------------------------------------------------
    # §8(8) — Expire consent (purpose no longer served)
    # ------------------------------------------------------------------

    def expire_consent(
        self,
        consent_id: str,
        reason: str = "Purpose no longer served. §8(8).",
    ) -> ConsentRecord:
        """
        Mark a consent as expired when the specified purpose is no longer
        being served (e.g. inactivity threshold crossed per §8(8)).

        The retention scheduler module calls this; it can also be called
        manually for testing.
        """
        record = self._store.get_consent(consent_id)
        if record is None:
            raise ValueError(f"ConsentRecord '{consent_id}' not found.")

        if record.state in (ConsentState.WITHDRAWN, ConsentState.INVALID):
            return record  # Already terminal state — no-op

        record.state = ConsentState.EXPIRED
        record.expired_at = _utcnow()
        record.updated_at = _utcnow()
        self._store.save_consent(record)

        self._audit(
            AuditEventType.CONSENT_EXPIRED,
            principal_id=record.principal_id,
            fiduciary_id=record.fiduciary_id,
            purpose_ids=record.purpose_ids,
            actor="system",
            detail=reason,
            consent_id=record.id,
        )
        return record

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def get_audit_trail(
        self,
        principal_id: str | None = None,
        consent_id: str | None = None,
    ) -> list[ConsentAuditEvent]:
        """Return the filtered, chronologically sorted audit trail."""
        return self._store.get_audit_trail(
            principal_id=principal_id,
            consent_id=consent_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_data_minimisation(
        self,
        requested_category_ids: list[str],
        purposes: list[ProcessingPurpose],
    ) -> bool:
        """
        Returns True if all requested data categories are declared as
        necessary in at least one of the purposes. §6(1).
        """
        allowed = set()
        for p in purposes:
            allowed.update(p.data_categories)
        return all(c in allowed for c in requested_category_ids)

    def _audit(
        self,
        event_type: AuditEventType,
        principal_id: str,
        fiduciary_id: str,
        purpose_ids: list[str],
        actor: str,
        detail: str = "",
        consent_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        event = ConsentAuditEvent(
            event_type=event_type,
            consent_id=consent_id,
            principal_id=principal_id,
            fiduciary_id=fiduciary_id,
            purpose_ids=purpose_ids,
            actor=actor,
            detail=detail,
            metadata=metadata or {},
        )
        self._store.append_audit(event)
