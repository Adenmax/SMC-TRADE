"""
SMC Logic v5 — final update from Callisto FX (Nick) live sessions July 30 2026
Changes from v4:
  1. Fixed kill zone times: NY AM 09:30, NY PM ends 16:00
  2. Full session liquidity tracking: NYPM/Lunch/NYAM/Asian/London H+L
  3. Nearest zone selection (closest to current price wins)
  4. Body candle close required for CHoCH (wick = invalid)
  5. CHoCH retest detection (second confirmation after body close)
  6. $$$ session liquidity markers displayed in alerts
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone


def _swing_points(high, low, n=3):
    size = len(high)
    ph = [False] * size
    pl = [False] * size
    for i in range(n, size - n):
        if high[i] == max(high[i - n: i + n + 1]):
            ph[i] = True
        if low[i] == min(low[i - n: i + n + 1]):
            pl[i] = True
    return ph, pl


def _atr(high, low, close, length=14):
    tr = [0.0] * len(high)
    for i in range(1, len(high)):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
    return pd.Series(tr).rolling(length, min_periods=1).mean().values


# ── PILLAR 1: BIAS ────────────────────────────────────────────────────────────
def get_bias(df, swing_n=5):
    if df is None or len(df) < swing_n * 2 + 2:
        return "ranging"
    h = df["high"].values
    l = df["low"].values
    ph, pl = _swing_points(h, l, swing_n)
    swing_highs = [h[i] for i in range(len(h)) if ph[i]]
    swing_lows  = [l[i] for i in range(len(l)) if pl[i]]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"
    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1]  > swing_lows[-2]
    ll = swing_lows[-1]  < swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    if hh and hl:
        return "bullish"
    if ll and lh:
        return "bearish"
    return "ranging"


def get_alignment_score(biases: dict):
    bull = sum(1 for v in biases.values() if v == "bullish")
    bear = sum(1 for v in biases.values() if v == "bearish")
    total = len(biases)
    if bull > bear:
        direction, score = "bullish", bull
    elif bear > bull:
        direction, score = "bearish", bear
    else:
        direction, score = "ranging", 0
    if score == total:
        label = "VERY STRONG TREND"
    elif score == total - 1:
        label = "STRONG TREND"
    elif score == total - 2:
        label = "MIXED — need clean POI"
    else:
        label = "NO CLEAR BIAS — sit out"
    return direction, score, label


# ── PILLAR 2: NARRATIVE ───────────────────────────────────────────────────────
# Kill zones in WAT (UTC+1) — your Nigeria timezone
# Asian:  1am  - 5am  WAT
# London: 7am  - 10am WAT
# NY AM:  2:30pm - 5pm WAT  (using 14 as floor)
# NY PM:  6pm  - 9pm  WAT
KILL_ZONES = {
    "Asian":    (1,  5),    # 1am  – 5am  WAT
    "London":   (7,  10),   # 7am  – 10am WAT
    "NY AM":    (14, 17),   # 2pm  – 5pm  WAT
    "NY PM":    (18, 21),   # 6pm  – 9pm  WAT
    # NY Lunch excluded
}


def get_kill_zone(dt: datetime):
    # Convert UTC to WAT (UTC+1)
    hour_wat = (dt.hour + 1) % 24
    for name, (start, end) in KILL_ZONES.items():
        if start <= hour_wat < end:
            return name
    return None


def get_all_session_levels(df_1h):
    """
    NEW: Track each session's high and low separately as $$$ magnets.
    Sessions: Asian, London, NY AM, NY Lunch, NY PM
    Returns dict of {session_name: {high, low}}
    """
    if df_1h is None or "datetime" not in df_1h.columns or len(df_1h) < 5:
        return {}

    df = df_1h.copy()
    df["hour_wat"] = (df["datetime"].dt.hour + 1) % 24  # WAT = UTC+1

    def session_mask(start, end):
        if start < end:
            return (df["hour_wat"] >= start) & (df["hour_wat"] < end)
        else:
            return (df["hour_wat"] >= start) | (df["hour_wat"] < end)

    sessions = {
        "Asian":    session_mask(1,  5),
        "London":   session_mask(7,  10),
        "NY AM":    session_mask(14, 17),
        "NY Lunch": session_mask(17, 18),
        "NY PM":    session_mask(18, 21),
    }

    result = {}
    for name, mask in sessions.items():
        subset = df[mask]
        if len(subset) >= 1:
            result[name] = {
                "high": float(subset["high"].max()),
                "low":  float(subset["low"].min()),
            }
    return result


def detect_consecutive_fvg(df, direction="bullish", lookback=6):
    if df is None or len(df) < 3:
        return False
    h = df["high"].values
    l = df["low"].values
    count = 0
    for i in range(max(2, len(df) - lookback), len(df)):
        if direction == "bullish" and l[i] > h[i - 2]:
            count += 1
        elif direction == "bearish" and h[i] < l[i - 2]:
            count += 1
    return count >= 2


def detect_fvg_momentum_fading(df, direction="bearish", lookback=8):
    if df is None or len(df) < 3:
        return False
    h = df["high"].values
    l = df["low"].values
    for i in range(max(2, len(df) - lookback), len(df)):
        if direction == "bullish" and l[i] > h[i - 2]:
            return False
        if direction == "bearish" and h[i] < l[i - 2]:
            return False
    return True


def detect_london_asian_sweep(df_asian, df_london):
    if df_asian is None or df_london is None:
        return None
    if len(df_asian) == 0 or len(df_london) == 0:
        return None
    asian_high   = df_asian["high"].max()
    asian_low    = df_asian["low"].min()
    london_high  = df_london["high"].max()
    london_low   = df_london["low"].min()
    london_close = df_london["close"].iloc[-1]
    if london_high > asian_high and london_close < asian_high:
        return "swept_high"
    if london_low < asian_low and london_close > asian_low:
        return "swept_low"
    return None


# ── PILLAR 3: POI ─────────────────────────────────────────────────────────────
def get_fvg_zones(df, direction="bullish", candle3_rule=True, max_age=50):
    if df is None or len(df) < 3:
        return []
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    fvgs = []
    for i in range(2, n):
        if (n - i) > max_age:
            continue
        if direction == "bullish" and l[i] > h[i - 2]:
            valid  = (not candle3_rule) or (c[i] <= h[i - 1])
            top    = l[i]
            bot    = h[i - 2]
            tapped = any(l[j] <= top and l[j] >= bot for j in range(i + 1, n))
            fvgs.append({"top": top, "bot": bot, "bar": i,
                         "valid": valid, "tapped": tapped, "direction": "bullish"})
        elif direction == "bearish" and h[i] < l[i - 2]:
            valid  = (not candle3_rule) or (c[i] >= l[i - 1])
            top    = l[i - 2]
            bot    = h[i]
            tapped = any(h[j] >= bot and h[j] <= top for j in range(i + 1, n))
            fvgs.append({"top": top, "bot": bot, "bar": i,
                         "valid": valid, "tapped": tapped, "direction": "bearish"})
    return fvgs


def detect_bpr(df, lookback=30):
    """BPR: two opposing overlapping FVGs = very strong reversal zone."""
    if df is None or len(df) < 5:
        return []
    bull_fvgs = get_fvg_zones(df, "bullish", candle3_rule=False, max_age=lookback)
    bear_fvgs = get_fvg_zones(df, "bearish", candle3_rule=False, max_age=lookback)
    bprs = []
    for bf in bull_fvgs:
        for brf in bear_fvgs:
            overlap_top = min(bf["top"], brf["top"])
            overlap_bot = max(bf["bot"], brf["bot"])
            if overlap_top > overlap_bot:
                direction = "bullish" if bf["bar"] > brf["bar"] else "bearish"
                bprs.append({"top": overlap_top, "bot": overlap_bot,
                             "direction": direction, "bar": max(bf["bar"], brf["bar"])})
    seen = []
    unique = []
    for b in sorted(bprs, key=lambda x: -x["bar"]):
        key = round(b["bot"], 2)
        if key not in seen:
            seen.append(key)
            unique.append(b)
    return unique[:3]


def get_order_blocks(df, swing_n=5, search_back=15):
    if df is None or len(df) < swing_n * 2 + 2:
        return None, None
    h  = df["high"].values
    l  = df["low"].values
    o  = df["open"].values
    c  = df["close"].values
    ph, pl = _swing_points(h, l, swing_n)
    last_sh = last_sl = None
    bull_ob = bear_ob = None
    choch_bull = choch_bear = None
    for j in range(len(df)):
        ci = j - swing_n
        if ci >= 0:
            if ph[ci]: last_sh = h[ci]
            if pl[ci]: last_sl = l[ci]
        bull_break = (last_sh is not None and j > 0
                      and c[j] > last_sh and c[j-1] <= last_sh)
        bear_break = (last_sl is not None and j > 0
                      and c[j] < last_sl and c[j-1] >= last_sl)
        if bull_break:
            choch_bull = last_sh
            for k in range(j-1, max(j-search_back, -1), -1):
                if c[k] < o[k]:
                    bull_ob = {"top": h[k], "bot": l[k], "bar": k,
                               "choch_level": choch_bull}
                    break
        if bear_break:
            choch_bear = last_sl
            for k in range(j-1, max(j-search_back, -1), -1):
                if c[k] > o[k]:
                    bear_ob = {"top": h[k], "bot": l[k], "bar": k,
                               "choch_level": choch_bear}
                    break
    return bull_ob, bear_ob


def get_equal_highs_lows(df, tolerance=0.001, lookback=30):
    if df is None or len(df) < 5:
        return []
    h = df["high"].values[-lookback:]
    l = df["low"].values[-lookback:]
    pools = []
    seen_h = set()
    for i in range(len(h)):
        if i in seen_h:
            continue
        cluster = [i]
        for j in range(i+1, len(h)):
            if abs(h[j] - h[i]) / h[i] < tolerance:
                cluster.append(j)
                seen_h.add(j)
        if len(cluster) >= 2:
            pools.append({"level": float(np.mean([h[k] for k in cluster])),
                          "type": "eq_high", "count": len(cluster)})
    seen_l = set()
    for i in range(len(l)):
        if i in seen_l:
            continue
        cluster = [i]
        for j in range(i+1, len(l)):
            if abs(l[j] - l[i]) / l[i] < tolerance:
                cluster.append(j)
                seen_l.add(j)
        if len(cluster) >= 2:
            pools.append({"level": float(np.mean([l[k] for k in cluster])),
                          "type": "eq_low", "count": len(cluster)})
    return pools


def get_nearest_session_zone(current_price, session_levels, direction):
    """
    NEW: Return the nearest session liquidity level to current price.
    For BUY setups → nearest session LOW below price (support).
    For SELL setups → nearest session HIGH above price (resistance).
    """
    if not session_levels:
        return None, None
    if direction == "BUY":
        candidates = []
        for name, lvl in session_levels.items():
            if lvl["low"] < current_price:
                candidates.append((name, lvl["low"], abs(current_price - lvl["low"])))
        if not candidates:
            return None, None
        candidates.sort(key=lambda x: x[2])
        return candidates[0][0], candidates[0][1]
    else:
        candidates = []
        for name, lvl in session_levels.items():
            if lvl["high"] > current_price:
                candidates.append((name, lvl["high"], abs(lvl["high"] - current_price)))
        if not candidates:
            return None, None
        candidates.sort(key=lambda x: x[2])
        return candidates[0][0], candidates[0][1]


def check_sl_near_magnet(sl_price, session_levels, tolerance_pct=0.001):
    """Warn if SL is near a session level (magnet = stop hunt risk)."""
    if sl_price is None or not session_levels:
        return False
    for name, lvl in session_levels.items():
        for level in [lvl["high"], lvl["low"]]:
            if abs(sl_price - level) / level < tolerance_pct:
                return True
    return False


# ── PILLAR 4: CONFIRMATION ────────────────────────────────────────────────────
def detect_sweep_choch(df, swing_n=3, min_atr_wick=0.05):
    """
    Sweep + CHoCH with BODY CANDLE CLOSE requirement.
    Mentor: 'Body candle close. Yes, a body candle close is exactly what I need.'
    """
    if df is None or len(df) < swing_n * 2 + 5:
        return None, None
    h   = df["high"].values
    l   = df["low"].values
    c   = df["close"].values
    o   = df["open"].values
    atr = _atr(h, l, c)
    ph, pl = _swing_points(h, l, swing_n)
    last_sh = last_sl = None
    result = None
    choch_level = None

    for j in range(len(df)):
        ci = j - swing_n
        if ci >= 0:
            if ph[ci]: last_sh = h[ci]
            if pl[ci]: last_sl = l[ci]
        if last_sh is None or last_sl is None:
            continue

        bear_sweep = (h[j] > last_sh and c[j] < last_sh
                      and (h[j] - last_sh) > min_atr_wick * atr[j])
        if bear_sweep:
            for k in range(j+1, min(j+8, len(df))):
                # BODY candle close below last_sl (not just wick)
                body_low = min(o[k], c[k])
                if body_low < last_sl:
                    result = "bearish_reversal"
                    choch_level = last_sl
                    break

        bull_sweep = (l[j] < last_sl and c[j] > last_sl
                      and (last_sl - l[j]) > min_atr_wick * atr[j])
        if bull_sweep:
            for k in range(j+1, min(j+8, len(df))):
                # BODY candle close above last_sh (not just wick)
                body_high = max(o[k], c[k])
                if body_high > last_sh:
                    result = "bullish_reversal"
                    choch_level = last_sh
                    break

    return result, choch_level


def detect_choch_retest(df, choch_level, direction, lookback=10):
    """
    NEW: After CHoCH body close, check if price has retested the CHoCH level.
    Mentor: 'Once I have the body candle close, the last confirmation is a retest.'
    """
    if df is None or choch_level is None or len(df) < 3:
        return False
    h = df["high"].values[-lookback:]
    l = df["low"].values[-lookback:]
    c = df["close"].values[-lookback:]
    tol = choch_level * 0.0005
    if direction == "bullish":
        # Price dips back near CHoCH level then closes above it
        for i in range(1, len(c)):
            if l[i] <= choch_level + tol and c[i] > choch_level:
                return True
    elif direction == "bearish":
        # Price rallies back near CHoCH level then closes below it
        for i in range(1, len(c)):
            if h[i] >= choch_level - tol and c[i] < choch_level:
                return True
    return False


def detect_internal_choch(df, swing_n=2):
    """Internal CHoCH on 5/3/1min — body close required."""
    if df is None or len(df) < 10:
        return None, None
    h  = df["high"].values[-30:]
    l  = df["low"].values[-30:]
    c  = df["close"].values[-30:]
    o  = df["open"].values[-30:]
    ph, pl = _swing_points(h, l, swing_n)
    swing_highs = [(i, h[i]) for i in range(len(h)) if ph[i]]
    swing_lows  = [(i, l[i]) for i in range(len(l)) if pl[i]]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None, None
    last_sh = swing_highs[-1][1]
    last_sl = swing_lows[-1][1]
    # Body close check
    body_high = max(o[-1], c[-1])
    body_low  = min(o[-1], c[-1])
    if body_high > last_sh and c[-2] <= last_sh:
        return "bullish_internal_choch", last_sl
    if body_low < last_sl and c[-2] >= last_sl:
        return "bearish_internal_choch", last_sh
    return None, None


def detect_engulfing(df):
    if df is None or len(df) < 3:
        return None
    idx = len(df) - 2
    o = df["open"].values
    c = df["close"].values
    if (c[idx] < o[idx] and c[idx-1] > o[idx-1]
            and o[idx] >= c[idx-1] and c[idx] <= o[idx-1]):
        return "bearish_engulfing"
    if (c[idx] > o[idx] and c[idx-1] < o[idx-1]
            and o[idx] <= c[idx-1] and c[idx] >= o[idx-1]):
        return "bullish_engulfing"
    return None


def score_trade(signal, score, biases, poi, conf, bprs,
                session_levels, counter_trend=False):
    """Mentor's 5th question: pros vs cons → will I take this trade?"""
    pros = []
    cons = []

    if score >= 3:
        pros.append(f"{score}/4 timeframes aligned")
    elif score == 2:
        pros.append("2/4 TFs aligned (acceptable with strong POI)")
        cons.append("Mixed bias — need extra confluence")
    else:
        cons.append("Timeframes not aligned — low probability")

    if counter_trend:
        pros.append("Major liquidity sweep (high prob reversal)")
        cons.append("Counter-trend to HTF bias — tighter SL needed")

    bpr = next((b for b in bprs if b["direction"] == (
        "bullish" if signal == "BUY" else "bearish")), None)
    if bpr:
        pros.append("BPR detected (two overlapping FVGs = very strong zone)")

    poi_count = 0
    if signal == "BUY":
        if poi.get("bull_fvg_15min_poi"):
            pros.append("15min iFVG BUY zone active (price inside)")
            poi_count += 1
        if poi.get("bull_ob_1h"):
            pros.append("1H bullish OB present")
            poi_count += 1
        if poi.get("bull_fvg_1h"):
            pros.append("1H bullish FVG present")
            poi_count += 1
        if poi.get("nearest_session_zone"):
            pros.append(f"Nearest session low = {poi['nearest_session_zone'][0]} ($$$ magnet)")
            poi_count += 1
    elif signal == "SELL":
        if poi.get("bear_fvg_15min_poi"):
            pros.append("15min iFVG SELL zone active (price inside)")
            poi_count += 1
        if poi.get("bear_ob_1h"):
            pros.append("1H bearish OB present")
            poi_count += 1
        if poi.get("bear_fvg_1h"):
            pros.append("1H bearish FVG present")
            poi_count += 1
        if poi.get("nearest_session_zone"):
            pros.append(f"Nearest session high = {poi['nearest_session_zone'][0]} ($$$ magnet)")
            poi_count += 1

    if poi_count < 2:
        cons.append("Only 1 POI confluence — need 2+ for strong zone")

    sc15, _ = conf.get("sweep_choch_15min", (None, None))
    sc1h, _ = conf.get("sweep_choch_1h", (None, None))
    ic, _   = conf.get("internal_choch", (None, None))
    eng     = conf.get("engulfing_15min")
    retest  = conf.get("choch_retest", False)

    if signal == "BUY":
        if sc15 == "bullish_reversal" or sc1h == "bullish_reversal":
            pros.append("Sweep+CHoCH body close confirmed (strongest)")
        elif ic == "bullish_internal_choch":
            pros.append("Internal CHoCH 5min confirmed")
        elif eng == "bullish_engulfing":
            pros.append("Bullish engulfing confirmed")
        if retest:
            pros.append("CHoCH retest complete — highest confidence entry")
    elif signal == "SELL":
        if sc15 == "bearish_reversal" or sc1h == "bearish_reversal":
            pros.append("Sweep+CHoCH body close confirmed (strongest)")
        elif ic == "bearish_internal_choch":
            pros.append("Internal CHoCH 5min confirmed")
        elif eng == "bearish_engulfing":
            pros.append("Bearish engulfing confirmed")
        if retest:
            pros.append("CHoCH retest complete — highest confidence entry")

    if conf.get("sl_near_magnet"):
        cons.append("SL near session level (magnet) — adjust SL or skip")

    if biases.get("daily") == "ranging" or biases.get("4h") == "ranging":
        cons.append("HTF ranging — watch for fake CHoCH")

    total_pros = len(pros)
    total_cons = len(cons)

    if total_pros >= 4 and total_cons == 0:
        rec = "TAKE THE TRADE ✅"
    elif total_pros >= 3 and total_cons <= 1:
        rec = "STRONG SETUP — confirm on chart then enter"
    elif total_pros >= 2 and total_cons <= 1:
        rec = "LEAN YES — your discretion"
    elif total_cons >= 2:
        rec = "SKIP — too many cons"
    else:
        rec = "BORDERLINE — wait for more confluence"

    return pros, cons, rec


# ── MASTER ANALYSIS ───────────────────────────────────────────────────────────
def run_full_analysis(df_daily, df_4h, df_1h, df_15min, df_5min=None,
                      df_asian=None, df_london=None):
    result = {"bias": {}, "alignment": {}, "narrative": {},
              "poi": {}, "confirmation": {}, "trade_idea": {}, "decision": {}}

    # Pillar 1 — Bias
    biases = {}
    for label, df in [("daily", df_daily), ("4h", df_4h),
                      ("1h", df_1h), ("15min", df_15min)]:
        biases[label] = get_bias(df)
    result["bias"] = biases
    direction, score, align_label = get_alignment_score(biases)
    result["alignment"] = {"direction": direction, "score": score, "label": align_label}

    # Pillar 2 — Narrative
    kz = None
    if df_15min is not None and "datetime" in df_15min.columns and len(df_15min) >= 2:
        last_dt = df_15min["datetime"].iloc[-2]
        if hasattr(last_dt, "to_pydatetime"):
            last_dt = last_dt.to_pydatetime()
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        kz = get_kill_zone(last_dt)

    london_asian = detect_london_asian_sweep(df_asian, df_london)

    fvg_momentum = None
    if df_1h is not None and len(df_1h) >= 10:
        if detect_consecutive_fvg(df_1h, direction):
            fvg_momentum = f"Consecutive {direction} FVGs — momentum strong"
        elif detect_fvg_momentum_fading(df_1h, direction):
            fvg_momentum = f"{direction.title()} FVG momentum fading — possible reversal"

    amd_context = biases.get("daily") == "ranging" or biases.get("4h") == "ranging"

    # All session levels ($$$ markers)
    session_levels = get_all_session_levels(df_1h)

    result["narrative"] = {
        "kill_zone": kz,
        "london_sweep": london_asian,
        "fvg_momentum": fvg_momentum,
        "amd_context": amd_context,
        "session_levels": session_levels,
    }

    # Pillar 3 — POI
    poi = {}
    current_price = float(df_15min["close"].iloc[-2]) if df_15min is not None and len(df_15min) >= 2 else 0

    if df_1h is not None and len(df_1h) >= 20:
        bull_ob, bear_ob = get_order_blocks(df_1h)
        poi["bull_ob_1h"] = bull_ob
        poi["bear_ob_1h"] = bear_ob
    if df_1h is not None and len(df_1h) >= 10:
        bf  = get_fvg_zones(df_1h, "bullish", candle3_rule=True)
        brf = get_fvg_zones(df_1h, "bearish", candle3_rule=True)
        poi["bull_fvg_1h"] = next((f for f in reversed(bf) if f["valid"]), None)
        poi["bear_fvg_1h"] = next((f for f in reversed(brf) if f["valid"]), None)

    poi["bpr_1h"]    = detect_bpr(df_1h,    lookback=40) if df_1h    is not None else []
    poi["bpr_15min"] = detect_bpr(df_15min, lookback=30) if df_15min is not None else []

    if df_15min is not None and len(df_15min) >= 10:
        bf15  = get_fvg_zones(df_15min, "bullish", candle3_rule=True, max_age=30)
        brf15 = get_fvg_zones(df_15min, "bearish", candle3_rule=True, max_age=30)
        poi["bull_fvg_15min_poi"] = next(
            (f for f in reversed(bf15) if f["valid"] and f["tapped"]), None)
        poi["bear_fvg_15min_poi"] = next(
            (f for f in reversed(brf15) if f["valid"] and f["tapped"]), None)
        poi["bull_fvg_15min_untapped"] = next(
            (f for f in reversed(bf15) if f["valid"] and not f["tapped"]), None)
        poi["bear_fvg_15min_untapped"] = next(
            (f for f in reversed(brf15) if f["valid"] and not f["tapped"]), None)

    poi["equal_levels_4h"]    = get_equal_highs_lows(df_4h)    if df_4h    is not None else []
    poi["equal_levels_15min"] = get_equal_highs_lows(df_15min, tolerance=0.0005, lookback=20) if df_15min is not None else []

    if df_1h is not None and len(df_1h) >= 2:
        ph, pl = None, None
        df2 = df_1h.copy()
        df2["date"] = df2["datetime"].dt.date
        grouped = df2.groupby("date")
        dates = sorted(grouped.groups.keys())
        if len(dates) >= 2:
            prev = grouped.get_group(dates[-2])
            ph = float(prev["high"].max())
            pl = float(prev["low"].min())
        poi["prev_session_high"] = ph
        poi["prev_session_low"]  = pl

    result["poi"] = poi

    # Pillar 4 — Confirmation (CHoCH > iFVG retest > Engulfing)
    conf = {}
    sc15, cl15 = detect_sweep_choch(df_15min) if df_15min is not None else (None, None)
    sc1h, cl1h = detect_sweep_choch(df_1h)    if df_1h    is not None else (None, None)
    conf["sweep_choch_15min"] = (sc15, cl15)
    conf["sweep_choch_1h"]    = (sc1h, cl1h)

    if df_5min is not None and len(df_5min) >= 10:
        ic, icl = detect_internal_choch(df_5min)
    elif df_15min is not None and len(df_15min) >= 10:
        ic, icl = detect_internal_choch(df_15min)
    else:
        ic, icl = None, None
    conf["internal_choch"] = (ic, icl)

    # CHoCH retest check
    choch_level_used = cl15 or cl1h or icl
    choch_dir = None
    if sc15 in ("bullish_reversal",) or sc1h in ("bullish_reversal",) or ic == "bullish_internal_choch":
        choch_dir = "bullish"
    elif sc15 in ("bearish_reversal",) or sc1h in ("bearish_reversal",) or ic == "bearish_internal_choch":
        choch_dir = "bearish"

    conf["choch_retest"] = detect_choch_retest(df_15min, choch_level_used, choch_dir)
    conf["engulfing_15min"] = detect_engulfing(df_15min)

    if df_15min is not None and len(df_15min) >= 5:
        bf15c  = get_fvg_zones(df_15min, "bullish", candle3_rule=True)
        brf15c = get_fvg_zones(df_15min, "bearish", candle3_rule=True)
        conf["bull_fvg_15min"] = next(
            (f for f in reversed(bf15c) if f["valid"] and not f["tapped"]), None)
        conf["bear_fvg_15min"] = next(
            (f for f in reversed(brf15c) if f["valid"] and not f["tapped"]), None)

    result["confirmation"] = conf

    # Trade idea
    sc15_r = sc15
    sc1h_r = sc1h
    ic_r   = ic
    eng    = conf.get("engulfing_15min")

    bull_conf = (sc15_r == "bullish_reversal" or sc1h_r == "bullish_reversal"
                 or eng == "bullish_engulfing" or ic_r == "bullish_internal_choch")
    bear_conf = (sc15_r == "bearish_reversal" or sc1h_r == "bearish_reversal"
                 or eng == "bearish_engulfing" or ic_r == "bearish_internal_choch")

    has_bull_poi = (poi.get("bull_fvg_15min_poi") is not None
                    or poi.get("bull_ob_1h") is not None
                    or poi.get("bull_fvg_1h") is not None)
    has_bear_poi = (poi.get("bear_fvg_15min_poi") is not None
                    or poi.get("bear_ob_1h") is not None
                    or poi.get("bear_fvg_1h") is not None)

    counter_trend_bull = (london_asian == "swept_low" and bull_conf and has_bull_poi
                          and direction in ["bearish", "ranging"])
    counter_trend_bear = (london_asian == "swept_high" and bear_conf and has_bear_poi
                          and direction in ["bullish", "ranging"])

    min_score = 2 if (has_bull_poi or has_bear_poi) else 3
    all_bprs = poi.get("bpr_1h", []) + poi.get("bpr_15min", [])

    trade = {"signal": None, "quality": None, "sl_price": None,
             "sl_note": None, "tp_note": None, "counter_trend": False}

    if (score >= min_score and direction == "bullish" and bull_conf and has_bull_poi) \
            or counter_trend_bull:
        sl_price = cl15 or cl1h or icl
        ob = poi.get("bull_ob_1h")
        if sl_price is None and ob:
            sl_price = ob["bot"] * 0.9997
        bpr_match = any(b["direction"] == "bullish" for b in all_bprs)
        fvg = poi.get("bull_fvg_15min_poi") or poi.get("bull_fvg_1h")
        both = fvg is not None and ob is not None
        quality = "HIGH ✅" if (both or bpr_match) else "MEDIUM ⚠️"
        if counter_trend_bull:
            quality = "COUNTER-TREND HIGH ✅" if bpr_match else "COUNTER-TREND ⚠️"

        # Nearest session low = TP target
        ns_name, ns_level = get_nearest_session_zone(current_price, session_levels, "BUY")
        poi["nearest_session_zone"] = (ns_name, ns_level) if ns_name else None

        conf["sl_near_magnet"] = check_sl_near_magnet(sl_price, session_levels)
        trade.update({"signal": "BUY", "quality": quality, "sl_price": sl_price,
                      "sl_note": "Below CHoCH structural low / 1H OB low",
                      "tp_note": f"Nearest session high / equal highs (BSL)",
                      "counter_trend": counter_trend_bull})

    elif (score >= min_score and direction == "bearish" and bear_conf and has_bear_poi) \
            or counter_trend_bear:
        sl_price = cl15 or cl1h or icl
        ob = poi.get("bear_ob_1h")
        if sl_price is None and ob:
            sl_price = ob["top"] * 1.0003
        bpr_match = any(b["direction"] == "bearish" for b in all_bprs)
        fvg = poi.get("bear_fvg_15min_poi") or poi.get("bear_fvg_1h")
        both = fvg is not None and ob is not None
        quality = "HIGH ✅" if (both or bpr_match) else "MEDIUM ⚠️"
        if counter_trend_bear:
            quality = "COUNTER-TREND HIGH ✅" if bpr_match else "COUNTER-TREND ⚠️"

        ns_name, ns_level = get_nearest_session_zone(current_price, session_levels, "SELL")
        poi["nearest_session_zone"] = (ns_name, ns_level) if ns_name else None

        conf["sl_near_magnet"] = check_sl_near_magnet(sl_price, session_levels)
        trade.update({"signal": "SELL", "quality": quality, "sl_price": sl_price,
                      "sl_note": "Above CHoCH structural high / 1H OB high",
                      "tp_note": "Nearest session low / equal lows (SSL)",
                      "counter_trend": counter_trend_bear})

    result["trade_idea"] = trade

    # 5th question: will I take this trade?
    if trade["signal"]:
        pros, cons, rec = score_trade(
            trade["signal"], score, biases, poi, conf, all_bprs,
            session_levels, counter_trend=trade["counter_trend"])
        result["decision"] = {"pros": pros, "cons": cons, "recommendation": rec}
    else:
        result["decision"] = {"pros": [], "cons": [], "recommendation": "NO TRADE"}

    return result
