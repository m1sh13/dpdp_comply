"""
Persistence interface for the consent engine.

ConsentStore is the abstract base — swap in a Postgres implementation
for production without touching the engine.

InMemoryConsentStore is used for testing and local development.
It implements the "existing data is not consented" default: any
principal_id not in the store has no active consents.
"""

from __future__ import annotations

import abc
from collections import defaultdict
from datetime import datetime, timezone

from core.models import ConsentRecord, NoticeRecord
from core.enums import ConsentState
from consent_engine.audit import ConsentAuditEvent


class ConsentStore(abc.ABC):
    """Abstract persistence interface for consent and notice records."""

    # ------------------------------------------------------------------
    # Notice persistence
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def save_notice(self, notice: NoticeRecord) -> None: ...

    @abc.abstractmethod
    def get_notice(self, notice_id: str) -> NoticeRecord | None: ...

    @abc.abstractmethod
    def get_notices_for_principal(
        self, principal_id: str, fiduciary_id: str
    ) -> list[NoticeRecord]: ...

    # ------------------------------------------------------------------
    # Consent record persistence
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def save_consent(self, record: ConsentRecord) -> None: ...

    @abc.abstractmethod
    def get_consent(self, consent_id: str) -> ConsentRecord | None: ...

    @abc.abstractmethod
    def get_active_consents(
        self, principal_id: str, fiduciary_id: str
    ) -> list[ConsentRecord]: ...

    @abc.abstractmethod
    def get_all_consents_for_principal(
        self, principal_id: str
    ) -> list[ConsentRecord]: ...

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def append_audit(self, event: ConsentAuditEvent) -> None: ...

    @abc.abstractmethod
    def get_audit_trail(
        self,
        principal_id: str | None = None,
        consent_id: str | None = None,
    ) -> list[ConsentAuditEvent]: ...

    # ------------------------------------------------------------------
    # Pre-Act data registry
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def register_pre_act_principal(
        self, principal_id: str, fiduciary_id: str, purpose_ids: list[str]
    ) -> None:
        """
        Mark a principal's data as pre-Act (existing data, not yet consented).
        §5(2): the fiduciary must serve a retrospective notice and collect
        fresh consent before processing under the Act.
        """
        ...

    @abc.abstractmethod
    def is_pre_act(self, principal_id: str, fiduciary_id: str) -> bool:
        """
        Returns True if this principal's data was loaded as pre-Act.
        A True result means processing is blocked until fresh consent is obtained.
        """
        ...


class InMemoryConsentStore(ConsentStore):
    """
    Thread-unsafe in-memory store.  Suitable for tests and single-process
    development only.

    Default posture: every principal_id is treated as having NO active
    consent unless a valid ConsentRecord exists in this store.
    """

    def __init__(self) -> None:
        # keyed by record.id
        self._notices: dict[str, NoticeRecord] = {}
        self._consents: dict[str, ConsentRecord] = {}
        self._audit: list[ConsentAuditEvent] = []

        # (principal_id, fiduciary_id) → set of purpose_ids
        self._pre_act: dict[tuple[str, str], set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    # Notice
    # ------------------------------------------------------------------

    def save_notice(self, notice: NoticeRecord) -> None:
        self._notices[notice.id] = notice

    def get_notice(self, notice_id: str) -> NoticeRecord | None:
        return self._notices.get(notice_id)

    def get_notices_for_principal(
        self, principal_id: str, fiduciary_id: str
    ) -> list[NoticeRecord]:
        return [
            n for n in self._notices.values()
            if n.principal_id == principal_id and n.fiduciary_id == fiduciary_id
        ]

    # ------------------------------------------------------------------
    # Consent
    # ------------------------------------------------------------------

    def save_consent(self, record: ConsentRecord) -> None:
        self._consents[record.id] = record

    def get_consent(self, consent_id: str) -> ConsentRecord | None:
        return self._consents.get(consent_id)

    def get_active_consents(
        self, principal_id: str, fiduciary_id: str
    ) -> list[ConsentRecord]:
        return [
            c for c in self._consents.values()
            if c.principal_id == principal_id
            and c.fiduciary_id == fiduciary_id
            and c.state == ConsentState.ACTIVE
            and c.is_valid_for_processing()
        ]

    def get_all_consents_for_principal(
        self, principal_id: str
    ) -> list[ConsentRecord]:
        return [
            c for c in self._consents.values()
            if c.principal_id == principal_id
        ]

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def append_audit(self, event: ConsentAuditEvent) -> None:
        self._audit.append(event)

    def get_audit_trail(
        self,
        principal_id: str | None = None,
        consent_id: str | None = None,
    ) -> list[ConsentAuditEvent]:
        results = self._audit
        if principal_id:
            results = [e for e in results if e.principal_id == principal_id]
        if consent_id:
            results = [e for e in results if e.consent_id == consent_id]
        return sorted(results, key=lambda e: e.occurred_at)

    # ------------------------------------------------------------------
    # Pre-Act data
    # ------------------------------------------------------------------

    def register_pre_act_principal(
        self, principal_id: str, fiduciary_id: str, purpose_ids: list[str]
    ) -> None:
        self._pre_act[(principal_id, fiduciary_id)].update(purpose_ids)

    def is_pre_act(self, principal_id: str, fiduciary_id: str) -> bool:
        return (principal_id, fiduciary_id) in self._pre_act
