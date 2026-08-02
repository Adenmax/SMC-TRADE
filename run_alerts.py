import os
import json
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


import time

def parse_df(values):
    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df.sort_values("datetime").reset_index(drop=True)


def fetch_all_timeframes(symbols, timeframes, outputsize=100):
    """
    Fetch all symbols and timeframes using batch requests.
    Twelve Data batch: comma-separated symbols = 1 API credit per symbol.
    One request per timeframe = 5 requests total for both symbols.
    5 requests < 8/min limit.
    Returns: {symbol: {tf: df}}
    """
    symbol_str = ",".join(symbols)
    result = {s: {} for s in symbols}

    for i, tf in enumerate(timeframes):
        if i > 0:
            time.sleep(10)  # stay well under rate limit
        try:
            resp = requests.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": symbol_str, "interval": tf,
                        "outputsize": outputsize, "apikey": TWELVE_DATA_API_KEY,
                        "order": "ASC"},
                timeout=30,
            )
            data = resp.json()
            if data.get("code") == 429:
                print(f"Rate limit on {tf}, waiting 30s...")
                time.sleep(30)
                continue
            # Batch response: {symbol: {values: [...]}}
            # Single symbol response: {values: [...]}
            for sym in symbols:
                if sym in data and "values" in data[sym]:
                    result[sym][tf] = parse_df(data[sym]["values"])
                elif "values" in data and len(symbols) == 1:
                    result[sym][tf] = parse_df(data["values"])
                else:
                    print(f"No data for {sym} {tf}")
        except Exception as e:
            print(f"Fetch error {tf}: {e}")

    return result


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


def get_realistic_sl(signal, poi, df_15min, buffer_pct=0.0005):
    """
    SL based on RECENT local structure — not a distant CHoCH.
    For BUY: just below the nearest recent swing low or OB low.
    For SELL: just above the nearest recent swing high or OB top.
    Uses last 20 bars of 15min to find local structure.
    """
    sl = None

    if df_15min is not None and len(df_15min) >= 10:
        recent_lows  = df_15min["low"].values[-20:]
        recent_highs = df_15min["high"].values[-20:]

        if signal == "BUY":
            # SL = below the lowest recent swing low in last 20 bars
            local_low = float(min(recent_lows))
            # But not more than 2% below current price (realistic)
            current = float(df_15min["close"].iloc[-2])
            if (current - local_low) / current < 0.02:
                sl = local_low * (1 - buffer_pct)

        elif signal == "SELL":
            local_high = float(max(recent_highs))
            current = float(df_15min["close"].iloc[-2])
            if (local_high - current) / current < 0.02:
                sl = local_high * (1 + buffer_pct)

    # Fallback to OB if local structure SL is too wide
    if sl is None:
        if signal == "BUY":
            ob = poi.get("bull_ob_1h")
            fvg = poi.get("bull_fvg_15min_poi") or poi.get("bull_fvg_1h")
            if fvg:
                sl = fvg["bot"] * (1 - buffer_pct)
            elif ob:
                sl = ob["bot"] * (1 - buffer_pct)
        elif signal == "SELL":
            ob = poi.get("bear_ob_1h")
            fvg = poi.get("bear_fvg_15min_poi") or poi.get("bear_fvg_1h")
            if fvg:
                sl = fvg["top"] * (1 + buffer_pct)
            elif ob:
                sl = ob["top"] * (1 + buffer_pct)

    return sl


def get_entry_and_tp(signal, poi, session_levels):
    entry = tp1 = tp2 = None

    if signal == "BUY":
        fvg = poi.get("bull_fvg_15min_poi")
        fvg1h = poi.get("bull_fvg_1h")
        ob = poi.get("bull_ob_1h")
        if fvg:
            entry = round(fvg["top"], 5)
        elif fvg1h:
            entry = round(fvg1h["top"], 5)
        elif ob:
            entry = round((ob["top"] + ob["bot"]) / 2, 5)

        # TP1 = nearest session HIGH above entry
        candidates = []
        for name, lvl in session_levels.items():
            if entry and lvl["high"] > entry:
                candidates.append((name, lvl["high"], lvl["high"] - entry))
        if candidates:
            candidates.sort(key=lambda x: x[2])
            tp1 = round(candidates[0][1], 5)

        # TP2 = prev day high or equal highs
        ph = poi.get("prev_session_high")
        eq = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_high"]
        if ph and entry and ph > entry:
            tp2 = round(ph, 5)
        elif eq and entry:
            tp2 = round(eq[0]["level"], 5)

    elif signal == "SELL":
        fvg = poi.get("bear_fvg_15min_poi")
        fvg1h = poi.get("bear_fvg_1h")
        ob = poi.get("bear_ob_1h")
        if fvg:
            entry = round(fvg["bot"], 5)
        elif fvg1h:
            entry = round(fvg1h["bot"], 5)
        elif ob:
            entry = round((ob["top"] + ob["bot"]) / 2, 5)

        # TP1 = nearest session LOW below entry
        candidates = []
        for name, lvl in session_levels.items():
            if entry and lvl["low"] < entry:
                candidates.append((name, lvl["low"], entry - lvl["low"]))
        if candidates:
            candidates.sort(key=lambda x: x[2])
            tp1 = round(candidates[0][1], 5)

        pl = poi.get("prev_session_low")
        eq = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_low"]
        if pl and entry and pl < entry:
            tp2 = round(pl, 5)
        elif eq and entry:
            tp2 = round(eq[0]["level"], 5)

    return entry, tp1, tp2


def calc_rr(entry, sl, tp):
    if entry and sl and tp:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk > 0:
            return round(reward / risk, 1)
    return None


def get_confirmation_summary(conf):
    """Single line confirmation status."""
    sc15, _ = conf.get("sweep_choch_15min", (None, None))
    sc1h, _ = conf.get("sweep_choch_1h",    (None, None))
    ic,   _ = conf.get("internal_choch",     (None, None))
    eng     = conf.get("engulfing_15min")
    retest  = conf.get("choch_retest", False)

    parts = []
    if sc15 in ("bullish_reversal", "bearish_reversal"):
        direction = "Bull" if sc15 == "bullish_reversal" else "Bear"
        parts.append(f"{direction} CHoCH (15min) ✅")
    if sc1h in ("bullish_reversal", "bearish_reversal"):
        direction = "Bull" if sc1h == "bullish_reversal" else "Bear"
        parts.append(f"{direction} CHoCH (1H) ✅")
    if ic in ("bullish_internal_choch", "bearish_internal_choch"):
        direction = "Bull" if "bullish" in ic else "Bear"
        parts.append(f"{direction} internal CHoCH (5min) ✅")
    if retest:
        parts.append("Retest confirmed ✅")
    if eng == "bullish_engulfing":
        parts.append("Bull engulfing ✅")
    if eng == "bearish_engulfing":
        parts.append("Bear engulfing ✅")
    return ", ".join(parts) if parts else "Waiting for confirmation"


def build_message(symbol, label, price, bar_time, r, df_15min):
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

    # ── CLEAN SIMPLE FORMAT ──────────────────────────────────────────────────
    if signal:
        emoji = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
        entry, tp1, tp2 = get_entry_and_tp(signal, p, sl)
        sl_price = get_realistic_sl(signal, p, df_15min)

        rr1 = calc_rr(entry, sl_price, tp1)
        rr2 = calc_rr(entry, sl_price, tp2)

        conf_summary = get_confirmation_summary(c)
        quality = t.get("quality", "")

        # Bias line
        bias_line = f"{bias_emoji(b.get('daily'))}D " \
                    f"{bias_emoji(b.get('4h'))}4H " \
                    f"{bias_emoji(b.get('1h'))}1H " \
                    f"{bias_emoji(b.get('15min'))}15m"

        # Zone description
        fvg15 = p.get(f"{'bull' if signal == 'BUY' else 'bear'}_fvg_15min_poi")
        ob1h  = p.get(f"{'bull' if signal == 'BUY' else 'bear'}_ob_1h")
        zone_parts = []
        if fvg15:
            zone_parts.append(f"15min iFVG {fvg15['bot']:.2f}-{fvg15['top']:.2f}")
        if ob1h:
            zone_parts.append(f"1H OB {ob1h['bot']:.2f}-{ob1h['top']:.2f}")
        zone_str = " + ".join(zone_parts) if zone_parts else "See chart"

        msg  = f"<b>{emoji} — {label}</b>\n"
        msg += f"Price: <b>{price:.2f}</b>  |  {kz}  |  {a['score']}/4 TF\n"
        msg += f"Bias: {bias_line}\n\n"

        msg += f"<b>ZONE:</b> {zone_str}\n"
        msg += f"<b>Confirmation:</b> {conf_summary}\n\n"

        msg += f"<b>Entry:</b>  {entry:.2f}\n" if entry else "<b>Entry:</b>  Tap zone first\n"
        msg += f"<b>SL:</b>     {sl_price:.2f}\n" if sl_price else "<b>SL:</b>     Below zone\n"
        if tp1:
            rr_str = f"  ({rr1}R)" if rr1 else ""
            msg += f"<b>TP1:</b>    {tp1:.2f}{rr_str}\n"
        if tp2:
            rr_str = f"  ({rr2}R)" if rr2 else ""
            msg += f"<b>TP2:</b>    {tp2:.2f}{rr_str}\n"

        msg += f"\n<b>Quality:</b> {quality}\n"

        if ls == "swept_high":
            msg += "🟡 London swept Asian HIGH\n"
        elif ls == "swept_low":
            msg += "🟡 London swept Asian LOW\n"

        if c.get("sl_near_magnet"):
            msg += "⚠️ SL near session level — widen SL\n"

        if n.get("amd_context"):
            msg += "⚠️ HTF ranging — watch for fake CHoCH\n"

        msg += "\n<b>Next step:</b> "
        if c.get("choch_retest"):
            msg += "CHoCH retest done → enter now at Entry price"
        else:
            msg += f"On 1/3/5min → wait for CHoCH body close → retest → enter"

        msg += "\n\nAlways confirm on chart before entering."

    else:
        # No trade — keep it very short
        bias_line = f"{bias_emoji(b.get('daily'))}D " \
                    f"{bias_emoji(b.get('4h'))}4H " \
                    f"{bias_emoji(b.get('1h'))}1H " \
                    f"{bias_emoji(b.get('15min'))}15m"

        msg  = f"<b>📊 {label} — MONITORING</b>\n"
        msg += f"Price: <b>{price:.2f}</b>  |  {kz}\n"
        msg += f"Bias: {bias_line}  ({a['score']}/4)\n\n"

        if n.get("amd_context"):
            msg += "⚠️ HTF ranging — AMD context\n"
            msg += "Wait for London/NY to sweep a session level\n"
            eq_h = [e for e in p.get("equal_levels_4h", []) if e["type"] == "eq_high"]
            eq_l = [e for e in p.get("equal_levels_4h", []) if e["type"] == "eq_low"]
            if eq_h:
                msg += f"Watch equal highs (BSL): {eq_h[0]['level']:.2f}\n"
            if eq_l:
                msg += f"Watch equal lows (SSL):  {eq_l[0]['level']:.2f}\n"
        elif a["score"] >= 3:
            direction = a["direction"]
            if direction == "bullish":
                ob  = p.get("bull_ob_1h")
                fvg = p.get("bull_fvg_15min_poi") or p.get("bull_fvg_1h")
                zone = fvg or ob
                msg += f"🟢 Bias BULLISH — waiting for buy zone tap\n"
                if zone:
                    msg += f"Buy zone: {zone['bot']:.2f} - {zone['top']:.2f}\n"
                msg += "Once tapped → 1/3/5min CHoCH body close → retest → BUY"
            else:
                ob  = p.get("bear_ob_1h")
                fvg = p.get("bear_fvg_15min_poi") or p.get("bear_fvg_1h")
                zone = fvg or ob
                msg += f"🔴 Bias BEARISH — waiting for sell zone tap\n"
                if zone:
                    msg += f"Sell zone: {zone['bot']:.2f} - {zone['top']:.2f}\n"
                msg += "Once tapped → 1/3/5min CHoCH body close → retest → SELL"
        else:
            msg += "No clear bias. Sit out and wait for next alert."

        if ls == "swept_high":
            msg += "\n🟡 London swept Asian HIGH → favor SELL"
        elif ls == "swept_low":
            msg += "\n🟡 London swept Asian LOW → favor BUY"

    return msg


def main():
    state = load_state()

    # Fetch all symbols and timeframes in batch (5 requests total)
    all_data = fetch_all_timeframes(list(SYMBOLS.keys()), TIMEFRAMES)

    for symbol, label in SYMBOLS.items():
        dfs = all_data.get(symbol, {})

        df_15min = dfs.get("15min")
        if df_15min is None or len(df_15min) < 20:
            print(f"Not enough bars for {symbol}")
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

        conf = r["confirmation"]
        sc15, _ = conf.get("sweep_choch_15min", (None, None))
        sc1h, _ = conf.get("sweep_choch_1h",    (None, None))
        ic,   _ = conf.get("internal_choch",     (None, None))

        # Only alert when FULL confirmation is complete:
        # 1. Trade idea exists (zone + bias + CHoCH detected)
        # 2. CHoCH retest confirmed OR engulfing candle (entry-ready)
        # Bot stays silent until all conditions are met.
        retest_done = conf.get("choch_retest", False)
        engulfing   = conf.get("engulfing_15min")
        trade_ready = r["trade_idea"]["signal"] is not None

        has_signal = trade_ready and (retest_done or engulfing is not None)

        # Monitoring alert: bias confirmed + zone exists but waiting
        # Send once per session so you know what to watch
        monitoring = (
            not has_signal
            and r["alignment"]["score"] >= 3
            and (
                r["poi"].get("bull_fvg_15min_poi") is not None
                or r["poi"].get("bear_fvg_15min_poi") is not None
                or r["poi"].get("bull_ob_1h") is not None
                or r["poi"].get("bear_ob_1h") is not None
            )
        )

        if has_signal:
            price = float(df_15min["close"].iloc[-2])
            msg   = build_message(symbol, label, price, bar_time, r, df_15min)
            send_telegram(msg)
            print(f"Alert sent for {symbol} at {bar_time}")
        elif monitoring:
            last_monitor = state.get(symbol, {}).get("last_monitor_session")
            kz = r["narrative"].get("kill_zone")
            monitor_key = f"{bar_time[:13]}_{kz}"
            if last_monitor != monitor_key:
                price = float(df_15min["close"].iloc[-2])
                msg   = build_message(symbol, label, price, bar_time, r, df_15min)
                send_telegram(msg)
                state.setdefault(symbol, {})["last_monitor_session"] = monitor_key
                print(f"Monitoring alert sent for {symbol}")
        else:
            print(f"No signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
