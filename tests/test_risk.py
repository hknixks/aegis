from decimal import Decimal

from aegis.risk import RiskLevel, assess_health_factor, health_factor_from_wad


def test_health_factor_from_wad() -> None:
    assert health_factor_from_wad("1500000000000000000") == Decimal("1.5")


def test_assess_health_factor_at_risk() -> None:
    result = assess_health_factor("1200000000000000000", Decimal("1.5"))
    assert result.level is RiskLevel.AT_RISK
    assert result.at_risk is True


def test_assess_health_factor_safe() -> None:
    result = assess_health_factor("4000000000000000000", Decimal("1.5"))
    assert result.level is RiskLevel.SAFE
    assert result.at_risk is False


def test_assess_health_factor_boundary_is_safe() -> None:
    result = assess_health_factor("1500000000000000000", Decimal("1.5"))
    assert result.level is RiskLevel.SAFE


def test_assess_health_factor_no_debt_sentinel_is_safe() -> None:
    max_uint256 = str(2**256 - 1)
    result = assess_health_factor(max_uint256, Decimal("1.5"))
    assert result.level is RiskLevel.SAFE
