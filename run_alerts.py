import os
import json
import requests
import pandas as pd

from smc_logic import compute_indicators

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]

SYMBOLS = {
    "XAU/USD": "Gold (XAU/USD)",
    "GBP/JPY": "GBP/JPY",
}

INTERVAL   = os.environ.get("SMC_INTERVAL", "15min")
STATE_FILE = "state.json"

SIGNAL_MAP = {
    "bullish_choch":  ("BUY",  "🟢", "CHoCH — Bearish structure broken, potential reversal UP"),
    "bullish_bos":    ("BUY",  "🟢", "BOS — Bullish structure confirmed, uptrend continuation"),
    "bull_fvg":       ("BUY",  "🔵", "Bullish Fair Value Gap formed (imbalance to fill UP)"),
    "bullish_sweep":  ("BUY",  "🟡", "Sell-side liquidity swept — smart money may be buying"),
    "bull_ob_retest": ("BUY",  "🟢", "Bullish Order Block retest — potential BUY zone"),
    "long_setup":     ("BUY",  "✅", "LONG SETUP — Structure shift + price in discount zone"),
    "bearish_choch":  ("SELL", "🔴", "CHoCH — Bullish structure broken, potential reversal DOWN"),
    "bearish_bos":    ("SELL", "🔴", "BOS — Bearish structure confirmed, downtrend continuation"),
    "bear_fvg":       ("SELL", "🟠", "Bearish Fair Value Gap formed (imbalance to fill DOWN)"),
    "bearish_sweep":  ("SELL", "🟡", "Buy-side liquidity swept — smart money may be selling"),
    "bear_ob_retest": ("SELL", "🔴", "Bearish Order Block retest — potential SELL zone"),
    "short_setup":    ("SELL", "✅", "SHORT SETUP — Structure shift + price in premium zone"),
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_bars(symbol, interval=INTERVAL, outputsize=150):
    url    = "https://api.twelvedata.com/time_series"
    params = {
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "apikey":     TWELVE_DATA_API_KEY,
        "order":      "ASC",
    }
    resp = requests.get(url, params=params, timeout=20)
    data = resp.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error for {symbol}: {data}")
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df.sort_values("datetime").reset_index(drop=True)


def send_telegram(text):
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    if resp.status_code != 200:
        print(f"Telegram send failed: {resp.text}")


def build_message(symbol, label, bar_time, price, active_signals):
    buy_signals  = [(k, v) for k, v in active_signals if v[0] == "BUY"]
    sell_signals = [(k, v) for k, v in active_signals if v[0] == "SELL"]

    if len(buy_signals) > len(sell_signals):
        bias      = "🟢 BUY BIAS"
        direction = "LOOK FOR LONGS"
    elif len(sell_signals) > len(buy_signals):
        bias      = "🔴 SELL BIAS"
        direction = "LOOK FOR SHORTS"
    else:
        bias      = "⚪ MIXED SIGNALS"
        direction = "WAIT FOR CONFIRMATION"

    lines = [
        f"<b>📡 SMC ALERT — {label}</b>",
        f"<b>Direction: {bias}</b>",
        f"<b>Action: {direction}</b>",
        f"Price: {price:.5f}",
        f"Time: {bar_time} ({INTERVAL})",
        "",
    ]

    if buy_signals:
        lines.append("🟢 <b>Bullish Signals:</b>")
        for _, (_, emoji, desc) in buy_signals:
            lines.append(f"  {emoji} {desc}")

    if sell_signals:
        if buy_signals:
            lines.append("")
        lines.append("🔴 <b>Bearish Signals:</b>")
        for _, (_, emoji, desc) in sell_signals:
            lines.append(f"  {emoji} {desc}")

    lines += [
        "",
        "⚠️ Always confirm on chart before entering.",
    ]

    return "\n".join(lines)


def main():
    state = load_state()

    for symbol, label in SYMBOLS.items():
        try:
            df = fetch_bars(symbol)
        except Exception as exc:
            print(f"Failed to fetch {symbol}: {exc}")
            continue

        if len(df) < 30:
            print(f"Not enough bars for {symbol}, skipping")
            continue

        df = compute_indicators(df)

        idx      = len(df) - 2
        bar_time = df.loc[idx, "datetime"].isoformat()
        last_seen = state.get(symbol, {}).get("last_processed_time")

        if last_seen == bar_time:
            print(f"No new bar for {symbol}, skipping")
            continue

        active = [
            (k, SIGNAL_MAP[k])
            for k in SIGNAL_MAP
            if bool(df.loc[idx, k])
        ]

        if active:
            price = df.loc[idx, "close"]
            msg   = build_message(symbol, label, bar_time, price, active)
            send_telegram(msg)
            print(f"Alert sent for {symbol} at {bar_time}")
        else:
            print(f"No new signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_processed_time"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
