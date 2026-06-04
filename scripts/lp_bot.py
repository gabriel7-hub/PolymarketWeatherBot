"""Live LP quoting daemon (Tier-1 edge #2 execution).

Each cycle: cancel stale orders, recompute fair-value quotes for the target
events, and post two-sided maker orders to earn the liquidity-reward spread.

Two-sided quoting on a YES bucket is done with two BUY orders:
    bid  ->  BUY  YES  @ bid
    ask  ->  BUY  NO   @ (1 - ask)
If both fill, you hold 1 YES + 1 NO at total cost (bid + 1 - ask); exactly one
pays $1, so you net (ask - bid) — the captured spread — plus LP rewards on the
resting orders. Leaned buckets post only the safe side.

SAFETY: orders route through clob (DRY_RUN-guarded) AND require LP_EXECUTE=1.
Default = simulation (logs the quotes it would post). To go live you need
DRY_RUN=0 + funded PK + LP_EXECUTE=1.

    python scripts/lp_bot.py --loop 120
    LP_EVENTS=highest-temperature-in-busan-on-june-5-2026 python scripts/lp_bot.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import LP_EXECUTE, LP_SIZE, LP_EVENTS, DRY_RUN
from src.polymarket.gamma import fetch_open_temperature_events
from src.polymarket.clob import place_maker, cancel_all
from src.strategy.market_making import quote_event


def _target_events(events: list[dict]) -> list[dict]:
    if LP_EVENTS.strip():
        wanted = {s.strip() for s in LP_EVENTS.split(",")}
        return [e for e in events if e["slug"] in wanted]
    # default: events whose city we model (the same ones the bot trades)
    from src.config import STATIONS
    out = []
    for e in events:
        from src.polymarket.gamma import parse_event
        if any(m.station_code in STATIONS for m in parse_event(e)):
            out.append(e)
    return out[:4]


def cycle(execute: bool) -> None:
    stamp = time.strftime("%H:%M:%S")
    events = _target_events(fetch_open_temperature_events())
    if execute:
        cancel_all()
    posted = 0
    for ev in events:
        quotes = quote_event(ev)
        elig = [q for q in quotes if q.reward_eligible or q.lean]
        print(f"[{stamp}] {ev['slug']}: {len(elig)} quotes")
        for q in quotes:
            if not (q.reward_eligible or q.lean):
                continue
            if q.bid is not None:                       # BUY YES @ bid
                if execute:
                    place_maker(q.yes_token, q.bid, LP_SIZE)
                posted += 1
            if q.ask is not None:                       # BUY NO @ (1 - ask)
                if execute:
                    place_maker(q.no_token, round(1 - q.ask, 3), LP_SIZE)
                posted += 1
            print(f"     {q}")
    print(f"[{stamp}] {'posted' if execute else 'would post'} {posted} maker "
          f"orders (size {LP_SIZE} ea)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    args = ap.parse_args()
    execute = LP_EXECUTE
    mode = ("EXECUTE " + ("(DRY_RUN sim)" if DRY_RUN else "LIVE ORDERS")
            if execute else "quote-only (set LP_EXECUTE=1 to post)")
    print(f"LP quoting daemon — {mode} — size {LP_SIZE}/quote\n")
    if args.loop <= 0:
        cycle(execute)
        return
    while True:
        try:
            cycle(execute)
        except Exception as e:  # noqa: BLE001
            print(f"cycle error: {e}")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
