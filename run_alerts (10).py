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

SYMBOLS    = {"XAU/USD": "Gold (XAU/USD)", "GBP/JPY": "GBP/JPY"}
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


def build_message(symbol, label, price, r):
    biases   = r["biases"]
    direction = r["direction"]
    kz       = r["kill_zone"] or "Outside kill zone"
    zone     = r["active_zone"]
    tapped   = r["zone_tapped"]
    conf     = r["confirmation"]
    trade    = r["trade"]
    bull     = r["bull_count"]
    bear     = r["bear_count"]

    bias_line = (f"{bias_emoji(biases.get('daily'))}D "
                 f"{bias_emoji(biases.get('4h'))}4H "
                 f"{bias_emoji(biases.get('1h'))}1H "
                 f"{bias_emoji(biases.get('15min'))}15m")

    if trade["signal"]:
        signal = trade["signal"]
        emoji  = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
        entry  = trade["entry"]
        sl     = trade["sl"]
        tp     = trade["tp"]
        rr     = calc_rr(entry, sl, tp)
        rr_str = f"  ({rr}R)" if rr else ""
        conf_str = f"{conf['type']} ({conf['tf']}) ✅"
        zone_str = " + ".join(zone.get("confluence", ["Zone"]))
        zone_tf  = zone.get("tf", "")

        msg  = f"<b>⚡ {emoji} — {label}</b>\n"
        msg += f"Price: <b>{price:.2f}</b>  |  {kz}\n"
        msg += f"Bias: {bias_line}  ({max(bull,bear)}/4)\n\n"
        msg += f"<b>Zone ({zone_tf}):</b> {zone['bot']:.2f} - {zone['top']:.2f}\n"
        msg += f"<b>Confluence:</b> {zone_str}\n"
        msg += f"<b>Confirmation:</b> {conf_str}\n\n"
        msg += f"<b>Entry:  {entry:.2f}</b>\n"
        msg += f"<b>SL:     {sl:.2f}</b>\n"
        msg += f"<b>TP:     {tp:.2f}</b>{rr_str}\n" if tp else ""
        msg += f"\nQuality: {trade['quality']}\n"
        msg += f"\n<b>All criteria met — Enter now</b>"
        msg += f"\nAlways confirm on chart before entering."
        return msg

    return None


def main():
    state = load_state()

    for symbol, label in SYMBOLS.items():
        print(f"\nFetching {symbol}...")
        dfs = fetch_symbol(symbol)

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
            msg = build_message(symbol, label, price, r)
            if msg:
                send_telegram(msg)
                print(f"⚡ ENTRY alert: {symbol} {r['trade']['signal']} @ {price:.2f}")
        else:
            direction = r["direction"]
            zone      = r["active_zone"]
            tapped    = r["zone_tapped"]
            kz        = r["kill_zone"]

            if direction == "ranging":
                print(f"Ranging [{symbol}] — no trade")
            elif zone and tapped:
                print(f"Zone tapped [{symbol}] {zone['bot']:.2f}-{zone['top']:.2f} "
                      f"| waiting for {direction} confirmation")
            elif zone:
                print(f"Zone identified [{symbol}] {zone['bot']:.2f}-{zone['top']:.2f} "
                      f"| {direction} | waiting for price to tap | kz={kz}")
            else:
                print(f"No zone [{symbol}] {direction} | price={price:.2f}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
