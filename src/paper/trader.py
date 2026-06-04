"""Paper-trading daemon.

Each tick:
  1. discover markets + generate signals (same engine the live bot will use)
  2. paper-fill new signals
  3. mark all open positions to live prices, settle resolved ones
  4. snapshot equity, record signals for the dashboard

    python -m src.paper.trader              # one tick
    python -m src.paper.trader --loop 900   # every 15 min for the week-long run
"""
from __future__ import annotations

import argparse
import time

from ..config import MIN_EDGE
from ..polymarket.gamma import fetch_open_temperature_events, parse_event
from ..strategy.edge import generate_signals
from .engine import PaperBroker


def tick(broker: PaperBroker) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    events = fetch_open_temperature_events()
    markets = [m for ev in events for m in parse_event(ev)]
    signals = generate_signals(markets)

    taken = set()
    for s in signals:
        broker.log_forecast(s)          # record every forecast for calibration
        if broker.execute(s):
            taken.add(s.token_id)
            print(f"  FILLED {s}")
    broker.record_signals(signals, taken)
    broker.mark_and_settle()
    filled_actuals = broker.backfill_actuals()
    if filled_actuals:
        print(f"  backfilled {filled_actuals} actual max-temp(s)")

    eq = broker.con.execute(
        "SELECT equity, realized, unrealized FROM equity ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    opens = broker.con.execute(
        "SELECT COUNT(*) c FROM fills WHERE status='open'").fetchone()["c"]
    print(f"[{stamp}] signals={len(signals)} filled={len(taken)} open={opens} "
          f"equity=${eq['equity']:.2f} (real ${eq['realized']:.2f} / "
          f"unreal ${eq['unrealized']:.2f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    args = ap.parse_args()
    broker = PaperBroker()
    if args.loop <= 0:
        tick(broker)
        return
    while True:
        try:
            tick(broker)
        except Exception as e:  # noqa: BLE001
            print(f"tick error: {e}")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
