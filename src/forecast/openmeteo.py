"""Fetch ensemble temperature forecasts from Open-Meteo (free, no API key).

The key to an edge in these markets is a *probability distribution* over the
daily maximum temperature, not a single number. We get that by pulling every
member of multiple ensemble models (GFS, ICON, ECMWF) and treating the spread
of member daily-maxes as the predictive distribution.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import requests

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# Models with hourly temperature ensemble members available on Open-Meteo.
MODELS = "gfs_seamless,icon_seamless,ecmwf_ifs025"

# Default extra uncertainty (°C) added in quadrature to ensemble spread.
# Overridden per-station once calibration data exists.
DEFAULT_SIGMA_FLOOR = 1.2


@dataclass
class MaxTempForecast:
    station_code: str
    date: str
    members_max_c: np.ndarray   # one daily-max per ensemble member
    bias_c: float = 0.0         # learned calibration offset (subtracted from mean)
    sigma_floor: float = DEFAULT_SIGMA_FLOOR
    emos: tuple | None = None   # (a,b,c,d): μ=a+b·mean, σ²=c+d·var (overrides above)

    @property
    def mean(self) -> float:
        return float(self.members_max_c.mean())

    @property
    def std(self) -> float:
        return float(self.members_max_c.std(ddof=1))


def fetch_max_temp_distribution(lat: float, lon: float, date: str,
                                tz: str, station_code: str = "",
                                models: str = MODELS) -> MaxTempForecast:
    """Return the distribution of the daily maximum 2m temperature for `date`.

    We request hourly temps for all ensemble members, then take, per member,
    the max over the local-day's hours.
    """
    r = requests.get(
        ENSEMBLE_URL,
        params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m",
            "models": models,
            "timezone": tz,
            "start_date": date, "end_date": date,
        },
        timeout=30,
    )
    r.raise_for_status()
    hourly = r.json()["hourly"]

    # Every member series is keyed temperature_2m, temperature_2m_member01, ...
    member_keys = [k for k in hourly if k.startswith("temperature_2m")]
    maxes = []
    for k in member_keys:
        arr = np.array([v for v in hourly[k] if v is not None], dtype=float)
        if arr.size:
            maxes.append(arr.max())
    members_max = np.array(maxes, dtype=float)
    if members_max.size == 0:
        raise RuntimeError(f"No ensemble data returned for {station_code} {date}")
    return MaxTempForecast(station_code=station_code, date=date, members_max_c=members_max)


def fetch_actual_max(lat: float, lon: float, date: str, tz: str) -> float | None:
    """Actual recorded daily max (ERA5 reanalysis archive).

    NOTE: this is a *proxy* for the resolution source (Wunderground station obs);
    they can differ ~0.5-1.5°C. For live trades the authoritative truth is the
    Polymarket resolution itself; ERA5 is used for historical backtesting where
    we have no trade.
    """
    try:
        r = requests.get(
            ARCHIVE_URL,
            params={"latitude": lat, "longitude": lon,
                    "daily": "temperature_2m_max", "timezone": tz,
                    "start_date": date, "end_date": date},
            timeout=30,
        )
        r.raise_for_status()
        vals = r.json().get("daily", {}).get("temperature_2m_max", [])
        return float(vals[0]) if vals and vals[0] is not None else None
    except Exception:  # noqa: BLE001
        return None
