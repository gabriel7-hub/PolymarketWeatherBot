"""Telegram notifications. Silently no-ops if credentials aren't set."""
from __future__ import annotations

import requests

from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

API = "https://api.telegram.org/bot{token}/sendMessage"


def enabled() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def send(text: str) -> bool:
    """Send a Markdown message. Returns True on success, never raises."""
    if not enabled():
        return False
    try:
        r = requests.post(
            API.format(token=TELEGRAM_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15,
        )
        return r.ok
    except Exception:  # noqa: BLE001
        return False


def notify_settlement(fill: dict, balance: dict) -> bool:
    """Format and send a closed-trade result.

    `fill` is the settled row; `balance` carries running portfolio totals.
    """
    won = fill["pnl"] > 0
    emoji = "🟢" if won else "🔴"
    verb = "WON" if won else "LOST"
    roi = fill["pnl"] / fill["cost"] * 100 if fill["cost"] else 0
    text = (
        f"{emoji} *Trade {verb}*  ({fill['pnl']:+.2f} USDC, {roi:+.0f}%)\n"
        f"_{fill['question']}_\n"
        f"`{fill['side']}` @ {fill['entry_price']:.3f} → resolved {fill['exit_price']:.0f}\n"
        f"stake ${fill['cost']:.2f} · model P(Yes) {fill['model_prob']*100:.0f}% · "
        f"edge {fill['edge']*100:+.1f}%\n"
        f"——\n"
        f"Equity *${balance['equity']:.2f}*  ·  "
        f"realized {balance['realized']:+.2f}  ·  "
        f"record {balance['wins']}/{balance['settled']}"
    )
    return send(text)


if __name__ == "__main__":
    ok = send("✅ PolymarketWeather: Telegram notifications are wired up.")
    print("sent" if ok else "failed (check TELEGRAM_TOKEN / TELEGRAM_CHAT_ID in .env)")
