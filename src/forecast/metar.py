"""Real station observations (METAR/ASOS) — the actual resolution source.

Polymarket resolves on the highest temperature recorded at a specific airport
station (Wunderground, whole °C). ERA5 reanalysis (used as a stopgap) differs
from that by ~0.5-1.5°C. This module pulls the *same class of observation* that
resolves the market, from the Iowa Environmental Mesonet (IEM) ASOS archive —
free, global, and keyed by ICAO code (which is exactly our STATIONS key).

METAR temperatures are reported in whole °C, matching the market's rounding.
"""
from __future__ import annotations

import datetime as dt

import requests

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"


def station_daily_max(icao: str, start: str, end: str, tz: str) -> dict[str, float]:
    """Map local-date -> max observed temperature (°C) over [start, end]."""
    d2 = dt.date.fromisoformat(end) + dt.timedelta(days=1)   # IEM end is exclusive-ish
    d1 = dt.date.fromisoformat(start)
    params = {
        "station": icao, "data": "tmpc", "tz": tz,
        "format": "onlycomma", "latlon": "no", "missing": "empty",
        "year1": d1.year, "month1": d1.month, "day1": d1.day,
        "year2": d2.year, "month2": d2.month, "day2": d2.day,
    }
    try:
        r = requests.get(IEM_URL, params=params, timeout=40)
        r.raise_for_status()
        lines = r.text.splitlines()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, float] = {}
    for line in lines[1:]:                      # skip header
        parts = line.split(",")
        if len(parts) < 3:
            continue
        day = parts[1][:10]                     # 'YYYY-MM-DD HH:MM' (local)
        try:
            t = float(parts[2])
        except ValueError:
            continue
        if start <= day <= end:
            out[day] = max(out.get(day, -1e9), t)
    return out


def fetch_station_daily_max(icao: str, date: str, tz: str) -> float | None:
    """Actual recorded daily max for one station/date (resolution-aligned)."""
    return station_daily_max(icao, date, date, tz).get(date)
