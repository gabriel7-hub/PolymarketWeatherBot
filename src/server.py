"""Dashboard API + static server.

    python -m src.server            # http://127.0.0.1:8000

Read-only over the paper-trading SQLite, plus one live endpoint (/api/forecast)
that recomputes a city's ensemble distribution vs market prices on demand.
"""
from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .config import ROOT, STATIONS, BANKROLL
from .paper import store
from .polymarket.gamma import fetch_open_temperature_events, parse_event
from .forecast.openmeteo import fetch_max_temp_distribution
from .forecast.model import yes_probability

WEB = ROOT / "web"
app = Flask(__name__, static_folder=None)

_fc_cache: dict[tuple, tuple[float, dict]] = {}


def db():
    return store.connect()


@app.get("/api/summary")
def summary():
    con = db()
    start = store.get_meta(con, "starting_cash", BANKROLL)
    cash = store.get_meta(con, "cash", BANKROLL)
    eq = con.execute("SELECT * FROM equity ORDER BY ts DESC LIMIT 1").fetchone()
    equity = eq["equity"] if eq else cash
    realized = eq["realized"] if eq else 0.0
    unrealized = eq["unrealized"] if eq else 0.0
    settled = con.execute(
        "SELECT pnl, side, resolved_yes FROM fills WHERE status='settled'").fetchall()
    wins = sum(1 for s in settled if s["pnl"] > 0)
    n = len(settled)
    # Brier score: model P(Yes) at entry vs whether Yes actually resolved.
    brier = None
    rows = con.execute(
        "SELECT model_prob, resolved_yes FROM fills WHERE status='settled' "
        "AND resolved_yes IS NOT NULL").fetchall()
    if rows:
        brier = sum((r["model_prob"] - r["resolved_yes"]) ** 2 for r in rows) / len(rows)
    open_n = con.execute("SELECT COUNT(*) c FROM fills WHERE status='open'").fetchone()["c"]
    created = store.get_meta(con, "created_at", time.time())
    return jsonify({
        "starting_cash": start, "cash": cash, "equity": equity,
        "total_pnl": equity - start, "roi": (equity - start) / start if start else 0,
        "realized": realized, "unrealized": unrealized,
        "open_positions": open_n, "settled": n,
        "wins": wins, "win_rate": wins / n if n else None,
        "brier": brier, "last_update": eq["ts"] if eq else None,
        "running_since": created,
        "is_live": bool(eq) and (time.time() - eq["ts"] < 3600),
    })


@app.get("/api/equity")
def equity_series():
    con = db()
    rows = con.execute("SELECT ts, equity, realized, unrealized FROM equity "
                       "ORDER BY ts ASC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/positions")
def positions():
    con = db()
    rows = con.execute(
        "SELECT * FROM fills WHERE status='open' ORDER BY ts DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/fills")
def fills():
    con = db()
    rows = con.execute(
        "SELECT * FROM fills ORDER BY ts DESC LIMIT 200").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/signals")
def signals():
    con = db()
    rows = con.execute(
        "SELECT * FROM signals ORDER BY edge DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/daily")
def daily():
    """Realized PnL grouped by settlement day + end-of-day equity."""
    con = db()
    rows = con.execute(
        "SELECT ts, pnl FROM fills WHERE status='settled'").fetchall()
    def day_of(ts):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    by_day = defaultdict(float)
    for r in rows:
        by_day[day_of(r["ts"])] += r["pnl"]
    eq = con.execute("SELECT ts, equity FROM equity ORDER BY ts ASC").fetchall()
    eq_by_day = {}
    for r in eq:
        eq_by_day[day_of(r["ts"])] = r["equity"]
    return jsonify({"realized": [{"day": d, "pnl": p} for d, p in sorted(by_day.items())],
                    "equity": [{"day": d, "equity": e} for d, e in sorted(eq_by_day.items())]})


@app.get("/api/forecast")
def forecast():
    """Live ensemble distribution vs market-implied bucket probabilities for one event."""
    slug = request.args.get("event", "")
    events = fetch_open_temperature_events()
    ev = next((e for e in events if e.get("slug") == slug), None)
    if ev is None:
        return jsonify({"error": "event not found", "available":
                        [e["slug"] for e in events[:60]]}), 404
    markets = parse_event(ev)
    station = next((m.station_code for m in markets if m.station_code in STATIONS), None)
    if not station:
        return jsonify({"error": "unknown station"}), 404
    date = markets[0].end_date[:10]
    key = (station, date)
    now = time.time()
    if key in _fc_cache and now - _fc_cache[key][0] < 600:
        fc = _fc_cache[key][1]
    else:
        s = STATIONS[station]
        f = fetch_max_temp_distribution(s["lat"], s["lon"], date, s["tz"], station)
        fc = {"mean": f.mean, "std": f.std, "members": f.members_max_c.tolist(), "_obj": f}
        _fc_cache[key] = (now, fc)
    buckets = []
    for m in sorted(markets, key=lambda x: x.threshold_c):
        p = yes_probability(fc["_obj"], m.bucket_kind, m.threshold_c)
        buckets.append({"label": f"{m.threshold_c}°", "kind": m.bucket_kind,
                        "degree": m.threshold_c, "model": p, "market": m.yes_price})
    s = STATIONS[station]
    return jsonify({"event": slug, "city": s["city"], "station": station,
                    "date": date, "mean": fc["mean"], "std": fc["std"],
                    "members": fc["members"], "buckets": buckets})


@app.get("/api/events")
def events():
    evs = fetch_open_temperature_events()
    out = []
    for e in evs:
        ms = parse_event(e)
        if any(m.station_code in STATIONS for m in ms):
            out.append({"slug": e["slug"], "title": e.get("title", "")})
    return jsonify(out)


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:p>")
def static_files(p):
    return send_from_directory(WEB, p)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
