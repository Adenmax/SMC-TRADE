import os
import json
import requests
import pandas as pd
from smc_logic import run_full_analysis

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "state.json"
SYMBOLS = {"XAU/USD": "Gold (XAU/USD)", "GBP/JPY": "GBP/JPY"}
TIMEFRAMES = ["1day", "4h", "1h", "15min"]


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_bars(symbol, interval, outputsize=100):
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_API_KEY,
            "order": "ASC",
        },
        timeout=20,
    )
    data = resp.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error [{symbol} {interval}]: {data}")
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df.sort_values("datetime").reset_index(drop=True)


def send_telegram(text):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"Telegram error: {resp.text}")


def bias_emoji(b):
    if b == "bullish":
        return "🟢"
    if b == "bearish":
        return "🔴"
    return "⚪"


def get_entry_levels(signal, poi, conf):
    entry = None
    sl = None
    tp1 = None
    tp2 = None
    buf = 0.0003

    if signal == "BUY":
        f15 = conf.get("bull_fvg_15min")
        f1h = poi.get("bull_fvg_1h")
        ob = poi.get("bull_ob_1h")
        if f15 is not None:
            entry = f15["top"]
            sl = f15["bot"] * (1 - buf)
        elif f1h is not None:
            entry = f1h["top"]
            sl = f1h["bot"] * (1 - buf)
        elif ob is not None:
            entry = (ob["top"] + ob["bot"]) / 2
            sl = ob["bot"] * (1 - buf)
        tp1 = poi.get("prev_session_high")
        eq = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_high"]
        tp2 = eq[0]["level"] if eq else None

    if signal == "SELL":
        f15 = conf.get("bear_fvg_15min")
        f1h = poi.get("bear_fvg_1h")
        ob = poi.get("bear_ob_1h")
        if f15 is not None:
            entry = f15["bot"]
            sl = f15["top"] * (1 + buf)
        elif f1h is not None:
            entry = f1h["bot"]
            sl = f1h["top"] * (1 + buf)
        elif ob is not None:
            entry = (ob["top"] + ob["bot"]) / 2
            sl = ob["top"] * (1 + buf)
        tp1 = poi.get("prev_session_low")
        eq = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_low"]
        tp2 = eq[0]["level"] if eq else None

    rr1 = None
    rr2 = None
    if entry is not None and sl is not None and tp1 is not None:
        risk = abs(entry - sl)
        if risk > 0:
            rr1 = abs(tp1 - entry) / risk
    if entry is not None and sl is not None and tp2 is not None:
        risk = abs(entry - sl)
        if risk > 0:
            rr2 = abs(tp2 - entry) / risk

    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr1": rr1, "rr2": rr2}


def build_message(symbol, label, price, bar_time, r):
    a = r["alignment"]
    b = r["bias"]
    n = r["narrative"]
    p = r["poi"]
    c = r["confirmation"]
    t = r["trade_idea"]

    kz = n.get("kill_zone") or "Outside kill zone"
    ls = n.get("london_sweep")
    fvgm = n.get("fvg_momentum")

    msg = f"<b>SMC ALERT v2 - {label}</b>\n"
    msg += f"Price: <b>{price:.5f}</b> | {bar_time}\n\n"

    msg += "<b>PILLAR 1 - BIAS</b>\n"
    msg += f"  Daily  {bias_emoji(b.get('daily'))} {str(b.get('daily','-')).upper()}\n"
    msg += f"  4H     {bias_emoji(b.get('4h'))} {str(b.get('4h','-')).upper()}\n"
    msg += f"  1H     {bias_emoji(b.get('1h'))} {str(b.get('1h','-')).upper()}\n"
    msg += f"  15min  {bias_emoji(b.get('15min'))} {str(b.get('15min','-')).upper()}\n"
    msg += f"  Result: <b>{a['label']}</b> ({a['score']}/4)\n\n"

    msg += "<b>PILLAR 2 - NARRATIVE</b>\n"
    msg += f"  Kill zone: <b>{kz}</b>\n"
    if ls == "swept_high":
        msg += "  London swept Asian HIGH - expect DOWN\n"
    elif ls == "swept_low":
        msg += "  London swept Asian LOW - expect UP\n"
    if fvgm:
        msg += f"  {fvgm}\n"

    msg += "\n<b>PILLAR 3 - POI</b>\n"
    bull_ob = p.get("bull_ob_1h")
    bear_ob = p.get("bear_ob_1h")
    bull_fvg = p.get("bull_fvg_1h")
    bear_fvg = p.get("bear_fvg_1h")
    ph = p.get("prev_session_high")
    pl = p.get("prev_session_low")

    if bull_ob:
        msg += f"  Bullish OB (1H): {bull_ob['bot']:.5f} - {bull_ob['top']:.5f}\n"
    if bull_fvg:
        msg += f"  Bullish FVG (1H): {bull_fvg['bot']:.5f} - {bull_fvg['top']:.5f}\n"
    if bear_ob:
        msg += f"  Bearish OB (1H): {bear_ob['bot']:.5f} - {bear_ob['top']:.5f}\n"
    if bear_fvg:
        msg += f"  Bearish FVG (1H): {bear_fvg['bot']:.5f} - {bear_fvg['top']:.5f}\n"
    if ph:
        msg += f"  Prev session high: {ph:.5f} (BSL target)\n"
    if pl:
        msg += f"  Prev session low: {pl:.5f} (SSL target)\n"
    for lvl in p.get("equal_levels_4h", [])[:2]:
        tag = "Equal Highs" if lvl["type"] == "eq_high" else "Equal Lows"
        msg += f"  {tag}: {lvl['level']:.5f} ({lvl['count']}x)\n"

    msg += "\n<b>PILLAR 4 - CONFIRMATION</b>\n"
    sc15 = c.get("sweep_choch_15min")
    sc1h = c.get("sweep_choch_1h")
    eng = c.get("engulfing_15min")
    bf15 = c.get("bull_fvg_15min")
    brf15 = c.get("bear_fvg_15min")

    if sc15 == "bullish_reversal":
        msg += "  Sweep+CHoCH (15min) - BULLISH\n"
    if sc15 == "bearish_reversal":
        msg += "  Sweep+CHoCH (15min) - BEARISH\n"
    if sc1h == "bullish_reversal":
        msg += "  Sweep+CHoCH (1H) - BULLISH\n"
    if sc1h == "bearish_reversal":
        msg += "  Sweep+CHoCH (1H) - BEARISH\n"
    if eng == "bullish_engulfing":
        msg += "  Bullish engulfing (15min)\n"
    if eng == "bearish_engulfing":
        msg += "  Bearish engulfing (15min)\n"
    if bf15:
        msg += f"  Bull FVG (15min): {bf15['bot']:.5f} - {bf15['top']:.5f}\n"
    if brf15:
        msg += f"  Bear FVG (15min): {brf15['bot']:.5f} - {brf15['top']:.5f}\n"
    if not any([sc15, sc1h, eng, bf15, brf15]):
        msg += "  No confirmation yet - wait\n"

    msg += "\n------------------------------\n"

    if t["signal"]:
        lvls = get_entry_levels(t["signal"], p, c)
        direction = "BUY" if t["signal"] == "BUY" else "SELL"
        msg += f"<b>TRADE IDEA: {direction}</b>\n"
        msg += f"  Quality: {t['quality']}\n\n"
        if lvls["entry"]:
            msg += f"  Entry: <b>{lvls['entry']:.5f}</b>\n"
        else:
            msg += "  Entry: Wait for price to reach POI\n"
        if lvls["sl"]:
            msg += f"  SL:    <b>{lvls['sl']:.5f}</b>\n"
        if lvls["tp1"]:
            rr = f" (RR: {lvls['rr1']:.1f}R)" if lvls["rr1"] else ""
            msg += f"  TP1:   <b>{lvls['tp1']:.5f}</b>{rr}\n"
        if lvls["tp2"]:
            rr = f" (RR: {lvls['rr2']:.1f}R)" if lvls["rr2"] else ""
            msg += f"  TP2:   <b>{lvls['tp2']:.5f}</b>{rr}\n"
        msg += "\n  Place limit order at Entry.\n"
        msg += "  Move SL to BE after TP1 hit.\n"
    else:
        msg += "<b>NO TRADE - wait for all 4 pillars.</b>\n"

    msg += "\nAlways confirm on chart. Manage your risk."
    return msg


def main():
    state = load_state()
    for symbol, label in SYMBOLS.items():
        try:
            dfs = {}
            for tf in TIMEFRAMES:
                dfs[tf] = fetch_bars(symbol, tf, outputsize=100)
        except Exception as exc:
            print(f"Fetch error [{symbol}]: {exc}")
            continue

        df_15min = dfs.get("15min")
        if df_15min is None or len(df_15min) < 20:
            print(f"Not enough bars for {symbol}")
            continue

        bar_time = df_15min["datetime"].iloc[-2].isoformat()
        last_seen = state.get(symbol, {}).get("last_bar")
        if last_seen == bar_time:
            print(f"No new bar for {symbol}")
            continue

        r = run_full_analysis(
            df_daily=dfs.get("1day"),
            df_4h=dfs.get("4h"),
            df_1h=dfs.get("1h"),
            df_15min=df_15min,
        )

        conf = r["confirmation"]
        has_signal = (
            r["trade_idea"]["signal"] is not None
            or conf.get("sweep_choch_15min") is not None
            or conf.get("sweep_choch_1h") is not None
            or conf.get("engulfing_15min") is not None
        )

        if has_signal:
            price = float(df_15min["close"].iloc[-2])
            msg = build_message(symbol, label, price, bar_time, r)
            send_telegram(msg)
            print(f"Alert sent for {symbol} at {bar_time}")
        else:
            print(f"No signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
