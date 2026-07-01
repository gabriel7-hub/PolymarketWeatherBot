"""Main bot loop.

  1. Discover open daily high-temperature markets on Polymarket.
  2. For each station/date, pull an ensemble max-temp forecast.
  3. Convert the forecast into bucket probabilities matching the resolution rule.
  4. Compare to market prices, rank by edge, size with fractional Kelly.
  5. Place orders (DRY_RUN by default — logs instead of sending).

Run once:   python -m src.bot
Loop:       python -m src.bot --loop 600    # every 10 min
"""
from __future__ import annotations

import argparse
import time

from .config import (MIN_EDGE, DRY_RUN, BANKROLL, CASH_BUFFER, MAX_DAY_FRACTION,
                     MAX_CITY_FRACTION, MIN_STAKE_PER_MARKET)
from .polymarket.gamma import fetch_open_temperature_events, parse_event
from .polymarket.clob import place_order
from .polymarket.portfolio import LivePortfolio, city_of
from .paper.engine import capped_budget
from .strategy.edge import generate_signals


def run_once() -> None:
    print(f"\n=== scan @ {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"(DRY_RUN={DRY_RUN}, MIN_EDGE={MIN_EDGE}) ===")
    events = fetch_open_temperature_events()
    markets = [m for ev in events for m in parse_event(ev)]
    print(f"discovered {len(events)} temperature events / {len(markets)} bucket markets")

    signals = generate_signals(markets)
    print(f"\n{len(signals)} actionable signal(s):")
    for s in signals:
        print(" ", s)

    _execute(signals)


def _execute(signals) -> None:
    """Place orders under the same risk controls the paper broker enforces:
    dedup against open positions, a cash-reserve floor, and per-resolution-day /
    per-city capital caps — sized off the weather BANKROLL, on the SHARED wallet.

    Bailing out here (rather than trading unguarded) is deliberate: without a live
    position read we can't dedup or measure deployed capital, so we'd risk stacking
    orders and blowing the caps."""
    try:
        pf = LivePortfolio.fetch()
    except Exception as e:  # noqa: BLE001
        print(f"! could not read live positions ({e}); skipping orders to stay guarded")
        return

    start = BANKROLL
    floor = CASH_BUFFER * start            # never spend below the reserve
    day_cap = MAX_DAY_FRACTION * start     # max committed to one resolution day
    city_cap = MAX_CITY_FRACTION * start   # max committed to one city
    placed = skipped = 0
    for s in signals:
        if pf.held(s.token_id) or s.stake <= 0:
            skipped += 1
            continue
        cash = start - pf.open_cost()
        city = city_of(s.market.question)
        budget = capped_budget(s.stake, cash, floor,
                               pf.day_deployed(s.market.end_date), day_cap,
                               pf.city_deployed(city), city_cap)
        if budget < MIN_STAKE_PER_MARKET:
            skipped += 1
            continue
        place_order(s.token_id, "BUY", s.price, budget)
        pf.commit(s.token_id, city, s.market.end_date, budget)
        placed += 1
    print(f"\nplaced {placed} order(s), skipped {skipped} "
          f"(DRY_RUN={DRY_RUN}, bankroll=${start:.0f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0,
                    help="seconds between scans; 0 = run once")
    args = ap.parse_args()

    if args.loop <= 0:
        run_once()
        return
    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            print(f"scan error: {e}")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
