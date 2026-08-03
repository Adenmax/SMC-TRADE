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

# Fetch order: daily+4h together (2 credits), then 1h, 15min, 5min separately
# Total: 4 requests per symbol = 4 credits, well under 8/min
# Two symbols = 8 credits but staggered across time
CORE_TIMEFRAMES = ["1day", "4h", "1h", "15min"]
ENTRY_TIMEFRAME = "5min"


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
    """Fetch one timeframe with rate limit handling."""
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
                print(f"No values [{symbol} {interval}]: {data.get('message','')}")
                return None
            df = pd.DataFrame(data["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Fetch error [{symbol} {interval}]: {e}")
            time.sleep(10)
    return None


def fetch_symbol_data(symbol):
    """
    Fetch all timeframes for one symbol.
    Spaces requests 8 seconds apart = max 7.5 calls/min per symbol.
    """
    dfs = {}
    all_tfs = CORE_TIMEFRAMES + [ENTRY_TIMEFRAME]
    for i, tf in enumerate(all_tfs):
        if i > 0:
            time.sleep(8)
        df = fetch_tf(symbol, tf)
        if df is not None:
            dfs[tf] = df
            print(f"  Fetched {symbol} {tf}: {len(df)} bars")
        else:
            print(f"  Skipped {symbol} {tf}")
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


def get_realistic_sl(signal, poi, df_15min, df_5min=None):
    """
    SL just beyond the most recent local swing on 5min or 15min.
    Keeps SL tight and realistic.
    """
    buf = 0.0003
    sl = None
    ref_df = df_5min if (df_5min is not None and len(df_5min) >= 10) else df_15min

    if ref_df is not None and len(ref_df) >= 10:
        recent = ref_df.tail(20)
        if signal == "BUY":
            local_low = float(recent["low"].min())
            current   = float(ref_df["close"].iloc[-2])
            if (current - local_low) / current < 0.015:
                sl = local_low * (1 - buf)
        elif signal == "SELL":
            local_high = float(recent["high"].max())
            current    = float(ref_df["close"].iloc[-2])
            if (local_high - current) / current < 0.015:
                sl = local_high * (1 + buf)

    if sl is None:
        if signal == "BUY":
            fvg = poi.get("bull_fvg_15min_poi") or poi.get("bull_fvg_1h")
            ob  = poi.get("bull_ob_1h")
            if fvg: sl = fvg["bot"] * (1 - buf)
            elif ob: sl = ob["bot"] * (1 - buf)
        elif signal == "SELL":
            fvg = poi.get("bear_fvg_15min_poi") or poi.get("bear_fvg_1h")
            ob  = poi.get("bear_ob_1h")
            if fvg: sl = fvg["top"] * (1 + buf)
            elif ob: sl = ob["top"] * (1 + buf)

    return sl


def get_entry_tp(signal, poi, session_levels):
    entry = tp1 = tp2 = None

    if signal == "BUY":
        fvg = poi.get("bull_fvg_15min_poi") or poi.get("bull_fvg_1h")
        ob  = poi.get("bull_ob_1h")
        if fvg:   entry = round(fvg["top"], 2)
        elif ob:  entry = round((ob["top"] + ob["bot"]) / 2, 2)

        candidates = [(n, v["high"]) for n, v in session_levels.items()
                      if entry and v["high"] > entry]
        if candidates:
            tp1 = round(min(candidates, key=lambda x: x[1] - entry)[1], 2)
        ph = poi.get("prev_session_high")
        if ph and entry and ph > entry: tp2 = round(ph, 2)

    elif signal == "SELL":
        fvg = poi.get("bear_fvg_15min_poi") or poi.get("bear_fvg_1h")
        ob  = poi.get("bear_ob_1h")
        if fvg:   entry = round(fvg["bot"], 2)
        elif ob:  entry = round((ob["top"] + ob["bot"]) / 2, 2)

        candidates = [(n, v["low"]) for n, v in session_levels.items()
                      if entry and v["low"] < entry]
        if candidates:
            tp1 = round(max(candidates, key=lambda x: x[1])[1], 2)
        pl = poi.get("prev_session_low")
        if pl and entry and pl < entry: tp2 = round(pl, 2)

    return entry, tp1, tp2


def calc_rr(entry, sl, tp):
    if entry and sl and tp:
        risk = abs(entry - sl)
        if risk > 0:
            return round(abs(tp - entry) / risk, 1)
    return None


def get_conf_summary(conf):
    parts = []
    sc15, _ = conf.get("sweep_choch_15min", (None, None))
    sc1h, _ = conf.get("sweep_choch_1h",    (None, None))
    ic,   _ = conf.get("internal_choch",     (None, None))
    eng     = conf.get("engulfing_15min")
    retest  = conf.get("choch_retest", False)

    if sc15 == "bullish_reversal":  parts.append("Bull CHoCH 15min ✅")
    if sc15 == "bearish_reversal":  parts.append("Bear CHoCH 15min ✅")
    if sc1h == "bullish_reversal":  parts.append("Bull CHoCH 1H ✅")
    if sc1h == "bearish_reversal":  parts.append("Bear CHoCH 1H ✅")
    if ic == "bullish_internal_choch": parts.append("Bull CHoCH 5min ✅")
    if ic == "bearish_internal_choch": parts.append("Bear CHoCH 5min ✅")
    if retest: parts.append("Retest ✅")
    if eng == "bullish_engulfing":  parts.append("Bull engulfing ✅")
    if eng == "bearish_engulfing":  parts.append("Bear engulfing ✅")
    return ", ".join(parts) if parts else "Waiting"


def build_message(symbol, label, price, bar_time, r, df_15min, df_5min, alert_type):
    a  = r["alignment"]
    b  = r["bias"]
    n  = r["narrative"]
    p  = r["poi"]
    c  = r["confirmation"]
    t  = r["trade_idea"]
    sl = n.get("session_levels", {})
    kz = n.get("kill_zone") or "Outside kill zone"
    ls = n.get("london_sweep")

    signal = t.get("signal")
    if not signal:
        sc15, _ = c.get("sweep_choch_15min", (None, None))
        sc1h, _ = c.get("sweep_choch_1h",    (None, None))
        ic,   _ = c.get("internal_choch",     (None, None))
        if any(x in ["bullish_reversal"] for x in [sc15, sc1h]) or ic == "bullish_internal_choch":
            signal = "BUY"
        elif any(x in ["bearish_reversal"] for x in [sc15, sc1h]) or ic == "bearish_internal_choch":
            signal = "SELL"

    bias_line = (f"{bias_emoji(b.get('daily'))}D "
                 f"{bias_emoji(b.get('4h'))}4H "
                 f"{bias_emoji(b.get('1h'))}1H "
                 f"{bias_emoji(b.get('15min'))}15m")

    if alert_type == "ENTRY" and signal:
        emoji = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
        entry, tp1, tp2 = get_entry_tp(signal, p, sl)
        sl_price = get_realistic_sl(signal, p, df_15min, df_5min)
        conf_str = get_conf_summary(c)

        fvg_key = "bull" if signal == "BUY" else "bear"
        fvg15 = p.get(f"{fvg_key}_fvg_15min_poi")
        ob1h  = p.get(f"{fvg_key}_ob_1h")
        zone_parts = []
        if fvg15: zone_parts.append(f"15min iFVG {fvg15['bot']:.2f}-{fvg15['top']:.2f}")
        if ob1h:  zone_parts.append(f"1H OB {ob1h['bot']:.2f}-{ob1h['top']:.2f}")
        zone_str = " + ".join(zone_parts) if zone_parts else "See chart"

        msg  = f"<b>⚡ {emoji} — {label}</b>\n"
        msg += f"Price: <b>{price:.2f}</b>  |  {kz}  |  {a['score']}/4 TF\n"
        msg += f"Bias: {bias_line}\n\n"
        msg += f"Zone: {zone_str}\n"
        msg += f"Confirmation: {conf_str}\n\n"
        msg += f"<b>Entry: {entry:.2f}</b>\n" if entry else "<b>Entry: Tap zone</b>\n"
        msg += f"<b>SL:    {sl_price:.2f}</b>\n" if sl_price else "<b>SL: Below zone</b>\n"
        if tp1:
            rr = calc_rr(entry, sl_price, tp1)
            msg += f"<b>TP1:   {tp1:.2f}</b>{'  (' + str(rr) + 'R)' if rr else ''}\n"
        if tp2:
            rr = calc_rr(entry, sl_price, tp2)
            msg += f"<b>TP2:   {tp2:.2f}</b>{'  (' + str(rr) + 'R)' if rr else ''}\n"
        if ls == "swept_high": msg += "\n🟡 London swept Asian HIGH\n"
        elif ls == "swept_low": msg += "\n🟡 London swept Asian LOW\n"
        if c.get("sl_near_magnet"): msg += "⚠️ SL near session level — widen SL\n"
        msg += "\n<b>All 3 criteria met — Enter now</b>"
        msg += "\nAlways confirm on chart before entering."

    elif alert_type == "SETUP" and signal:
        emoji = "🟢 BUY SETUP" if signal == "BUY" else "🔴 SELL SETUP"
        fvg_key = "bull" if signal == "BUY" else "bear"
        fvg15 = p.get(f"{fvg_key}_fvg_15min_poi") or p.get(f"{fvg_key}_fvg_15min_untapped")
        ob1h  = p.get(f"{fvg_key}_ob_1h")
        zone_parts = []
        if fvg15: zone_parts.append(f"15min iFVG {fvg15['bot']:.2f}-{fvg15['top']:.2f}")
        if ob1h:  zone_parts.append(f"1H OB {ob1h['bot']:.2f}-{ob1h['top']:.2f}")
        zone_str = " + ".join(zone_parts) if zone_parts else "See chart"
        conf_str = get_conf_summary(c)

        msg  = f"<b>⏳ {emoji} — {label}</b>\n"
        msg += f"Price: <b>{price:.2f}</b>  |  {kz}  |  {a['score']}/4 TF\n"
        msg += f"Bias: {bias_line}\n\n"
        msg += f"Zone: {zone_str}\n"
        msg += f"Criteria 1: 4H high/low swept ✅\n"
        msg += f"Criteria 2: {conf_str}\n"
        msg += f"Criteria 3: Waiting for 5M CHoCH retest...\n\n"
        msg += f"Bot will alert again when retest is confirmed."
        if ls == "swept_high": msg += "\n🟡 London swept Asian HIGH"
        elif ls == "swept_low": msg += "\n🟡 London swept Asian LOW"

    else:
        # Monitoring
        msg  = f"<b>📊 {label} — MONITORING</b>\n"
        msg += f"Price: <b>{price:.2f}</b>  |  {kz}  |  {a['score']}/4 TF\n"
        msg += f"Bias: {bias_line}\n\n"

        if n.get("amd_context"):
            msg += "⚠️ HTF ranging — wait for sweep\n"

        if a["score"] >= 2:
            direction = a["direction"]
            if direction == "bullish":
                zone = (p.get("bull_fvg_15min_poi") or p.get("bull_fvg_15min_untapped")
                        or p.get("bull_ob_1h"))
                msg += "🟢 Bullish bias\n"
                if zone: msg += f"Buy zone: {zone['bot']:.2f} - {zone['top']:.2f}\n"
                msg += "Watching for: 4H high sweep → 5M CHoCH → retest"
            elif direction == "bearish":
                zone = (p.get("bear_fvg_15min_poi") or p.get("bear_fvg_15min_untapped")
                        or p.get("bear_ob_1h"))
                msg += "🔴 Bearish bias\n"
                if zone: msg += f"Sell zone: {zone['bot']:.2f} - {zone['top']:.2f}\n"
                msg += "Watching for: 4H low sweep → 5M CHoCH → retest"
            else:
                eq_h = [e for e in p.get("equal_levels_4h", []) if e["type"] == "eq_high"]
                eq_l = [e for e in p.get("equal_levels_4h", []) if e["type"] == "eq_low"]
                msg += "⚪ Mixed bias — ranging market\n"
                if eq_h: msg += f"Watch BSL (equal highs): {eq_h[0]['level']:.2f}\n"
                if eq_l: msg += f"Watch SSL (equal lows):  {eq_l[0]['level']:.2f}\n"
        else:
            msg += "No clear bias. Sit out."

        if ls == "swept_high": msg += "\n🟡 London swept Asian HIGH → favor SELL"
        elif ls == "swept_low": msg += "\n🟡 London swept Asian LOW → favor BUY"

    return msg


def main():
    state = load_state()

    for symbol, label in SYMBOLS.items():
        print(f"\nFetching {symbol}...")
        dfs = fetch_symbol_data(symbol)

        # Wait between symbols to avoid rate limits
        if symbol != list(SYMBOLS.keys())[-1]:
            print("Waiting 15s before next symbol...")
            time.sleep(15)

        df_15min = dfs.get("15min")
        df_5min  = dfs.get("5min")

        if df_15min is None or len(df_15min) < 20:
            print(f"Not enough 15min data for {symbol}")
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
            df_5min=df_5min,
        )

        conf      = r["confirmation"]
        score     = r["alignment"]["score"]
        direction = r["alignment"]["direction"]
        poi       = r["poi"]

        sc15, _ = conf.get("sweep_choch_15min", (None, None))
        sc1h, _ = conf.get("sweep_choch_1h",    (None, None))
        ic,   _ = conf.get("internal_choch",     (None, None))
        retest  = conf.get("choch_retest", False)
        eng     = conf.get("engulfing_15min")

        has_bull_poi = (poi.get("bull_fvg_15min_poi") or poi.get("bull_fvg_15min_untapped")
                        or poi.get("bull_ob_1h") or poi.get("bull_fvg_1h"))
        has_bear_poi = (poi.get("bear_fvg_15min_poi") or poi.get("bear_fvg_15min_untapped")
                        or poi.get("bear_ob_1h") or poi.get("bear_fvg_1h"))

        bull_choch = (sc15 == "bullish_reversal" or sc1h == "bullish_reversal"
                      or ic == "bullish_internal_choch")
        bear_choch = (sc15 == "bearish_reversal" or sc1h == "bearish_reversal"
                      or ic == "bearish_internal_choch")

        # ENTRY: all 3 criteria met
        is_entry = (
            r["trade_idea"]["signal"] is not None
            or (bull_choch and has_bull_poi and (retest or eng == "bullish_engulfing") and score >= 2)
            or (bear_choch and has_bear_poi and (retest or eng == "bearish_engulfing") and score >= 2)
        )

        # SETUP: CHoCH confirmed + zone, waiting for retest
        is_setup = (
            not is_entry
            and ((bull_choch and has_bull_poi) or (bear_choch and has_bear_poi))
            and score >= 2
        )

        # MONITORING: bias + zone identified
        is_monitoring = (
            not is_entry and not is_setup
            and score >= 2
            and (has_bull_poi or has_bear_poi)
        )

        price = float(df_15min["close"].iloc[-2])

        if is_entry:
            r["alert_type"] = "ENTRY"
            msg = build_message(symbol, label, price, bar_time, r, df_15min, df_5min, "ENTRY")
            send_telegram(msg)
            print(f"⚡ ENTRY alert sent for {symbol}")

        elif is_setup:
            last_setup = state.get(symbol, {}).get("last_setup_bar")
            if last_setup != bar_time:
                r["alert_type"] = "SETUP"
                msg = build_message(symbol, label, price, bar_time, r, df_15min, df_5min, "SETUP")
                send_telegram(msg)
                state.setdefault(symbol, {})["last_setup_bar"] = bar_time
                print(f"⏳ SETUP alert sent for {symbol}")

        elif is_monitoring:
            kz = r["narrative"].get("kill_zone")
            monitor_key = f"{bar_time[:13]}_{kz}"
            last_monitor = state.get(symbol, {}).get("last_monitor_session")
            if last_monitor != monitor_key:
                r["alert_type"] = "MONITORING"
                msg = build_message(symbol, label, price, bar_time, r, df_15min, df_5min, "MONITORING")
                send_telegram(msg)
                state.setdefault(symbol, {})["last_monitor_session"] = monitor_key
                print(f"📊 MONITORING alert sent for {symbol}")
        else:
            print(f"No signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
