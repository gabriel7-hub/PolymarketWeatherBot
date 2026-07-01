"""Live weather-portfolio state, read from the Polymarket Data API.

The trading wallet is SHARED with other strategies (e.g. the sibling crypto
"Up or Down" engine), so every accounting query here is filtered to *our* weather
markets. Counting the crypto legs would corrupt the per-day / per-city capital
caps and the cash figure the live bot sizes against.

This is the live analogue of the paper broker's SQLite state (`paper.engine`):
`held()` ⇔ `already_open`, `day_deployed` / `city_deployed` mirror the same, and
`open_cost()` feeds the cash-buffer floor. Positions are the source of truth, so
the live path stays stateless between scans — no local DB to drift out of sync.
"""
from __future__ import annotations

import re

from ..config import STATIONS, POLY_PROXY_ADDRESS
from . import data_api

# Matches the resolved question wording, e.g.
#   "Will the highest temperature in Seoul be 28°C on June 17?"
_CITY_RE = re.compile(r"in ([\w ]+?) be ", re.I)
_WEATHER_CITIES = {s["city"] for s in STATIONS.values()}


def city_of(text: str) -> str:
    """City name out of a market question/title, '' if it doesn't match."""
    m = _CITY_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _is_weather(p: dict) -> bool:
    title = (p.get("title") or "")
    return "temperature in" in title.lower() and city_of(title) in _WEATHER_CITIES


def weather_positions(wallet: str | None = None) -> list[dict]:
    """Our open weather positions on the wallet, normalised to the fields the
    caps need. Cost is entry cost (size × avg entry), matching the paper broker's
    per-fill `cost`."""
    wallet = wallet or POLY_PROXY_ADDRESS
    if not wallet:
        return []
    out: list[dict] = []
    for p in data_api.get_positions(wallet):
        if not _is_weather(p):
            continue
        size = float(p.get("size") or 0.0)
        avg = float(p.get("avgPrice") or 0.0)
        out.append({
            "token_id": str(p.get("asset")),
            "city": city_of(p.get("title", "")),
            "end_date": (p.get("endDate") or "")[:10],
            "cost": size * avg,
            "size": size,
        })
    return out


class LivePortfolio:
    """Deployed-capital view for the live risk caps. Provisional in-scan orders
    are folded in via `commit()` so a single scan can't double-deploy before the
    Data API reflects the just-placed order."""

    def __init__(self, positions: list[dict]):
        self.positions = positions
        self._committed_tokens: set[str] = set()
        self._extra_day: dict[str, float] = {}
        self._extra_city: dict[str, float] = {}
        self._extra_total: float = 0.0

    @classmethod
    def fetch(cls, wallet: str | None = None) -> "LivePortfolio":
        return cls(weather_positions(wallet))

    def held(self, token_id: str) -> bool:
        return (token_id in self._committed_tokens
                or any(p["token_id"] == token_id for p in self.positions))

    def day_deployed(self, end_date: str) -> float:
        day = (end_date or "")[:10]
        if not day:
            return 0.0
        base = sum(p["cost"] for p in self.positions if p["end_date"] == day)
        return base + self._extra_day.get(day, 0.0)

    def city_deployed(self, city: str) -> float:
        if not city:
            return 0.0
        base = sum(p["cost"] for p in self.positions if p["city"] == city)
        return base + self._extra_city.get(city, 0.0)

    def open_cost(self) -> float:
        """Total weather capital deployed (for the cash-buffer floor)."""
        return sum(p["cost"] for p in self.positions) + self._extra_total

    def commit(self, token_id: str, city: str, end_date: str, cost: float) -> None:
        day = (end_date or "")[:10]
        self._committed_tokens.add(token_id)
        self._extra_day[day] = self._extra_day.get(day, 0.0) + cost
        self._extra_city[city] = self._extra_city.get(city, 0.0) + cost
        self._extra_total += cost
