"""
run_alerts.py v2 — Kalisto FX 4-pillar framework
Fetches 4 timeframes, runs full analysis, sends structured Telegram alert.
API budget: daily + 4h + 1h + 15min = 4 calls x 2 symbols = 8 calls/run
At 15-min schedule = ~768 calls/day (under Twelve Data free 800 limit).
"""

import os, json, requests, pandas as pd
from smc_logic import run_full_analysis

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE          = "state.json"

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


def build_message(symbol, label, price, bar_time, r):
    a  = r["alignment"]
    b  = r["bias"]
    n  = r["narrative"]
    p  = r["poi"]
    c  = r["confirmation"]
    t  = r["trade_idea"]

    kz   = n.get("kill_zone") or "Outside kill zone"
    ls   = n.get("london_sweep")
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

    lines += ["", f"<b>【 PILLAR 3 — POINTS OF INTEREST 】</b>"]

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

    eq = p.get("equal_levels_4h", [])
    for lvl in eq[:2]:
        tag = "Equal Highs (BSL)" if lvl["type"] == "eq_high" else "Equal Lows (SSL)"
        lines.append(f"  ⚡ {tag}: {lvl['level']:.5f}  ({lvl['count']}x touched)")

    lines += ["", f"<b>【 PILLAR 4 — CONFIRMATION 】</b>"]

    sc15 = c.get("sweep_choch_15min")
    sc1h = c.get("sweep_choch_1h")
    eng  = c.get("engulfing_15min")
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
        emoji = "🟢 BUY" if t["signal"] == "BUY" else "🔴 SELL"
        lines += [
            f"<b>🎯 TRADE IDEA: {emoji}</b>",
            f"  Quality:  <b>{t['quality']}</b>",
            f"  SL:       {t['sl_note']}",
            f"  TP:       {t['tp_note']}",
        ]
    else:
        lines.append("<b>⏸ NO TRADE — conditions not met yet. Wait for all 4 pillars.</b>")

    lines += ["", "⚠️ Always confirm on chart. Never trade without all 4 pillars."]
    return "\n".join(lines)


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
            print(f"Not enough 15min bars for {symbol}")
            continue

        bar_time  = df_15min["datetime"].iloc[-2].isoformat()
        last_seen = state.get(symbol, {}).get("last_bar")
        if last_seen == bar_time:
            print(f"No new bar for {symbol}")
            continue

        r = run_full_analysis(
            df_daily  = dfs.get("1day"),
            df_4h     = dfs.get("4h"),
            df_1h     = dfs.get("1h"),
            df_15min  = df_15min,
        )

        # Only send if there's a trade idea OR a sweep+CHoCH confirmation
        conf = r["confirmation"]
        has_signal = (
            r["trade_idea"]["signal"] is not None
            or conf.get("sweep_choch_15min") is not None
            or conf.get("sweep_choch_1h") is not None
            or conf.get("engulfing_15min") is not None
        )

        if has_signal:
            price = df_15min["close"].iloc[-2]
            msg = build_message(symbol, label, price, bar_time, r)
            send_telegram(msg)
            print(f"Alert sent for {symbol} at {bar_time}")
        else:
            print(f"No signal for {symbol} at {bar_time}")

        state.setdefault(symbol, {})["last_bar"] = bar_time

    save_state(state)


if __name__ == "__main__":
    main()
