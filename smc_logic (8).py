"""
SMC Logic - Happiness Hanson (HCNFX Academy) Strategy
Entry TF: 15min | Confirmation: 5min CHoCH

5 Confluences (ALL must be present):
1. Structural Bias (Daily -> 4H -> 1H -> 15min majority)
2. BOS on 15min
3. Order Block (origin of the BOS)
4. Liquidity cleared before OB tap
5. Imbalance (FVG inside/near the OB)
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


# ── 1. STRUCTURAL BIAS ────────────────────────────────────────────────────────
def get_bias(df, swing_n=3):
    """
    Bullish = HH + HL. Bearish = LH + LL.
    No trade if ranging.
    Uses most recent swing points only.
    """
    if df is None or len(df) < 20:
        return "ranging"
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values

    # Swing structure
    ph, pl = _swing_points(h, l, swing_n)
    highs = [h[i] for i in range(len(h)) if ph[i]]
    lows  = [l[i] for i in range(len(l)) if pl[i]]

    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]
        hl = lows[-1]  > lows[-2]
        ll = lows[-1]  < lows[-2]
        lh = highs[-1] < highs[-2]
        if hh and hl: return "bullish"
        if ll and lh: return "bearish"

    # Fallback: price vs 50-bar midpoint
    lookback = min(50, len(c))
    mid = (float(max(h[-lookback:])) + float(min(l[-lookback:]))) / 2
    current = float(c[-1])
    recent_high = float(max(h[-10:]))
    recent_low  = float(min(l[-10:]))
    if current > mid * 1.005 and recent_low > mid:
        return "bullish"
    if current < mid * 0.995 and recent_high < mid:
        return "bearish"
    return "ranging"


def get_overall_bias(df_daily, df_4h, df_1h, df_15min):
    """
    Top-down: Daily -> 4H -> 1H -> 15min.
    Need 3/4 or 4/4 agreement. Else ranging = no trade.
    """
    biases = {
        "daily": get_bias(df_daily),
        "4h":    get_bias(df_4h),
        "1h":    get_bias(df_1h),
        "15min": get_bias(df_15min),
    }
    bull = sum(1 for v in biases.values() if v == "bullish")
    bear = sum(1 for v in biases.values() if v == "bearish")

    if bull >= 3:   direction = "bullish"
    elif bear >= 3: direction = "bearish"
    else:           direction = "ranging"

    return biases, direction, bull, bear


# ── KILL ZONE (WAT UTC+1) ─────────────────────────────────────────────────────
KILL_ZONES = {
    "Asian":  (1,  5),
    "London": (7,  10),
    "NY AM":  (14, 17),
    "NY PM":  (18, 21),
}

def get_kill_zone(dt: datetime):
    hour_wat = (dt.hour + 1) % 24
    for name, (start, end) in KILL_ZONES.items():
        if start <= hour_wat < end:
            return name
    return None


# ── 2. BOS ON 15MIN ──────────────────────────────────────────────────────────
def detect_bos_15min(df, direction="bullish", swing_n=3):
    """
    Break of Structure on 15min.
    Bullish BOS: price closes above a previous swing high.
    Bearish BOS: price closes below a previous swing low.
    Returns (bos_found, bos_level, bos_bar_index)
    """
    if df is None or len(df) < 20:
        return False, None, None

    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    ph, pl = _swing_points(h, l, swing_n)

    last_bos_level = None
    last_bos_bar   = None

    for i in range(swing_n, len(df)):
        if direction == "bullish":
            # Find swing highs
            if ph[i - swing_n]:
                sh = h[i - swing_n]
                # Check if later close breaks above it
                for j in range(i, len(df)):
                    if c[j] > sh:
                        last_bos_level = sh
                        last_bos_bar   = j
                        break
        else:
            if pl[i - swing_n]:
                sl = l[i - swing_n]
                for j in range(i, len(df)):
                    if c[j] < sl:
                        last_bos_level = sl
                        last_bos_bar   = j
                        break

    if last_bos_level is not None:
        return True, last_bos_level, last_bos_bar
    return False, None, None


# ── 3. ORDER BLOCK (origin of BOS) ───────────────────────────────────────────
def get_order_block(df, direction="bullish", bos_bar=None,
                    swing_n=3, search_back=20):
    """
    The order block is the last opposite-coloured candle BEFORE the BOS.
    This is the ORIGIN of the impulsive move that caused the BOS.
    Bullish OB: last bearish candle before the bullish BOS impulse.
    Bearish OB: last bullish candle before the bearish BOS impulse.
    """
    if df is None or len(df) < 5:
        return None

    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    c = df["close"].values

    if bos_bar is None:
        bos_bar = len(df) - 1

    # Search backwards from BOS bar
    start = max(0, bos_bar - search_back)
    ob = None

    if direction == "bullish":
        # Last bearish candle before BOS
        for k in range(bos_bar - 1, start - 1, -1):
            if c[k] < o[k]:  # bearish candle
                ob = {"top": h[k], "bot": l[k], "bar": k,
                      "direction": "bullish"}
                break
    else:
        # Last bullish candle before BOS
        for k in range(bos_bar - 1, start - 1, -1):
            if c[k] > o[k]:  # bullish candle
                ob = {"top": h[k], "bot": l[k], "bar": k,
                      "direction": "bearish"}
                break

    return ob


# ── 4. LIQUIDITY CLEARED ─────────────────────────────────────────────────────
def check_liquidity_cleared(df, direction="bullish", ob=None, lookback=30):
    """
    Liquidity must be cleared BEFORE price taps the OB.
    Bullish: equal lows or previous swing low swept before OB tap.
    Bearish: equal highs or previous swing high swept before OB tap.
    Returns (cleared, liquidity_level)
    """
    if df is None or ob is None or len(df) < 10:
        return False, None

    h = df["high"].values[-lookback:]
    l = df["low"].values[-lookback:]
    c = df["close"].values[-lookback:]
    n = len(h)

    if direction == "bullish":
        # Look for a swing low that was swept (wick below, close above)
        for i in range(2, n - 1):
            prev_low = min(l[max(0, i-3):i])
            # Wick went below but close is back above = sweep
            if l[i] < prev_low and c[i] > prev_low:
                return True, float(prev_low)
        # Also check equal lows (price touched same level twice)
        for i in range(2, n):
            for j in range(i - 1, max(0, i - 5), -1):
                if abs(l[i] - l[j]) / l[j] < 0.001:
                    return True, float(l[i])
    else:
        # Look for swing high swept
        for i in range(2, n - 1):
            prev_high = max(h[max(0, i-3):i])
            if h[i] > prev_high and c[i] < prev_high:
                return True, float(prev_high)
        # Equal highs
        for i in range(2, n):
            for j in range(i - 1, max(0, i - 5), -1):
                if abs(h[i] - h[j]) / h[j] < 0.001:
                    return True, float(h[i])

    return False, None


# ── 5. IMBALANCE (FVG near/inside OB) ────────────────────────────────────────
def check_imbalance(df, direction="bullish", ob=None, search_range=10):
    """
    The OB must have created an imbalance (FVG).
    We look for an FVG within search_range bars of the OB.
    Returns (imbalance_found, fvg_zone)
    """
    if df is None or ob is None or len(df) < 5:
        return False, None

    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    ob_bar = ob.get("bar", 0)

    search_start = max(0, ob_bar - 2)
    search_end   = min(n, ob_bar + search_range)

    for i in range(search_start + 2, search_end):
        if direction == "bullish" and l[i] > h[i - 2]:
            return True, {"top": l[i], "bot": h[i - 2], "bar": i}
        if direction == "bearish" and h[i] < l[i - 2]:
            return True, {"top": l[i - 2], "bot": h[i], "bar": i}

    return False, None


# ── PRICE IN OB CHECK ─────────────────────────────────────────────────────────
def price_in_ob(price, ob):
    if ob is None:
        return False
    return ob["bot"] <= price <= ob["top"]


# ── CONFIRMATION: 5min CHoCH ──────────────────────────────────────────────────
def confirm_5min_choch(df_5min, direction="bullish", swing_n=2):
    """
    5min Change of Character = entry trigger.
    Bullish CHoCH: body close above most recent lower high on 5min.
    Bearish CHoCH: body close below most recent higher low on 5min.
    Returns (confirmed, choch_level)
    """
    if df_5min is None or len(df_5min) < 10:
        return False, None

    h = df_5min["high"].values[-30:]
    l = df_5min["low"].values[-30:]
    o = df_5min["open"].values[-30:]
    c = df_5min["close"].values[-30:]
    ph, pl = _swing_points(h, l, swing_n)

    if direction == "bullish":
        highs = [h[i] for i in range(len(h)) if ph[i]]
        if not highs:
            return False, None
        last_lower_high = highs[-1]
        body_high = max(o[-1], c[-1])
        if body_high > last_lower_high and c[-2] <= last_lower_high:
            return True, last_lower_high
    else:
        lows = [l[i] for i in range(len(l)) if pl[i]]
        if not lows:
            return False, None
        last_higher_low = lows[-1]
        body_low = min(o[-1], c[-1])
        if body_low < last_higher_low and c[-2] >= last_higher_low:
            return True, last_higher_low

    return False, None


# ── SESSION LEVELS (TP targets) ───────────────────────────────────────────────
def get_session_levels(df_1h):
    if df_1h is None or "datetime" not in df_1h.columns:
        return {}
    df = df_1h.copy()
    df["hour_wat"] = (df["datetime"].dt.hour + 1) % 24
    sessions = {
        "Asian":    (1,  5),
        "London":   (7,  10),
        "NY AM":    (14, 17),
        "NY PM":    (18, 21),
    }
    result = {}
    for name, (start, end) in sessions.items():
        mask = (df["hour_wat"] >= start) & (df["hour_wat"] < end)
        sub  = df[mask]
        if len(sub) >= 1:
            result[name] = {
                "high": float(sub["high"].max()),
                "low":  float(sub["low"].min()),
            }
    return result


def get_prev_day_levels(df_daily):
    if df_daily is None or len(df_daily) < 2:
        return None, None
    prev = df_daily.iloc[-2]
    return float(prev["high"]), float(prev["low"])


def get_tp(direction, entry, session_levels, df_4h, prev_high, prev_low):
    """
    TP = next key zone / liquidity in direction of trade.
    Uses session levels and previous day high/low.
    """
    min_dist = 0.003
    tp = None

    if direction == "bullish":
        candidates = []
        for n, v in session_levels.items():
            if v["high"] > entry * (1 + min_dist):
                candidates.append(v["high"])
        if prev_high and prev_high > entry * (1 + min_dist):
            candidates.append(prev_high)
        if candidates:
            tp = max(candidates)
        elif df_4h is not None and len(df_4h) >= 10:
            tp = float(df_4h["high"].tail(50).max())
    else:
        candidates = []
        for n, v in session_levels.items():
            if v["low"] < entry * (1 - min_dist):
                candidates.append(v["low"])
        if prev_low and prev_low < entry * (1 - min_dist):
            candidates.append(prev_low)
        if candidates:
            tp = min(candidates)
        elif df_4h is not None and len(df_4h) >= 10:
            tp = float(df_4h["low"].tail(50).min())

    return round(tp, 2) if tp else None


# ── MASTER ANALYSIS ───────────────────────────────────────────────────────────
def run_full_analysis(df_daily, df_4h, df_1h, df_15min,
                      df_5min=None, df_1min=None):

    result = {
        "biases":        {},
        "direction":     "ranging",
        "bull_count":    0,
        "bear_count":    0,
        "kill_zone":     None,
        "confluences":   {
            "bias":        False,
            "bos":         False,
            "order_block": False,
            "liquidity":   False,
            "imbalance":   False,
        },
        "confluence_count": 0,
        "ob":            None,
        "fvg":           None,
        "liquidity_level": None,
        "ob_tapped":     False,
        "confirmation":  {"confirmed": False, "level": None},
        "trade":         {"signal": None, "entry": None, "sl": None,
                          "tp": None, "quality": None},
        "session_levels": {},
    }

    # ── Confluence 1: Structural Bias ─────────────────────────────────────────
    biases, direction, bull, bear = get_overall_bias(
        df_daily, df_4h, df_1h, df_15min)
    result["biases"]     = biases
    result["direction"]  = direction
    result["bull_count"] = bull
    result["bear_count"] = bear

    if direction == "ranging":
        return result  # No trade - ranging

    result["confluences"]["bias"] = True
    result["confluence_count"] += 1

    # Kill zone
    if df_15min is not None and "datetime" in df_15min.columns and len(df_15min) >= 2:
        last_dt = df_15min["datetime"].iloc[-2]
        if hasattr(last_dt, "to_pydatetime"):
            last_dt = last_dt.to_pydatetime()
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        result["kill_zone"] = get_kill_zone(last_dt)

    # Session levels
    session_levels = get_session_levels(df_1h)
    result["session_levels"] = session_levels
    prev_h, prev_l = get_prev_day_levels(df_daily)

    if df_15min is None or len(df_15min) < 20:
        return result

    current_price = float(df_15min["close"].iloc[-2])

    # ── Confluence 2: BOS on 15min ────────────────────────────────────────────
    bos_found, bos_level, bos_bar = detect_bos_15min(df_15min, direction)
    if bos_found:
        result["confluences"]["bos"] = True
        result["confluence_count"] += 1

        # ── Confluence 3: Order Block (origin of BOS) ─────────────────────────
        ob = get_order_block(df_15min, direction, bos_bar)
        if ob:
            result["confluences"]["order_block"] = True
            result["confluence_count"] += 1
            result["ob"] = ob

            # ── Confluence 4: Liquidity Cleared ──────────────────────────────
            liq_cleared, liq_level = check_liquidity_cleared(
                df_15min, direction, ob)
            if liq_cleared:
                result["confluences"]["liquidity"] = True
                result["confluence_count"] += 1
                result["liquidity_level"] = liq_level

            # ── Confluence 5: Imbalance (FVG near OB) ────────────────────────
            imb_found, fvg = check_imbalance(df_15min, direction, ob)
            if imb_found:
                result["confluences"]["imbalance"] = True
                result["confluence_count"] += 1
                result["fvg"] = fvg

            # ── Check if price is in OB ───────────────────────────────────────
            ob_tapped = price_in_ob(current_price, ob)
            result["ob_tapped"] = ob_tapped

            # ── Confirmation: 5min CHoCH ──────────────────────────────────────
            if ob_tapped and df_5min is not None:
                confirmed, choch_level = confirm_5min_choch(df_5min, direction)
                result["confirmation"] = {
                    "confirmed": confirmed,
                    "level":     choch_level,
                }

                # ── Trade Signal ──────────────────────────────────────────────
                # Need all 5 confluences + confirmation
                all_confluences = result["confluence_count"] >= 5
                if confirmed and all_confluences:
                    signal = "BUY" if direction == "bullish" else "SELL"
                    entry  = current_price
                    buf    = 0.0003

                    # SL: below OB low (bullish) or above OB high (bearish)
                    # + small buffer for wick
                    if signal == "BUY":
                        sl = round(ob["bot"] * (1 - buf), 2)
                    else:
                        sl = round(ob["top"] * (1 + buf), 2)

                    tp = get_tp(direction, entry, session_levels,
                                df_4h, prev_h, prev_l)

                    # Quality based on confluence count
                    quality = "HIGH ✅" if all_confluences else "MEDIUM ⚠️"

                    result["trade"] = {
                        "signal":  signal,
                        "entry":   round(entry, 2),
                        "sl":      sl,
                        "tp":      tp,
                        "quality": quality,
                    }

                elif ob_tapped and not confirmed:
                    # OB tapped but waiting for 5min CHoCH
                    result["trade"]["signal"] = "WAITING"

    return result
