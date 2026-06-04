"""Combine market data + forecast model into ranked trade signals."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..config import (MIN_EDGE, STATIONS, MIN_PRICE, MAX_PRICE,
                      MIN_HOURS_TO_RESOLVE, CALIBRATION)
from ..forecast.openmeteo import fetch_max_temp_distribution, MaxTempForecast
from ..forecast.model import yes_probability, apply_calibration
from ..polymarket.gamma import TempMarket
from .sizing import stake_usdc


@dataclass
class Signal:
    market: TempMarket
    side: str            # "Yes" or "No" — the outcome we BUY
    token_id: str
    model_prob: float    # our P(Yes resolves)
    price: float         # price we'd pay for the side we buy
    edge: float          # model edge on the side we buy
    stake: float         # USDC to deploy
    station: str = ""
    date: str = ""
    fc_mean: float = 0.0  # calibrated ensemble mean max-temp at entry
    fc_std: float = 0.0

    def __str__(self) -> str:
        return (f"BUY {self.side:3} @ {self.price:.3f} "
                f"(model P(Yes)={self.model_prob:.3f}, edge={self.edge:+.3f}, "
                f"${self.stake:>6.2f})  {self.market.question[:60]}")


def _apply_calibration(fc: MaxTempForecast) -> MaxTempForecast:
    return apply_calibration(fc, CALIBRATION)


def _hours_to_resolve(end_date: str) -> float:
    try:
        end = dt.datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except ValueError:
        return 1e9
    return (end - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600


def is_tradable(m: TempMarket) -> bool:
    """Skip markets that are mid-resolution (pinned price / dead liquidity) or
    too close to settlement for a forecast edge to be real."""
    if _hours_to_resolve(m.end_date) < MIN_HOURS_TO_RESOLVE:
        return False
    if not (MIN_PRICE <= m.yes_price <= MAX_PRICE):
        return False
    return True


def evaluate_market(m: TempMarket, fc: MaxTempForecast,
                    min_edge: float = MIN_EDGE) -> Signal | None:
    if not is_tradable(m):
        return None
    p_yes = yes_probability(fc, m.bucket_kind, m.threshold_c)

    # Edge on buying Yes vs buying No; take whichever is positive & bigger.
    yes_edge = p_yes - m.yes_price
    no_edge = (1.0 - p_yes) - m.no_price

    meta = dict(station=fc.station_code, date=fc.date,
                fc_mean=fc.mean - fc.bias_c, fc_std=fc.std)
    if yes_edge >= no_edge and yes_edge >= min_edge:
        return Signal(m, "Yes", m.yes_token_id, p_yes, m.yes_price, yes_edge,
                      stake_usdc(p_yes, m.yes_price), **meta)
    if no_edge > yes_edge and no_edge >= min_edge:
        return Signal(m, "No", m.no_token_id, p_yes, m.no_price, no_edge,
                      stake_usdc(1.0 - p_yes, m.no_price), **meta)
    return None


def generate_signals(markets: list[TempMarket],
                     min_edge: float = MIN_EDGE) -> list[Signal]:
    """Fetch one forecast per (station, date) and score every bucket market."""
    cache: dict[tuple[str, str], MaxTempForecast] = {}
    signals: list[Signal] = []

    for m in markets:
        station = m.station_code
        if not station or station not in STATIONS:
            continue
        date = m.end_date[:10]
        key = (station, date)
        if key not in cache:
            s = STATIONS[station]
            try:
                cache[key] = _apply_calibration(fetch_max_temp_distribution(
                    s["lat"], s["lon"], date, s["tz"], station))
            except Exception as e:  # noqa: BLE001
                print(f"  ! forecast failed for {station} {date}: {e}")
                cache[key] = None
        fc = cache[key]
        if fc is None:
            continue
        sig = evaluate_market(m, fc, min_edge)
        if sig and sig.stake > 0:
            signals.append(sig)

    signals.sort(key=lambda s: s.edge, reverse=True)
    return signals
