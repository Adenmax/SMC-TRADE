"""
SMC Logic v6 - Clean rebuild matching Callisto FX (Nick) exact flow
Zone = 1H FVG + 1H OB overlap -> price taps zone -> 1min/5min confirmation -> alert
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


# ── BIAS (Daily + 4H only for direction) ─────────────────────────────────────
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
    if hh and hl: return "bullish"
    if ll and lh: return "bearish"
    return "ranging"


# ── KILL ZONE (WAT UTC+1) ────────────────────────────────────────────────────
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


# ── ZONE BUILDER: merge 1H FVG + 1H OB into one zone ────────────────────────
def get_1h_fvg(df, direction="bullish"):
    """Get the most recent valid 1H FVG."""
    if df is None or len(df) < 3:
        return None
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    result = None
    for i in range(2, len(df)):
        if direction == "bullish" and l[i] > h[i - 2]:
            # candle-3 rule: close should not be above high of candle 2
            if c[i] <= h[i - 1]:
                result = {"top": l[i], "bot": h[i - 2], "bar": i}
        elif direction == "bearish" and h[i] < l[i - 2]:
            if c[i] >= l[i - 1]:
                result = {"top": l[i - 2], "bot": h[i], "bar": i}
    return result


def get_1h_ob(df, direction="bullish", swing_n=5, search_back=15):
    """Get the most recent 1H order block."""
    if df is None or len(df) < swing_n * 2 + 2:
        return None
    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    c = df["close"].values
    ph, pl = _swing_points(h, l, swing_n)
    last_sh = last_sl = None
    result = None
    for j in range(len(df)):
        ci = j - swing_n
        if ci >= 0:
            if ph[ci]: last_sh = h[ci]
            if pl[ci]: last_sl = l[ci]
        if direction == "bullish":
            if last_sh is not None and j > 0 and c[j] > last_sh and c[j-1] <= last_sh:
                for k in range(j-1, max(j-search_back, -1), -1):
                    if c[k] < o[k]:
                        result = {"top": h[k], "bot": l[k], "bar": k}
                        break
        else:
            if last_sl is not None and j > 0 and c[j] < last_sl and c[j-1] >= last_sl:
                for k in range(j-1, max(j-search_back, -1), -1):
                    if c[k] > o[k]:
                        result = {"top": h[k], "bot": l[k], "bar": k}
                        break
    return result


def build_zone(df_1h, direction):
    """
    Merge 1H FVG + 1H OB into one zone box.
    Zone top = highest top of either, zone bot = lowest bot of either.
    This is exactly how mentor draws his zones.
    """
    fvg = get_1h_fvg(df_1h, direction)
    ob  = get_1h_ob(df_1h, direction)

    if fvg is None and ob is None:
        return None

    tops = []
    bots = []
    components = []

    if fvg:
        tops.append(fvg["top"])
        bots.append(fvg["bot"])
        components.append("1H FVG")
    if ob:
        tops.append(ob["top"])
        bots.append(ob["bot"])
        components.append("1H OB")

    zone = {
        "top":        max(tops),
        "bot":        min(bots),
        "direction":  direction,
        "components": " + ".join(components),
        "fvg":        fvg,
        "ob":         ob,
    }
    return zone


def price_in_zone(price, zone):
    """Check if current price is inside or has tapped the zone."""
    if zone is None:
        return False
    # Tapped = price has entered the zone from outside
    return zone["bot"] <= price <= zone["top"]


def price_approaching_zone(price, zone, atr, threshold=2.0):
    """Price is within threshold ATRs of the zone."""
    if zone is None:
        return False
    dist = min(abs(price - zone["top"]), abs(price - zone["bot"]))
    return dist <= threshold * atr


# ── CONFIRMATION: 3 types on 1min or 5min ────────────────────────────────────
def detect_choch(df, direction="bullish", swing_n=2):
    """
    CHoCH on entry TF (1min or 5min).
    Bullish: price breaks most recent lower high with BODY candle close.
    Bearish: price breaks most recent higher low with BODY candle close.
    Returns (confirmed: bool, level: float)
    """
    if df is None or len(df) < 10:
        return False, None
    h = df["high"].values[-30:]
    l = df["low"].values[-30:]
    o = df["open"].values[-30:]
    c = df["close"].values[-30:]
    ph, pl = _swing_points(h, l, swing_n)
    swing_highs = [h[i] for i in range(len(h)) if ph[i]]
    swing_lows  = [l[i] for i in range(len(l)) if pl[i]]

    if not swing_highs or not swing_lows:
        return False, None

    if direction == "bullish":
        # Last lower high = most recent swing high in downtrend
        last_sh = swing_highs[-1]
        # Body close above it
        body_high = max(o[-1], c[-1])
        if body_high > last_sh and c[-2] <= last_sh:
            return True, last_sh
    else:
        # Last higher low = most recent swing low in uptrend
        last_sl = swing_lows[-1]
        body_low = min(o[-1], c[-1])
        if body_low < last_sl and c[-2] >= last_sl:
            return True, last_sl

    return False, None


def detect_ifvg(df, direction="bullish"):
    """
    iFVG on entry TF: a FVG that price has already entered (retested).
    This is the FVG retest confirmation.
    """
    if df is None or len(df) < 5:
        return False, None
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    for i in range(2, n - 1):
        if direction == "bullish" and l[i] > h[i - 2]:
            top = l[i]
            bot = h[i - 2]
            # Check if price came back into this FVG after it formed
            for j in range(i + 1, n):
                if l[j] <= top and c[j] >= bot:
                    # Price retested and closed above bot = iFVG confirmed
                    if j == n - 1 or j == n - 2:
                        return True, (top + bot) / 2
        elif direction == "bearish" and h[i] < l[i - 2]:
            top = l[i - 2]
            bot = h[i]
            for j in range(i + 1, n):
                if h[j] >= bot and c[j] <= top:
                    if j == n - 1 or j == n - 2:
                        return True, (top + bot) / 2

    return False, None


def detect_engulfing(df, direction="bullish"):
    """
    Engulfing candle on entry TF.
    Body of current candle fully engulfs body of previous candle.
    """
    if df is None or len(df) < 3:
        return False
    idx = len(df) - 2
    o = df["open"].values
    c = df["close"].values

    if direction == "bullish":
        # Big green candle engulfs previous red candle
        if (c[idx] > o[idx] and c[idx-1] < o[idx-1]
                and o[idx] <= c[idx-1] and c[idx] >= o[idx-1]):
            return True
    else:
        # Big red candle engulfs previous green candle
        if (c[idx] < o[idx] and c[idx-1] > o[idx-1]
                and o[idx] >= c[idx-1] and c[idx] <= o[idx-1]):
            return True
    return False


def get_confirmation(df_5min, df_1min, direction):
    """
    Check all 3 confirmation types on 5min first, then 1min.
    Returns (confirmed, conf_type, conf_tf, level)
    """
    for tf_name, df in [("5min", df_5min), ("1min", df_1min)]:
        if df is None or len(df) < 5:
            continue

        # 1. CHoCH (strongest)
        choch, level = detect_choch(df, direction)
        if choch:
            return True, "CHoCH", tf_name, level

        # 2. iFVG retest
        ifvg, level = detect_ifvg(df, direction)
        if ifvg:
            return True, "iFVG retest", tf_name, level

        # 3. Engulfing
        if detect_engulfing(df, direction):
            return True, "Engulfing", tf_name, None

    return False, None, None, None


# ── SESSION LEVELS ────────────────────────────────────────────────────────────
def get_session_levels(df_1h):
    if df_1h is None or "datetime" not in df_1h.columns:
        return {}
    df = df_1h.copy()
    df["hour_wat"] = (df["datetime"].dt.hour + 1) % 24

    def mask(s, e):
        return (df["hour_wat"] >= s) & (df["hour_wat"] < e)

    sessions = {
        "Asian":    mask(1,  5),
        "London":   mask(7,  10),
        "NY AM":    mask(14, 17),
        "NY Lunch": mask(17, 18),
        "NY PM":    mask(18, 21),
    }
    result = {}
    for name, m in sessions.items():
        sub = df[m]
        if len(sub) >= 1:
            result[name] = {
                "high": float(sub["high"].max()),
                "low":  float(sub["low"].min()),
            }
    return result


def get_tp_target(direction, current_price, session_levels, df_4h):
    """
    TP = next session liquidity level in the direction of trade.
    Mentor always targets the next $$$ level.
    """
    tp = None
    if direction == "bullish":
        candidates = [(n, v["high"]) for n, v in session_levels.items()
                      if v["high"] > current_price]
        if candidates:
            tp = min(candidates, key=lambda x: x[1] - current_price)[1]
    else:
        candidates = [(n, v["low"]) for n, v in session_levels.items()
                      if v["low"] < current_price]
        if candidates:
            tp = max(candidates, key=lambda x: x[1])[1]

    # If no session level found, use 4H swing
    if tp is None and df_4h is not None and len(df_4h) >= 5:
        if direction == "bullish":
            tp = float(df_4h["high"].tail(20).max())
        else:
            tp = float(df_4h["low"].tail(20).min())

    return tp


# ── MASTER ANALYSIS ───────────────────────────────────────────────────────────
def run_full_analysis(df_daily, df_4h, df_1h, df_15min,
                      df_5min=None, df_1min=None):
    result = {
        "bias":         {},
        "kill_zone":    None,
        "zones":        {"bull": None, "bear": None},
        "zone_tapped":  {"bull": False, "bear": False},
        "confirmation": {"confirmed": False, "type": None,
                         "tf": None, "level": None, "direction": None},
        "trade":        {"signal": None, "entry": None, "sl": None,
                         "tp": None, "zone": None, "quality": None},
        "session_levels": {},
    }

    # Bias
    daily_bias = get_bias(df_daily)
    h4_bias    = get_bias(df_4h)
    result["bias"] = {"daily": daily_bias, "4h": h4_bias}

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

    # Current price
    if df_15min is None or len(df_15min) < 2:
        return result
    current_price = float(df_15min["close"].iloc[-2])

    # ATR for zone proximity check
    h = df_15min["high"].values
    l = df_15min["low"].values
    c = df_15min["close"].values
    atr = float(_atr(h, l, c)[-1])

    # Build zones
    bull_zone = build_zone(df_1h, "bullish")
    bear_zone = build_zone(df_1h, "bearish")
    result["zones"]["bull"] = bull_zone
    result["zones"]["bear"] = bear_zone

    # Check zone tapped
    bull_tapped = price_in_zone(current_price, bull_zone)
    bear_tapped = price_in_zone(current_price, bear_zone)
    result["zone_tapped"]["bull"] = bull_tapped
    result["zone_tapped"]["bear"] = bear_tapped

    # Determine which direction to look for confirmation
    # Priority: if bias is clear, only look in that direction
    # If ranging, look in whichever zone is tapped
    check_bull = bull_tapped and daily_bias in ["bullish", "ranging"]
    check_bear = bear_tapped and daily_bias in ["bearish", "ranging"]

    # If both tapped (shouldn't happen but just in case), use 4H bias
    if check_bull and check_bear:
        if h4_bias == "bullish": check_bear = False
        elif h4_bias == "bearish": check_bull = False

    # Get confirmation
    conf_direction = None
    if check_bull:
        conf_direction = "bullish"
    elif check_bear:
        conf_direction = "bearish"

    if conf_direction:
        confirmed, conf_type, conf_tf, conf_level = get_confirmation(
            df_5min, df_1min, conf_direction)

        result["confirmation"] = {
            "confirmed":  confirmed,
            "type":       conf_type,
            "tf":         conf_tf,
            "level":      conf_level,
            "direction":  conf_direction,
        }

        if confirmed:
            zone = bull_zone if conf_direction == "bullish" else bear_zone
            signal = "BUY" if conf_direction == "bullish" else "SELL"

            # Entry: at confirmation level or zone edge
            if conf_level:
                entry = round(conf_level, 2)
            elif zone:
                entry = round(zone["top"] if signal == "BUY" else zone["bot"], 2)
            else:
                entry = round(current_price, 2)

            # SL: just beyond zone
            buf = 0.0003
            if zone:
                sl = round(zone["bot"] * (1 - buf), 2) if signal == "BUY" \
                     else round(zone["top"] * (1 + buf), 2)
            else:
                sl = None

            # TP: next session liquidity
            tp = get_tp_target(conf_direction, current_price,
                               session_levels, df_4h)
            if tp: tp = round(tp, 2)

            # Quality: HIGH if both FVG+OB in zone, MEDIUM if only one
            quality = "HIGH ✅" if (zone and zone.get("fvg") and zone.get("ob")) \
                      else "MEDIUM ⚠️"

            result["trade"] = {
                "signal":  signal,
                "entry":   entry,
                "sl":      sl,
                "tp":      tp,
                "zone":    zone,
                "quality": quality,
            }

    return result
