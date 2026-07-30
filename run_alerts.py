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


def fetch_bars(symbol, interval, outputsize=100):
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol": symbol, "interval": interval,
                "outputsize": outputsize, "apikey": TWELVE_DATA_API_KEY,
                "order": "ASC"},
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
    if b == "bullish": return "🟢"
    if b == "bearish": return "🔴"
    return "⚪"


def get_entry_levels(signal, poi, conf, trade):
    entry = sl = tp1 = tp2 = None
    buf = 0.0003

    sl = trade.get("sl_price")

    if signal == "BUY":
        f15 = poi.get("bull_fvg_15min_poi") or conf.get("bull_fvg_15min")
        f1h = poi.get("bull_fvg_1h")
        ob  = poi.get("bull_ob_1h")
        if f15:
            entry = f15["top"]
        elif f1h:
            entry = f1h["top"]
        elif ob:
            entry = (ob["top"] + ob["bot"]) / 2
        tp1 = poi.get("prev_session_high")
        eq  = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_high"]
        tp2 = eq[0]["level"] if eq else None
        ns  = poi.get("nearest_session_zone")
        if ns and ns[1] and (tp1 is None or abs(ns[1] - (entry or 0)) < abs((tp1 or 0) - (entry or 0))):
            tp1 = ns[1]

    if signal == "SELL":
        f15 = poi.get("bear_fvg_15min_poi") or conf.get("bear_fvg_15min")
        f1h = poi.get("bear_fvg_1h")
        ob  = poi.get("bear_ob_1h")
        if f15:
            entry = f15["bot"]
        elif f1h:
            entry = f1h["bot"]
        elif ob:
            entry = (ob["top"] + ob["bot"]) / 2
        tp1 = poi.get("prev_session_low")
        eq  = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_low"]
        tp2 = eq[0]["level"] if eq else None
        ns  = poi.get("nearest_session_zone")
        if ns and ns[1] and (tp1 is None or abs(ns[1] - (entry or 0)) < abs((tp1 or 0) - (entry or 0))):
            tp1 = ns[1]

    rr1 = rr2 = None
    if entry and sl and tp1:
        risk = abs(entry - sl)
        if risk > 0:
            rr1 = abs(tp1 - entry) / risk
    if entry and sl and tp2:
        risk = abs(entry - sl)
        if risk > 0:
            rr2 = abs(tp2 - entry) / risk

    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr1": rr1, "rr2": rr2}


def build_watch_next(signal, score, direction, poi, conf, ls, session_levels):
    lines = ["\n<b>WHAT TO DO NOW</b>"]
    eq_highs = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_high"]
    eq_lows  = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_low"]
    ph = poi.get("prev_session_high")
    pl = poi.get("prev_session_low")
    retest = conf.get("choch_retest", False)

    if signal:
        lines.append("✅ Setup detected. Your action plan:")
        if signal == "BUY":
            lines.append("1. Open TradingView → XAU/USD or GBP/JPY → 15min chart")
            lines.append("2. Find the Bullish OB or 15min iFVG BUY zone")
            lines.append("3. Wait for price to tap into the zone")
            lines.append("4. On the 1, 3, or 5min chart look for:")
            lines.append("   → Lower lows forming, then price breaks the most recent lower high")
            lines.append("   → BODY candle close above that lower high = CHoCH confirmed")
            if retest:
                lines.append("   ★ CHoCH retest already detected — entry may be now")
            else:
                lines.append("   → Then wait for a RETEST of that level before entering")
            lines.append("5. Place BUY LIMIT at entry price shown below")
            lines.append("6. SL below the CHoCH structural low")
            lines.append("7. TP1 = nearest session high ($$$ magnet)")
            lines.append("8. Move SL to breakeven after TP1 hit")
        else:
            lines.append("1. Open TradingView → XAU/USD or GBP/JPY → 15min chart")
            lines.append("2. Find the Bearish OB or 15min iFVG SELL zone")
            lines.append("3. Wait for price to tap into the zone")
            lines.append("4. On the 1, 3, or 5min chart look for:")
            lines.append("   → Higher highs forming, then price breaks the most recent higher low")
            lines.append("   → BODY candle close below that higher low = CHoCH confirmed")
            if retest:
                lines.append("   ★ CHoCH retest already detected — entry may be now")
            else:
                lines.append("   → Then wait for a RETEST of that level before entering")
            lines.append("5. Place SELL LIMIT at entry price shown below")
            lines.append("6. SL above the CHoCH structural high")
            lines.append("7. TP1 = nearest session low ($$$ magnet)")
            lines.append("8. Move SL to breakeven after TP1 hit")

    elif score <= 1:
        lines.append("⚪ Timeframes MIXED — this is a ranging market.")
        lines.append("DO NOT trade. Watch for price to sweep one of these levels:")
        if eq_highs:
            lines.append(f"  $$$ Equal Highs at {eq_highs[0]['level']:.5f}")
            lines.append("      → Wick above + body close below = BEARISH SWEEP")
            lines.append("        Then look for SELL setup on 1/3/5min CHoCH")
        if eq_lows:
            lines.append(f"  $$$ Equal Lows at {eq_lows[0]['level']:.5f}")
            lines.append("      → Wick below + body close above = BULLISH SWEEP")
            lines.append("        Then look for BUY setup on 1/3/5min CHoCH")
        lines.append("  Wait for next alert when 3+ TFs agree on direction.")

    elif score >= 3 and direction == "bullish":
        lines.append("🟢 Bias BULLISH. Waiting for price to tap buy zone.")
        ob  = poi.get("bull_ob_1h")
        fvg = poi.get("bull_fvg_15min_poi") or poi.get("bull_fvg_1h")
        if fvg:
            lines.append(f"  Watch BUY ZONE: {fvg['bot']:.5f} - {fvg['top']:.5f}")
        elif ob:
            lines.append(f"  Watch Bullish OB: {ob['bot']:.5f} - {ob['top']:.5f}")
        if pl:
            lines.append(f"  Also watching session low $$$ at {pl:.5f}")
        lines.append("  Once tapped → look for 1/3/5min CHoCH body close → retest → BUY")

    elif score >= 3 and direction == "bearish":
        lines.append("🔴 Bias BEARISH. Waiting for price to tap sell zone.")
        ob  = poi.get("bear_ob_1h")
        fvg = poi.get("bear_fvg_15min_poi") or poi.get("bear_fvg_1h")
        if fvg:
            lines.append(f"  Watch SELL ZONE: {fvg['bot']:.5f} - {fvg['top']:.5f}")
        elif ob:
            lines.append(f"  Watch Bearish OB: {ob['bot']:.5f} - {ob['top']:.5f}")
        if ph:
            lines.append(f"  Also watching session high $$$ at {ph:.5f}")
        lines.append("  Once tapped → look for 1/3/5min CHoCH body close → retest → SELL")

    if ls == "swept_high":
        lines.append("\n🟡 London swept Asian HIGH → bias for DOWN this session")
    elif ls == "swept_low":
        lines.append("\n🟡 London swept Asian LOW → bias for UP this session")

    lines.append("\nOpen TradingView to verify before entering any trade.")
    return "\n".join(lines)


def build_message(symbol, label, price, bar_time, r):
    a   = r["alignment"]
    b   = r["bias"]
    n   = r["narrative"]
    p   = r["poi"]
    c   = r["confirmation"]
    t   = r["trade_idea"]
    d   = r["decision"]
    kz  = n.get("kill_zone") or "Outside kill zone"
    ls  = n.get("london_sweep")
    fvgm = n.get("fvg_momentum")
    amd  = n.get("amd_context", False)
    sl   = n.get("session_levels", {})

    msg  = f"<b>SMC ALERT — {label}</b>\n"
    msg += f"Price: <b>{price:.5f}</b> | {bar_time}\n\n"

    # PILLAR 1
    msg += "<b>PILLAR 1 — BIAS</b>\n"
    msg += f"  Daily  {bias_emoji(b.get('daily'))} {str(b.get('daily','-')).upper()}\n"
    msg += f"  4H     {bias_emoji(b.get('4h'))} {str(b.get('4h','-')).upper()}\n"
    msg += f"  1H     {bias_emoji(b.get('1h'))} {str(b.get('1h','-')).upper()}\n"
    msg += f"  15min  {bias_emoji(b.get('15min'))} {str(b.get('15min','-')).upper()}\n"
    msg += f"  → <b>{a['label']}</b> ({a['score']}/4)\n\n"

    # PILLAR 2
    msg += "<b>PILLAR 2 — NARRATIVE</b>\n"
    msg += f"  Kill zone: <b>{kz}</b>\n"
    if amd:
        msg += "  ⚠️ HTF ranging — AMD context (wait for manipulation sweep)\n"
    if ls == "swept_high":
        msg += "  🟡 London swept Asian HIGH → expect move DOWN\n"
    elif ls == "swept_low":
        msg += "  🟡 London swept Asian LOW → expect move UP\n"
    if fvgm:
        msg += f"  {fvgm}\n"

    # Session liquidity ($$$ markers)
    if sl:
        msg += "\n  <b>$$$ Session Liquidity Levels:</b>\n"
        for sname, slvl in sl.items():
            msg += f"    {sname}: H={slvl['high']:.5f}  L={slvl['low']:.5f}\n"

    # PILLAR 3
    msg += "\n<b>PILLAR 3 — POINTS OF INTEREST</b>\n"
    bull_ob  = p.get("bull_ob_1h")
    bear_ob  = p.get("bear_ob_1h")
    bull_fvg = p.get("bull_fvg_1h")
    bear_fvg = p.get("bear_fvg_1h")
    b15_poi  = p.get("bull_fvg_15min_poi")
    br15_poi = p.get("bear_fvg_15min_poi")
    b15_un   = p.get("bull_fvg_15min_untapped")
    br15_un  = p.get("bear_fvg_15min_untapped")

    if b15_poi:
        msg += f"  ★ BUY ZONE (15min iFVG): {b15_poi['bot']:.5f} - {b15_poi['top']:.5f}\n"
    if br15_poi:
        msg += f"  ★ SELL ZONE (15min iFVG): {br15_poi['bot']:.5f} - {br15_poi['top']:.5f}\n"
    if bull_ob:
        msg += f"  Bullish OB (1H): {bull_ob['bot']:.5f} - {bull_ob['top']:.5f}\n"
    if bull_fvg:
        msg += f"  Bullish FVG (1H): {bull_fvg['bot']:.5f} - {bull_fvg['top']:.5f}\n"
    if bear_ob:
        msg += f"  Bearish OB (1H): {bear_ob['bot']:.5f} - {bear_ob['top']:.5f}\n"
    if bear_fvg:
        msg += f"  Bearish FVG (1H): {bear_fvg['bot']:.5f} - {bear_fvg['top']:.5f}\n"
    if b15_un:
        msg += f"  Bull FVG target (15min): {b15_un['bot']:.5f} - {b15_un['top']:.5f}\n"
    if br15_un:
        msg += f"  Bear FVG target (15min): {br15_un['bot']:.5f} - {br15_un['top']:.5f}\n"

    all_bprs = p.get("bpr_1h", []) + p.get("bpr_15min", [])
    for bpr in all_bprs[:2]:
        msg += f"  ⚡ BPR ({bpr['direction'].upper()}): {bpr['bot']:.5f} - {bpr['top']:.5f}\n"

    ph = p.get("prev_session_high")
    pl = p.get("prev_session_low")
    if ph:
        msg += f"  Prev day high: {ph:.5f} (BSL)\n"
    if pl:
        msg += f"  Prev day low:  {pl:.5f} (SSL)\n"

    for lvl in p.get("equal_levels_4h", [])[:2]:
        tag = "Equal Highs" if lvl["type"] == "eq_high" else "Equal Lows"
        msg += f"  $$$ {tag}: {lvl['level']:.5f} ({lvl['count']}x)\n"

    ns = p.get("nearest_session_zone")
    if ns and ns[1]:
        msg += f"  → Nearest $$$ magnet: {ns[0]} at {ns[1]:.5f}\n"

    # PILLAR 4
    msg += "\n<b>PILLAR 4 — CONFIRMATION</b>\n"
    sc15_r, cl15 = c.get("sweep_choch_15min", (None, None))
    sc1h_r, cl1h = c.get("sweep_choch_1h",    (None, None))
    ic_r, icl    = c.get("internal_choch",     (None, None))
    eng          = c.get("engulfing_15min")
    retest       = c.get("choch_retest", False)
    sl_mag       = c.get("sl_near_magnet", False)

    if sc15_r == "bullish_reversal":
        msg += f"  ✅ [1] Sweep+CHoCH (15min) BULLISH — body close confirmed\n"
        if cl15: msg += f"      CHoCH level: {cl15:.5f}\n"
    if sc15_r == "bearish_reversal":
        msg += f"  ✅ [1] Sweep+CHoCH (15min) BEARISH — body close confirmed\n"
        if cl15: msg += f"      CHoCH level: {cl15:.5f}\n"
    if sc1h_r == "bullish_reversal":
        msg += f"  ✅ [1] Sweep+CHoCH (1H) BULLISH — body close confirmed\n"
        if cl1h: msg += f"      CHoCH level: {cl1h:.5f}\n"
    if sc1h_r == "bearish_reversal":
        msg += f"  ✅ [1] Sweep+CHoCH (1H) BEARISH — body close confirmed\n"
        if cl1h: msg += f"      CHoCH level: {cl1h:.5f}\n"
    if ic_r == "bullish_internal_choch":
        msg += f"  ✅ [1] Internal CHoCH (5min) BULLISH\n"
    if ic_r == "bearish_internal_choch":
        msg += f"  ✅ [1] Internal CHoCH (5min) BEARISH\n"
    if retest:
        msg += "  ✅ [2] CHoCH retest complete — HIGHEST CONFIDENCE\n"
    if eng == "bullish_engulfing":
        msg += "  ✅ [3] Bullish engulfing (15min)\n"
    if eng == "bearish_engulfing":
        msg += "  ✅ [3] Bearish engulfing (15min)\n"
    if sl_mag:
        msg += "  ⚠️ SL near session level (magnet) — adjust or skip!\n"
    if not any([sc15_r, sc1h_r, ic_r, eng]):
        msg += "  ⏳ No confirmation yet — wait for 1/3/5min CHoCH\n"

    msg += "\n——————————————————————\n"

    # TRADE IDEA
    if t["signal"]:
        lvls = get_entry_levels(t["signal"], p, c, t)
        direction = "BUY" if t["signal"] == "BUY" else "SELL"
        msg += f"<b>TRADE: {direction}</b>  Quality: {t['quality']}\n\n"
        if lvls["entry"]:
            msg += f"  Entry:  <b>{lvls['entry']:.5f}</b>\n"
        else:
            msg += "  Entry:  Wait for price to tap zone\n"
        if lvls["sl"]:
            msg += f"  SL:     <b>{lvls['sl']:.5f}</b>\n"
        if lvls["tp1"]:
            rr = f" ({lvls['rr1']:.1f}R)" if lvls["rr1"] else ""
            msg += f"  TP1:    <b>{lvls['tp1']:.5f}</b>{rr}\n"
        if lvls["tp2"]:
            rr = f" ({lvls['rr2']:.1f}R)" if lvls["rr2"] else ""
            msg += f"  TP2:    <b>{lvls['tp2']:.5f}</b>{rr}\n"
        if t.get("counter_trend"):
            msg += "  ⚡ Counter-trend setup — use tight SL\n"
    else:
        msg += "<b>NO TRADE YET</b>\n"

    # 5TH QUESTION — WILL I TAKE THIS TRADE?
    if d.get("pros") or d.get("cons"):
        msg += "\n<b>WILL I TAKE THIS TRADE?</b>\n"
        for pro in d.get("pros", []):
            msg += f"  ✅ {pro}\n"
        for con in d.get("cons", []):
            msg += f"  ❌ {con}\n"
        msg += f"\n  → <b>{d.get('recommendation','')}</b>\n"

    # WHAT TO DO NOW
    msg += build_watch_next(
        t["signal"], a["score"], a["direction"], p, c, ls,
        n.get("session_levels", {}))

    msg += "\n\nAlways confirm on chart. Manage your risk."
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

        has_signal = (
            r["trade_idea"]["signal"] is not None
            or sc15 is not None
            or sc1h is not None
            or ic is not None
            or conf.get("engulfing_15min") is not None
        )

        if has_signal:
            price = float(df_15min["close"].iloc[-2])
            msg   = build_message(symbol, label, price, bar_time, r)
            send_telegram(msg)
            print(f"Alert sent for {symbol} at {bar_time}")
        else:
            print(f"No signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
