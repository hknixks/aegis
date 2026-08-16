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

# Aave V3's Pool.getUserAccountData returns exactly this value (2**256 - 1,
# Solidity's uint256 max) for healthFactor when a position has no debt at
# all — there is nothing to be "at risk" of, so there is no ratio to
# express. This is a fixed constant of the EVM (the max value a uint256
# can hold), not a heuristic or a "very large number" guess: any other
# value, however large, is a real computed ratio and must be treated as
# one. Comparing the raw wad-scaled string against this exact constant is
# how aegis.api recognizes the sentinel and never displays it as a number
# — see RiskAssessment.no_debt.
_NO_DEBT_SENTINEL_WAD = Decimal(2**256 - 1)


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    AT_RISK = "AT_RISK"


class RiskAssessment(BaseModel):
    health_factor: Decimal
    threshold: Decimal
    level: RiskLevel
    # True exactly when the raw healthFactor was Aave's uint256-max
    # "no debt" sentinel (see _NO_DEBT_SENTINEL_WAD above) — never a
    # heuristic on how large health_factor happens to be. Callers that
    # display health_factor (aegis.api) must check this first and show
    # an explicit "no debt" state instead of the numeric value, which is
    # otherwise a meaningless, enormous number with no real meaning as a
    # ratio.
    no_debt: bool = False

    @property
    def at_risk(self) -> bool:
        return self.level is RiskLevel.AT_RISK


def health_factor_from_wad(raw: str) -> Decimal:
    """Convert Aave's wad-scaled healthFactor string to human units."""
    return Decimal(raw) / _WAD


# Human-scaled (post-/1e18) form of _NO_DEBT_SENTINEL_WAD, for callers that
# only have an already-scaled value (e.g. a string recorded in an audit
# event) rather than the raw wad reading. Prefer RiskAssessment.no_debt
# wherever a fresh RiskAssessment is available — this exists only for
# aegis.api's cross-process audit-log fallback path.
NO_DEBT_HEALTH_FACTOR = _NO_DEBT_SENTINEL_WAD / _WAD


def is_no_debt_health_factor(health_factor: Decimal) -> bool:
    return health_factor == NO_DEBT_HEALTH_FACTOR


def assess_health_factor(raw_health_factor_wad: str, threshold: Decimal) -> RiskAssessment:
    """Classify a position from its raw (wad-scaled) Aave V3 healthFactor.

    A wallet with no debt returns Aave's max-uint256 sentinel for
    healthFactor, which converts to an enormous number here and is
    correctly classified SAFE by the plain comparison below — no special
    casing needed for the SAFE/AT_RISK decision itself. `no_debt` is set
    so callers that *display* health_factor (rather than just compare it)
    know to show an explicit no-debt state instead of that number.
    """
    raw = Decimal(raw_health_factor_wad)
    health_factor = raw / _WAD
    level = RiskLevel.AT_RISK if health_factor < threshold else RiskLevel.SAFE
    no_debt = raw == _NO_DEBT_SENTINEL_WAD
    return RiskAssessment(health_factor=health_factor, threshold=threshold, level=level, no_debt=no_debt)
