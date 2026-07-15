import os
import json
import requests
import pandas as pd

from smc_logic import compute_indicators

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Twelve Data symbol -> friendly label
SYMBOLS = {
    "XAU/USD": "Gold (XAU/USD)",
    "GBP/JPY": "GBP/JPY",
}

INTERVAL = os.environ.get("SMC_INTERVAL", "15min")
STATE_FILE = "state.json"

SIGNAL_LABELS = {
    "bullish_choch": "Bullish CHoCH (possible reversal up)",
    "bullish_bos": "Bullish BOS (uptrend continuation)",
    "bearish_choch": "Bearish CHoCH (possible reversal down)",
    "bearish_bos": "Bearish BOS (downtrend continuation)",
    "bull_fvg": "Bullish FVG formed",
    "bear_fvg": "Bearish FVG formed",
    "bullish_sweep": "Sell-side liquidity swept",
    "bearish_sweep": "Buy-side liquidity swept",
    "bull_ob_retest": "Bullish order block retest",
    "bear_ob_retest": "Bearish order block retest",
    "long_setup": "LONG SETUP (structure shift + discount zone)",
    "short_setup": "SHORT SETUP (structure shift + premium zone)",
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
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
    }
    resp = requests.get(url, params=params, timeout=20)
    data = resp.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error for {symbol}: {data}")
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    if resp.status_code != 200:
        print(f"Telegram send failed: {resp.text}")


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

        # Use the second-to-last bar: the last fully closed candle.
        idx = len(df) - 2
        bar_time = df.loc[idx, "datetime"].isoformat()

        last_seen = state.get(symbol, {}).get("last_processed_time")
        if last_seen == bar_time:
            continue  # already evaluated this bar, nothing new

        active = [SIGNAL_LABELS[k] for k in SIGNAL_LABELS if bool(df.loc[idx, k])]

        if active:
            price = df.loc[idx, "close"]
            lines = "\n".join(f"- {s}" for s in active)
            msg = (
                f"SMC Signal: {label}\n"
                f"{lines}\n"
                f"Close: {price}\n"
                f"Bar: {bar_time} ({INTERVAL})"
            )
            send_telegram(msg)
            print(f"Alert sent for {symbol} at {bar_time}")
        else:
            print(f"No new signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_processed_time"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
