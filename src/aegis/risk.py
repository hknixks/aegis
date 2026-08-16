"""Deterministic risk model for Aave V3 lending positions.

Pure functions only — no network calls, no KeeperHub knowledge. Takes the
raw values KeeperHub's Aave V3 read actions return and turns them into a
typed, auditable risk assessment.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

# Aave V3 healthFactor is a uint256 scaled by 1e18 ("wad" units), confirmed
# by the `{{@step:Get Aave Health Factor.healthFactor}}` template reference
# in this project's real "Aave Health Factor Monitor" KeeperHub workflows.
_WAD = Decimal(10) ** 18


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    AT_RISK = "AT_RISK"


class RiskAssessment(BaseModel):
    health_factor: Decimal
    threshold: Decimal
    level: RiskLevel

    @property
    def at_risk(self) -> bool:
        return self.level is RiskLevel.AT_RISK


def health_factor_from_wad(raw: str) -> Decimal:
    """Convert Aave's wad-scaled healthFactor string to human units."""
    return Decimal(raw) / _WAD


def assess_health_factor(raw_health_factor_wad: str, threshold: Decimal) -> RiskAssessment:
    """Classify a position from its raw (wad-scaled) Aave V3 healthFactor.

    A wallet with no debt returns Aave's max-uint256 sentinel for
    healthFactor, which converts to an enormous number here and is
    correctly classified SAFE by the plain comparison below — no special
    casing needed.
    """
    health_factor = health_factor_from_wad(raw_health_factor_wad)
    level = RiskLevel.AT_RISK if health_factor < threshold else RiskLevel.SAFE
    return RiskAssessment(health_factor=health_factor, threshold=threshold, level=level)
