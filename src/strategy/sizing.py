"""Position sizing. Fractional Kelly for a binary contract priced at `price`
that pays 1 if it resolves Yes."""
from __future__ import annotations

from ..config import KELLY_FRACTION, MAX_STAKE_PER_MARKET, BANKROLL


def kelly_fraction(p: float, price: float) -> float:
    """Optimal bankroll fraction for buying a Yes contract at `price`.

    Buying pays net odds b = (1-price)/price on a win prob p.
    Kelly f* = (b*p - (1-p)) / b = (p - price) / (1 - price).
    Returns 0 when there is no edge.
    """
    if price <= 0 or price >= 1:
        return 0.0
    f = (p - price) / (1.0 - price)
    return max(0.0, f)


def stake_usdc(p: float, price: float,
               bankroll: float = BANKROLL,
               fraction: float = KELLY_FRACTION,
               cap: float = MAX_STAKE_PER_MARKET) -> float:
    f = kelly_fraction(p, price) * fraction
    return round(min(f * bankroll, cap), 2)
