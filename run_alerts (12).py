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
TIMEFRAMES = ["1day", "4h", "1h", "15min", "5min"]


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


def conf_emoji(v):
    return "✅" if v else "❌"


def calc_rr(entry, sl, tp):
    if entry and sl and tp:
        risk = abs(entry - sl)
        if risk > 0:
            return round(abs(tp - entry) / risk, 1)
    return None


def build_entry_message(symbol, label, price, r):
    trade   = r["trade"]
    biases  = r["biases"]
    conf    = r["confluences"]
    ob      = r["ob"]
    fvg     = r["fvg"]
    liq     = r["liquidity_level"]
    kz      = r["kill_zone"] or "Outside kill zone"
    choch   = r["confirmation"]

    signal  = trade["signal"]
    entry   = trade["entry"]
    sl      = trade["sl"]
    tp      = trade["tp"]
    rr      = calc_rr(entry, sl, tp)
    rr_str  = f"  ({rr}R)" if rr else ""

    emoji = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
    bias_line = (f"{bias_emoji(biases.get('daily'))}D "
                 f"{bias_emoji(biases.get('4h'))}4H "
                 f"{bias_emoji(biases.get('1h'))}1H "
                 f"{bias_emoji(biases.get('15min'))}15m")

    ob_str  = f"{ob['bot']:.2f} - {ob['top']:.2f}" if ob else "N/A"
    fvg_str = f"{fvg['bot']:.2f} - {fvg['top']:.2f}" if fvg else "N/A"
    liq_str = f"{liq:.2f}" if liq else "N/A"

    msg  = f"<b>⚡ {emoji} — {label}</b>\n"
    msg += f"Price: <b>{price:.2f}</b>  |  {kz}\n"
    msg += f"Bias: {bias_line}\n\n"
    msg += f"<b>5 Confluences:</b>\n"
    msg += f"  {conf_emoji(conf['bias'])} Structural Bias: {r['direction'].upper()}\n"
    msg += f"  {conf_emoji(conf['bos'])} BOS on 15min\n"
    msg += f"  {conf_emoji(conf['order_block'])} Order Block: {ob_str}\n"
    msg += f"  {conf_emoji(conf['liquidity'])} Liquidity cleared: {liq_str}\n"
    msg += f"  {conf_emoji(conf['imbalance'])} Imbalance (FVG): {fvg_str}\n\n"
    msg += f"  ✅ 5min CHoCH confirmed @ {choch['level']:.2f}\n\n"
    msg += f"<b>Entry:  {entry:.2f}</b>\n"
    msg += f"<b>SL:     {sl:.2f}</b>\n"
    msg += f"<b>TP:     {tp:.2f}</b>{rr_str}\n" if tp else ""
    msg += f"\nQuality: {trade['quality']}\n"
    msg += f"\n<b>All 5 confluences met — Enter now</b>"
    msg += f"\nAlways confirm on chart before entering."
    return msg


def build_waiting_message(symbol, label, price, r):
    biases  = r["biases"]
    conf    = r["confluences"]
    ob      = r["ob"]
    fvg     = r["fvg"]
    liq     = r["liquidity_level"]
    kz      = r["kill_zone"] or "Outside kill zone"
    count   = r["confluence_count"]
    direction = r["direction"]

    emoji = "🟢" if direction == "bullish" else "🔴"
    bias_line = (f"{bias_emoji(biases.get('daily'))}D "
                 f"{bias_emoji(biases.get('4h'))}4H "
                 f"{bias_emoji(biases.get('1h'))}1H "
                 f"{bias_emoji(biases.get('15min'))}15m")

    ob_str = f"{ob['bot']:.2f} - {ob['top']:.2f}" if ob else "Not found yet"
    fvg_str = f"{fvg['bot']:.2f} - {fvg['top']:.2f}" if fvg else "Not found yet"

    msg  = f"<b>{emoji} {direction.upper()} SETUP — {label}</b>\n"
    msg += f"Price: <b>{price:.2f}</b>  |  {kz}\n"
    msg += f"Bias: {bias_line}\n\n"
    msg += f"<b>Confluences ({count}/5):</b>\n"
    msg += f"  {conf_emoji(conf['bias'])} Structural Bias\n"
    msg += f"  {conf_emoji(conf['bos'])} BOS on 15min\n"
    msg += f"  {conf_emoji(conf['order_block'])} Order Block: {ob_str}\n"
    msg += f"  {conf_emoji(conf['liquidity'])} Liquidity cleared\n"
    msg += f"  {conf_emoji(conf['imbalance'])} Imbalance (FVG): {fvg_str}\n\n"
    msg += f"<b>⏳ OB tapped — Waiting for 5min CHoCH</b>\n"
    msg += f"Watch 5min chart for {'bullish' if direction == 'bullish' else 'bearish'} "
    msg += f"body close above {'lower high' if direction == 'bullish' else 'higher low'}\n"
    msg += f"Bot will alert when CHoCH confirmed."
    return msg


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
        )

        price     = float(df_15min["close"].iloc[-2])
        signal    = r["trade"]["signal"]
        direction = r["direction"]
        count     = r["confluence_count"]

        if signal in ("BUY", "SELL"):
            # All 5 confluences + CHoCH = entry now
            msg = build_entry_message(symbol, label, price, r)
            send_telegram(msg)
            print(f"⚡ {signal} alert sent for {symbol} @ {price:.2f}")

        elif signal == "WAITING":
            # OB tapped, waiting for 5min CHoCH
            last_waiting = state.get(symbol, {}).get("last_waiting_bar")
            if last_waiting != bar_time:
                msg = build_waiting_message(symbol, label, price, r)
                send_telegram(msg)
                state.setdefault(symbol, {})["last_waiting_bar"] = bar_time
                print(f"⏳ WAITING alert sent for {symbol}")

        elif direction == "ranging":
            print(f"Ranging [{symbol}] — no trade")

        else:
            print(f"No signal [{symbol}] {direction} | {count}/5 confluences | price={price:.2f}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
