"""
PolicyRegistry
~~~~~~~~~~~~~~
Resolves which obligations apply to a given Data Fiduciary at runtime.

The registry ships with four built-in PolicyConfig presets
(one per FiduciaryClass).  Production deployments can override
any preset or register custom configs per fiduciary ID.

Usage::

    registry = PolicyRegistry()
    config = registry.resolve(fiduciary_profile)

    if config.sdf_obligations:
        # run §10 checks
        ...
"""

from __future__ import annotations

from .enums import FiduciaryClass
from .models import DataFiduciaryProfile, PolicyConfig

# ---------------------------------------------------------------------------
# Built-in presets (one per FiduciaryClass)
# ---------------------------------------------------------------------------

_DEFAULTS: dict[FiduciaryClass, PolicyConfig] = {

    FiduciaryClass.STANDARD: PolicyConfig(
        fiduciary_class=FiduciaryClass.STANDARD,
        purpose_lapse_days=365,
        grievance_response_days=30,
        appeal_window_days=60,
        dpia_interval_days=365,
        audit_interval_days=365,
        notice_required=True,
        accuracy_obligation=True,
        erasure_obligation=True,
        sdf_obligations=False,
        access_right=True,
    ),

    FiduciaryClass.SIGNIFICANT: PolicyConfig(
        fiduciary_class=FiduciaryClass.SIGNIFICANT,
        purpose_lapse_days=365,
        grievance_response_days=30,
        appeal_window_days=60,
        dpia_interval_days=365,        # §10(2)(c)(i) — periodic; frequency TBD by rules
        audit_interval_days=365,       # §10(2)(c)(ii) — periodic; frequency TBD by rules
        notice_required=True,
        accuracy_obligation=True,
        erasure_obligation=True,
        sdf_obligations=True,          # §10 additional obligations apply
        access_right=True,
    ),

    FiduciaryClass.STARTUP_EXEMPT: PolicyConfig(
        # §17(3): §5, §8(3), §8(7), §10, §11 do NOT apply to exempt startups.
        fiduciary_class=FiduciaryClass.STARTUP_EXEMPT,
        purpose_lapse_days=365,
        grievance_response_days=30,
        appeal_window_days=60,
        dpia_interval_days=365,
        audit_interval_days=365,
        notice_required=False,         # §17(3) exempts §5
        accuracy_obligation=False,     # §17(3) exempts §8(3)
        erasure_obligation=False,      # §17(3) exempts §8(7)
        sdf_obligations=False,         # §17(3) exempts §10
        access_right=False,            # §17(3) exempts §11
    ),

    FiduciaryClass.STATE_INSTRUMENTALITY: PolicyConfig(
        # §17(4): §8(7), §12(3) do NOT apply; §12(2) only applies for decision-making.
        fiduciary_class=FiduciaryClass.STATE_INSTRUMENTALITY,
        purpose_lapse_days=365,
        grievance_response_days=30,
        appeal_window_days=60,
        dpia_interval_days=365,
        audit_interval_days=365,
        notice_required=True,
        accuracy_obligation=True,
        erasure_obligation=False,      # §17(4) removes §8(7) obligation
        sdf_obligations=False,
        access_right=True,
    ),
}


class PolicyRegistry:
    """
    Resolves the PolicyConfig that governs a Data Fiduciary.

    Lookup order:
    1. Per-fiduciary override (keyed by fiduciary ID).
    2. Per-class preset.
    3. STANDARD fallback.
    """

    def __init__(self) -> None:
        self._presets: dict[FiduciaryClass, PolicyConfig] = dict(_DEFAULTS)
        self._overrides: dict[str, PolicyConfig] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_preset(self, config: PolicyConfig) -> None:
        """Override the built-in preset for a fiduciary class."""
        self._presets[config.fiduciary_class] = config

    def set_override(self, fiduciary_id: str, config: PolicyConfig) -> None:
        """Register a per-fiduciary config that takes precedence over the preset."""
        self._overrides[fiduciary_id] = config

    def remove_override(self, fiduciary_id: str) -> None:
        self._overrides.pop(fiduciary_id, None)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, fiduciary: DataFiduciaryProfile) -> PolicyConfig:
        """
        Return the PolicyConfig applicable to *fiduciary*.

        Resolution order: per-ID override → class preset → STANDARD.
        """
        if fiduciary.id in self._overrides:
            return self._overrides[fiduciary.id]
        return self._presets.get(
            fiduciary.fiduciary_class,
            self._presets[FiduciaryClass.STANDARD],
        )

    def resolve_by_class(self, cls: FiduciaryClass) -> PolicyConfig:
        """Convenience: resolve by class without a fiduciary instance."""
        return self._presets.get(cls, self._presets[FiduciaryClass.STANDARD])

    # ------------------------------------------------------------------
    # Obligation checks (convenience wrappers)
    # ------------------------------------------------------------------

    def requires_notice(self, fiduciary: DataFiduciaryProfile) -> bool:
        return self.resolve(fiduciary).notice_required

    def requires_sdf_obligations(self, fiduciary: DataFiduciaryProfile) -> bool:
        return self.resolve(fiduciary).sdf_obligations

    def requires_erasure(self, fiduciary: DataFiduciaryProfile) -> bool:
        return self.resolve(fiduciary).erasure_obligation

    def grievance_sla_days(self, fiduciary: DataFiduciaryProfile) -> int:
        return self.resolve(fiduciary).grievance_response_days

    def purpose_lapse_days(self, fiduciary: DataFiduciaryProfile) -> int:
        return self.resolve(fiduciary).purpose_lapse_days

    # ------------------------------------------------------------------
    # Penalty schedule (Schedule to §33)
    # ------------------------------------------------------------------

    PENALTY_SCHEDULE: dict[str, int] = {
        # Schedule item: maximum penalty in Indian Rupees
        "security_safeguard_failure":   250_00_00_000,  # ₹250 crore — §8(5)
        "breach_notification_failure":  200_00_00_000,  # ₹200 crore — §8(6)
        "children_data_violation":      200_00_00_000,  # ₹200 crore — §9
        "sdf_obligation_breach":        150_00_00_000,  # ₹150 crore — §10
        "data_principal_duty_breach":   10_000,         # ₹10,000    — §15
        "voluntary_undertaking_breach": None,           # Same as underlying breach
        "other_breach":                 50_00_00_000,   # ₹50 crore  — Schedule item 7
    }

    def max_penalty(self, breach_type: str) -> int | None:
        """
        Return the maximum monetary penalty for a breach type (in INR).
        Returns None for breach types where the penalty mirrors the underlying
        breach (voluntary undertaking — Schedule item 6).
        """
        return self.PENALTY_SCHEDULE.get(breach_type, self.PENALTY_SCHEDULE["other_breach"])


# Module-level default instance — import and use directly in most cases.
default_registry = PolicyRegistry()
