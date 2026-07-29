import os, json, requests, pandas as pd
from smc_logic import run_full_analysis

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE          = "state.json"

SYMBOLS    = {"XAU/USD": "Gold (XAU/USD)", "GBP/JPY": "GBP/JPY"}
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
    return {"bullish": "🟢", "bearish": "🔴", "ranging": "⚪"}.get(b, "⚪")


def get_entry_levels(signal, poi, conf):
    entry = None
    sl = None
    tp1 = None
    tp2 = None
    buffer_pct = 0.0003

    if signal == "BUY":
        fvg15 = conf.get("bull_fvg_15min")
        fvg1h = poi.get("bull_fvg_1h")
        ob = poi.get("bull_ob_1h")
        if fvg15:
            entry = fvg15["top"]
            sl = fvg15["bot"] * (1 - buffer_pct)
        elif fvg1h:
            entry = fvg1h["top"]
            sl = fvg1h["bot"] * (1 - buffer_pct)
        elif ob:
            entry = (ob["top"] + ob["bot"]) / 2
            sl = ob["bot"] * (1 - buffer_pct)
        tp1 = poi.get("prev_session_high")
        eq = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_high"]
        tp2 = eq[0]["level"] if eq else None

    elif signal == "SELL":
        fvg15 = conf.get("bear_fvg_15min")
        fvg1h = poi.get("bear_fvg_1h")
        ob = poi.get("bear_ob_1h")
        if fvg15:
            entry = fvg15["bot"]
            sl = fvg15["top"] * (1 + buffer_pct)
        elif fvg1h:
            entry = fvg1h["bot"]
            sl = fvg1h["top"] * (1 + buffer_pct)
        elif ob:
            entry = (ob["top"] + ob["bot"]) / 2
            sl = ob["top"] * (1 + buffer_pct)
        tp1 = poi.get("prev_session_low")
        eq = [e for e in poi.get("equal_levels_4h", []) if e["type"] == "eq_low"]
        tp2 = eq[0]["level"] if eq else None

    rr1 = None
    rr2 = None
    if entry and sl and tp1:
        risk = abs(entry - sl)
        if risk > 0:
            rr1 = abs(tp1 - entry) / risk
    if entry and sl and tp2:
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

    lines = [
        f"<b>📡 SMC ALERT v2 — {label}</b>",
        f"Price: <b>{price:.5f}</b>  |  {bar_time}",
        "",
        f"<b>【 PILLAR 1 — BIAS 】</b>",
        f"  Daily  {bias_emoji(b.get('daily'))}  {b.get('daily','?').upper()}",
        f"  4H     {bias_emoji(b.get('4h'))}  {b.get('4h','?').upper()}",
        f"  1H     {bias_emoji(b.get('1h'))}  {b.get('1h','?').upper()}",
        f"  15min  {bias_emoji(b.get('15min'))}  {b.get('15min','?').upper()}",
        f"  → <b>{a['label']}</b>  ({a['score']}/4 aligned)",
        "",
        f"<b>【 PILLAR 2 — NARRATIVE 】</b>",
        f"  Kill zone: <b>{kz}</b>",
    ]

    if ls == "swept_high":
        lines.append("  🟡 London swept Asian HIGH → expect move DOWN")
    elif ls == "swept_low":
        lines.append("  🟡 London swept Asian LOW → expect move UP")
    if fvgm:
        lines.append(f"  📊 {fvgm}")

    lines += ["", "<b>【 PILLAR 3 — POINTS OF INTEREST 】</b>"]

    bull_ob = p.get("bull_ob_1h")
    bear_ob = p.get("bear_ob_1h")
    bull_fvg = p.get("bull_fvg_1h")
    bear_fvg = p.get("bear_fvg_1h")
    ph = p.get("prev_session_high")
    pl = p.get("prev_session_low")

    if bull_ob:
        lines.append(f"  🟢 Bullish OB (1H): {bull_ob['bot']:.5f} – {bull_ob['top']:.5f}")
    if bull_fvg:
        lines.append(f"  🔵 Bullish FVG (1H): {bull_fvg['bot']:.5f} – {bull_fvg['top']:.5f}")
    if bear_ob:
        lines.append(f"  🔴 Bearish OB (1H): {bear_ob['bot']:.5f} – {bear_ob['top']:.5f}")
    if bear_fvg:
        lines.append(f"  🟠 Bearish FVG (1H): {bear_fvg['bot']:.5f} – {bear_fvg['top']:.5f}")
    if ph:
        lines.append(f"  📌 Prev session high: {ph:.5f}  (BSL target)")
    if pl:
        lines.append(f"  📌 Prev session low:  {pl:.5f}  (SSL target)")
    for lvl in p.get("equal_levels_4h", [])[:2]:
        tag = "Equal Highs (BSL)" if lvl["type"] == "eq_high" else "Equal Lows (SSL)"
        lines.append(f"  ⚡ {tag}: {lvl['level']:.5f}  ({lvl['count']}x touched)")

    lines += ["", "<b>【 PILLAR 4 — CONFIRMATION 】</b>"]

    sc15 = c.get("sweep_choch_15min")
    sc1h = c.get("sweep_choch_1h")
    eng = c.get("engulfing_15min")
    bf15 = c.get("bull_fvg_15min")
    brf15 = c.get("bear_fvg_15min")

    if sc15 == "bullish_reversal":
        lines.append("  ✅ Sweep + CHoCH (15min) → BULLISH REVERSAL confirmed")
    if sc15 == "bearish_reversal":
        lines.append("  ✅ Sweep + CHoCH (15min) → BEARISH REVERSAL confirmed")
    if sc1h == "bullish_reversal":
        lines.append("  ✅ Sweep + CHoCH (1H) → BULLISH REVERSAL confirmed")
    if sc1h == "bearish_reversal":
        lines.append("  ✅ Sweep + CHoCH (1H) → BEARISH REVERSAL confirmed")
    if eng == "bullish_engulfing":
        lines.append("  📗 Bullish engulfing candle (15min)")
    if eng == "bearish_engulfing":
        lines.append("  📕 Bearish engulfing candle (15min)")
    if bf15:
        lines.append(f"  🔵 LTF Bull FVG (15min): {bf15['bot']:.5f} – {bf15['top']:.5f}")
    if brf15:
        lines.append(f"  🟠 LTF Bear FVG (15min): {brf15['bot']:.5f} – {brf15['top']:.5f}")
    if not any([sc15, sc1h, eng, bf15, brf15]):
        lines.append("  ⏳ No confirmation yet — wait for entry trigger")

    lines += ["", "─" * 30]

    if t["signal"]:
        lvls = get_entry_levels(t["signal"], p, c)
        emoji = "🟢 BUY" if t["signal"] == "BUY" else "🔴 SELL"
        lines += [
            f"<b>🎯 TRADE IDEA: {emoji}</b>",
            f"  Quality:  <b>{t['quality']}</b>",
            "",
        ]
        if lvls["entry"]:
            lines.append(f"  📍 Entry:   <b>{lvls['entry']:.5f}</b>")
        else:
            lines.append(f"  📍 Entry:   Wait for price to reach POI zone")
        if lvls["sl"]:
            lines.append(f"  🛑 SL:      <b>{lvls['sl']:.5f}</b>")
        else:
            lines.append(f"  🛑 SL:      {t['sl_note']}")
        if lvls["tp1"]:
            rr_str = f"  (RR: {lvls['rr1']:.1f}R)" if lvls["rr1"] else ""
            lines.append(f"  🎯 TP1:     <b>{lvls['tp1']:.5f}</b>{rr_str}")
        if lvls["tp2"]:
            rr_str = f"  (RR: {lvls['rr2']:.1f}R)" if lvls["rr2"] else ""
            lines.append(f"  🎯 TP2:     <b>{lvls['tp2']:.5f}</b>{rr_str}  <- equal highs/lows")
        if not lvls["tp1"] and not lvls["tp2"]:
            lines.append(f"  🎯 TP:      {t['tp_note']}")
        lines += [
            "",
            "  💡 Place limit order at Entry.",
            "  💡 Move SL to BE after TP1 hit.",
        ]
    else:
        lines.append("<b>⏸ NO TRADE — wait for all 4 pillars to align.</b>")

    lines += ["", "⚠️ Always confirm on chart. Manage your risk."]
    return "\n".join(lines)


def main():
    state = load_state()
    for symbol, label in SYMBOLS.items():
        try:
            dfs = {tf: fetch_bars(symbol, tf, outputsize=100) for tf in TIMEFRAMES}
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
