"""
SMC Logic v2 — built 100% from the Kalisto FX framework:
  Bias → Narrative → POI → Confirmation

Pillars implemented:
  1. BIAS      — top-down structural bias (Daily/4H/1H/15min)
  2. NARRATIVE — kill zone, London/Asian sweep, FVG momentum
  3. POI       — order blocks, FVGs (candle-3 rule), session liquidity, equal H/L
  4. CONFIRM   — sweep + CHoCH (highest probability), engulfing, LTF FVG
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone


def _swing_points(high, low, n=5):
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
    h = df["high"].values
    l = df["low"].values
    ph, pl = _swing_points(h, l, swing_n)
    swing_highs = [(i, h[i]) for i in range(len(h)) if ph[i]]
    swing_lows  = [(i, l[i]) for i in range(len(l)) if pl[i]]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"
    hh = swing_highs[-1][1] > swing_highs[-2][1]
    hl = swing_lows[-1][1]  > swing_lows[-2][1]
    ll = swing_lows[-1][1]  < swing_lows[-2][1]
    lh = swing_highs[-1][1] < swing_highs[-2][1]
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
        label = "RANGING — wait for clarity"
    else:
        label = "NO CLEAR BIAS — sit out"
    return direction, score, label


# ── PILLAR 2: NARRATIVE ───────────────────────────────────────────────────────
KILL_ZONES = {
    "Asian":  (20, 24),
    "London": (2,  5),
    "NY AM":  (8,  12),
    "NY PM":  (13, 17),
}


def get_kill_zone(dt: datetime):
    hour_et = (dt.hour - 4) % 24
    for name, (start, end) in KILL_ZONES.items():
        if start <= hour_et < end:
            return name
    return None


def detect_consecutive_fvg(df, direction="bullish", lookback=6):
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
    asian_high = df_asian["high"].max()
    asian_low  = df_asian["low"].min()
    london_high = df_london["high"].max()
    london_low  = df_london["low"].min()
    london_close = df_london["close"].iloc[-1]
    if london_high > asian_high and london_close < asian_high:
        return "swept_high"
    if london_low < asian_low and london_close > asian_low:
        return "swept_low"
    return None


# ── PILLAR 3: POI ─────────────────────────────────────────────────────────────
def get_fvg(df, direction="bullish", candle3_rule=True):
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    fvgs = []
    for i in range(2, len(df)):
        if direction == "bullish" and l[i] > h[i - 2]:
            valid = (not candle3_rule) or (c[i] <= h[i - 1])
            fvgs.append({"top": l[i], "bot": h[i - 2], "bar": i,
                         "valid": valid, "direction": "bullish"})
        elif direction == "bearish" and h[i] < l[i - 2]:
            valid = (not candle3_rule) or (c[i] >= l[i - 1])
            fvgs.append({"top": l[i - 2], "bot": h[i], "bar": i,
                         "valid": valid, "direction": "bearish"})
    return fvgs


def get_order_blocks(df, swing_n=5, search_back=15):
    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    c = df["close"].values
    ph, pl = _swing_points(h, l, swing_n)
    last_sh = last_sl = None
    bull_ob = bear_ob = None
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
            for k in range(j-1, max(j-search_back, -1), -1):
                if c[k] < o[k]:
                    bull_ob = {"top": h[k], "bot": l[k], "bar": k}
                    break
        if bear_break:
            for k in range(j-1, max(j-search_back, -1), -1):
                if c[k] > o[k]:
                    bear_ob = {"top": h[k], "bot": l[k], "bar": k}
                    break
    return bull_ob, bear_ob


def get_equal_highs_lows(df, tolerance=0.001, lookback=30):
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


def get_session_levels(df):
    if "datetime" not in df.columns:
        return None, None
    df2 = df.copy()
    df2["date"] = df2["datetime"].dt.date
    grouped = df2.groupby("date")
    dates = sorted(grouped.groups.keys())
    if len(dates) < 2:
        return None, None
    prev = grouped.get_group(dates[-2])
    return float(prev["high"].max()), float(prev["low"].min())


# ── PILLAR 4: CONFIRMATION ────────────────────────────────────────────────────
def detect_sweep_choch(df, swing_n=5, min_atr_wick=0.1):
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    atr = _atr(h, l, c)
    ph, pl = _swing_points(h, l, swing_n)
    last_sh = last_sl = None
    result = None
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
            for k in range(j+1, min(j+6, len(df))):
                if c[k-1] > last_sl and c[k] < last_sl:
                    result = "bearish_reversal"
                    break
        bull_sweep = (l[j] < last_sl and c[j] > last_sl
                      and (last_sl - l[j]) > min_atr_wick * atr[j])
        if bull_sweep:
            for k in range(j+1, min(j+6, len(df))):
                if c[k-1] < last_sh and c[k] > last_sh:
                    result = "bullish_reversal"
                    break
    return result


def detect_engulfing(df):
    if len(df) < 3:
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


# ── MASTER ANALYSIS ───────────────────────────────────────────────────────────
def run_full_analysis(df_daily, df_4h, df_1h, df_15min,
                      df_asian=None, df_london=None):
    result = {"bias": {}, "alignment": {}, "narrative": {},
              "poi": {}, "confirmation": {}, "trade_idea": {}}

    # Pillar 1
    biases = {}
    for label, df in [("daily", df_daily), ("4h", df_4h),
                      ("1h", df_1h), ("15min", df_15min)]:
        biases[label] = get_bias(df) if (df is not None and len(df) >= 20) else "ranging"
    result["bias"] = biases
    direction, score, align_label = get_alignment_score(biases)
    result["alignment"] = {"direction": direction, "score": score, "label": align_label}

    # Pillar 2
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

    result["narrative"] = {"kill_zone": kz, "london_sweep": london_asian,
                           "fvg_momentum": fvg_momentum}

    # Pillar 3
    poi = {}
    if df_1h is not None and len(df_1h) >= 20:
        bull_ob, bear_ob = get_order_blocks(df_1h)
        poi["bull_ob_1h"] = bull_ob
        poi["bear_ob_1h"] = bear_ob
    if df_1h is not None and len(df_1h) >= 10:
        bf = get_fvg(df_1h, "bullish", candle3_rule=True)
        brf = get_fvg(df_1h, "bearish", candle3_rule=True)
        poi["bull_fvg_1h"] = next((f for f in reversed(bf) if f["valid"]), None)
        poi["bear_fvg_1h"] = next((f for f in reversed(brf) if f["valid"]), None)
    if df_4h is not None and len(df_4h) >= 15:
        poi["equal_levels_4h"] = get_equal_highs_lows(df_4h)
    else:
        poi["equal_levels_4h"] = []
    if df_1h is not None and len(df_1h) >= 2:
        ph, pl = get_session_levels(df_1h)
        poi["prev_session_high"] = ph
        poi["prev_session_low"]  = pl
    result["poi"] = poi

    # Pillar 4
    conf = {}
    if df_15min is not None and len(df_15min) >= 15:
        conf["sweep_choch_15min"] = detect_sweep_choch(df_15min)
    if df_1h is not None and len(df_1h) >= 15:
        conf["sweep_choch_1h"] = detect_sweep_choch(df_1h)
    if df_15min is not None and len(df_15min) >= 3:
        conf["engulfing_15min"] = detect_engulfing(df_15min)
    if df_15min is not None and len(df_15min) >= 5:
        bf15 = get_fvg(df_15min, "bullish", candle3_rule=True)
        brf15 = get_fvg(df_15min, "bearish", candle3_rule=True)
        conf["bull_fvg_15min"] = next((f for f in reversed(bf15) if f["valid"]), None)
        conf["bear_fvg_15min"] = next((f for f in reversed(brf15) if f["valid"]), None)
    result["confirmation"] = conf

    # Trade idea
    trade = {"signal": None, "quality": None, "sl_note": None, "tp_note": None}
    if score >= 3 and direction != "ranging":
        sc15 = conf.get("sweep_choch_15min")
        sc1h = conf.get("sweep_choch_1h")
        eng  = conf.get("engulfing_15min")
        bull_conf = (sc15 == "bullish_reversal" or sc1h == "bullish_reversal"
                     or eng == "bullish_engulfing"
                     or conf.get("bull_fvg_15min") is not None)
        bear_conf = (sc15 == "bearish_reversal" or sc1h == "bearish_reversal"
                     or eng == "bearish_engulfing"
                     or conf.get("bear_fvg_15min") is not None)
        if direction == "bullish" and bull_conf:
            ob  = poi.get("bull_ob_1h")
            fvg = poi.get("bull_fvg_1h")
            trade.update({"signal": "BUY",
                          "quality": "HIGH ✅" if (ob and fvg) else "MEDIUM ⚠️",
                          "sl_note": "Below 1H bullish OB / FVG low",
                          "tp_note": "Prev session high / equal highs (BSL)"})
        elif direction == "bearish" and bear_conf:
            ob  = poi.get("bear_ob_1h")
            fvg = poi.get("bear_fvg_1h")
            trade.update({"signal": "SELL",
                          "quality": "HIGH ✅" if (ob and fvg) else "MEDIUM ⚠️",
                          "sl_note": "Above 1H bearish OB / FVG high",
                          "tp_note": "Prev session low / equal lows (SSL)"})
    result["trade_idea"] = trade
    return result
