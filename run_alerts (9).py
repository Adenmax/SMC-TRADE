import os
import json
import time
import requests
import pandas as pd
from smc_logic import run_full_analysis

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE          = "state.json"

SYMBOLS = {"XAU/USD": "Gold (XAU/USD)", "GBP/JPY": "GBP/JPY"}

# Timeframes to fetch per symbol
# Free plan: 8 credits/min. We fetch 5 TFs with 8s gap = ~7/min. Fine.
TIMEFRAMES = ["1day", "4h", "1h", "15min", "5min", "1min"]


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_tf(symbol, interval, outputsize=100):
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": symbol, "interval": interval,
                        "outputsize": outputsize, "apikey": TWELVE_DATA_API_KEY,
                        "order": "ASC"},
                timeout=30,
            )
            data = resp.json()
            if data.get("code") == 429:
                wait = 20 * (attempt + 1)
                print(f"Rate limit [{symbol} {interval}], waiting {wait}s")
                time.sleep(wait)
                continue
            if "values" not in data:
                print(f"No data [{symbol} {interval}]: {data.get('message','')}")
                return None
            df = pd.DataFrame(data["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Error [{symbol} {interval}]: {e}")
            time.sleep(10)
    return None


def fetch_symbol(symbol):
    dfs = {}
    for i, tf in enumerate(TIMEFRAMES):
        if i > 0:
            time.sleep(8)
        df = fetch_tf(symbol, tf)
        if df is not None:
            dfs[tf] = df
    return dfs


def send_telegram(text):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"Telegram error: {resp.text}")


def bias_emoji(b):
    if b == "bullish": return "🟢"
    if b == "bearish": return "🔴"
    return "⚪"


def calc_rr(entry, sl, tp):
    if entry and sl and tp:
        risk = abs(entry - sl)
        if risk > 0:
            return round(abs(tp - entry) / risk, 1)
    return None


def build_entry_message(symbol, label, price, r):
    trade = r["trade"]
    conf  = r["confirmation"]
    bias  = r["bias"]
    kz    = r["kill_zone"] or "Outside kill zone"
    zone  = trade["zone"]

    signal = trade["signal"]
    emoji  = "🟢 BUY" if signal == "BUY" else "🔴 SELL"

    zone_str = f"{zone['bot']:.2f} - {zone['top']:.2f} ({zone['components']})" \
               if zone else "See chart"

    rr = calc_rr(trade["entry"], trade["sl"], trade["tp"])
    rr_str = f"  ({rr}R)" if rr else ""

    msg  = f"<b>⚡ {emoji} — {label}</b>\n"
    msg += f"Price: <b>{price:.2f}</b>  |  {kz}\n"
    msg += f"Bias: {bias_emoji(bias.get('daily'))}Daily  "
    msg += f"{bias_emoji(bias.get('4h'))}4H\n\n"
    msg += f"<b>Zone:</b> {zone_str} ✅ TAPPED\n"
    msg += f"<b>Confirmation:</b> {conf['type']} ({conf['tf']}) ✅\n\n"
    msg += f"<b>Entry:  {trade['entry']:.2f}</b>\n" if trade['entry'] else ""
    msg += f"<b>SL:     {trade['sl']:.2f}</b>\n"    if trade['sl']    else ""
    msg += f"<b>TP:     {trade['tp']:.2f}</b>{rr_str}\n" if trade['tp'] else ""
    msg += f"\nQuality: {trade['quality']}\n"
    msg += f"\nAlways confirm on chart before entering."
    return msg


def main():
    state = load_state()

    for symbol, label in SYMBOLS.items():
        print(f"\nFetching {symbol}...")
        dfs = fetch_symbol(symbol)

        # Wait between symbols
        if symbol != list(SYMBOLS.keys())[-1]:
            print("Waiting 15s before next symbol...")
            time.sleep(15)

        df_15min = dfs.get("15min")
        if df_15min is None or len(df_15min) < 20:
            print(f"Not enough data for {symbol}")
            continue

        bar_time  = df_15min["datetime"].iloc[-2].isoformat()
        last_seen = state.get(symbol, {}).get("last_bar")
        if last_seen == bar_time:
            print(f"No new bar for {symbol}")
            continue

        r = run_full_analysis(
            df_daily=dfs.get("1day"),
            df_4h=dfs.get("4h"),
            df_1h=dfs.get("1h"),
            df_15min=df_15min,
            df_5min=dfs.get("5min"),
            df_1min=dfs.get("1min"),
        )

        price = float(df_15min["close"].iloc[-2])

        if r["trade"]["signal"]:
            # Full confirmation — fire entry alert
            msg = build_entry_message(symbol, label, price, r)
            send_telegram(msg)
            print(f"⚡ ENTRY alert: {symbol} {r['trade']['signal']}")

        else:
            # No confirmation yet — log silently
            zt = r["zone_tapped"]
            kz = r["kill_zone"]
            conf = r["confirmation"]
            if zt["bull"] or zt["bear"]:
                direction = "bull" if zt["bull"] else "bear"
                zone = r["zones"][direction]
                print(f"Zone tapped [{symbol}] "
                      f"{zone['bot']:.2f}-{zone['top']:.2f} "
                      f"| waiting for {conf.get('direction','?')} confirmation")
            else:
                print(f"No signal [{symbol}] price={price:.2f} kz={kz}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
