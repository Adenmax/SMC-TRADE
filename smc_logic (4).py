"""
SMC Logic v7 - Complete rebuild based on Callisto FX (Nick) exact rules
Bias: Daily->4H->1H->15min majority vote
Zone: 1H or 15min FVG/iFVG/failed S-R/range extreme (untapped only)
Confirmation: CHoCH / iFVG retest / Engulfing on 1min or 5min
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


# ── BIAS ─────────────────────────────────────────────────────────────────────
def get_bias(df, swing_n=5):
    if df is None or len(df) < swing_n * 2 + 2:
        return "ranging"
    h = df["high"].values
    l = df["low"].values
    ph, pl = _swing_points(h, l, swing_n)
    highs = [h[i] for i in range(len(h)) if ph[i]]
    lows  = [l[i] for i in range(len(l)) if pl[i]]
    if len(highs) < 2 or len(lows) < 2:
        return "ranging"
    # Use most recent swing points only
    hh = highs[-1] > highs[-2]
    hl = lows[-1]  > lows[-2]
    ll = lows[-1]  < lows[-2]
    lh = highs[-1] < highs[-2]
    if hh and hl: return "bullish"
    if ll and lh: return "bearish"
    return "ranging"


def get_overall_bias(df_daily, df_4h, df_1h, df_15min):
    """
    Majority vote across 4 timeframes.
    3/4 or 4/4 = clear bias. 2/4 = ranging.
    """
    biases = {
        "daily": get_bias(df_daily),
        "4h":    get_bias(df_4h),
        "1h":    get_bias(df_1h),
        "15min": get_bias(df_15min),
    }
    bull = sum(1 for v in biases.values() if v == "bullish")
    bear = sum(1 for v in biases.values() if v == "bearish")

    if bull >= 3:
        direction = "bullish"
    elif bear >= 3:
        direction = "bearish"
    else:
        direction = "ranging"

    return biases, direction, bull, bear


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


# ── ZONE BUILDER ──────────────────────────────────────────────────────────────
def get_fvgs(df, direction="bullish", max_age=50):
    """
    Get all FVGs. Mark each as:
    - tapped: price already entered the zone (invalid as POI)
    - untapped: price has not entered yet (valid POI)
    """
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
            top = l[i]
            bot = h[i - 2]
            # candle-3 rule
            if c[i] > h[i - 1]:
                continue
            tapped = any(l[j] <= top for j in range(i + 1, n))
            fvgs.append({"top": top, "bot": bot, "bar": i,
                         "tapped": tapped, "direction": "bullish",
                         "type": "fvg"})
        elif direction == "bearish" and h[i] < l[i - 2]:
            top = l[i - 2]
            bot = h[i]
            if c[i] < l[i - 1]:
                continue
            tapped = any(h[j] >= bot for j in range(i + 1, n))
            fvgs.append({"top": top, "bot": bot, "bar": i,
                         "tapped": tapped, "direction": "bearish",
                         "type": "fvg"})

    return fvgs


def detect_ifvg(df, direction="bullish", max_age=50):
    """
    iFVG = a FVG that has been broken through (flipped).
    Bullish FVG broken to downside = bearish iFVG (sell zone).
    Bearish FVG broken to upside = bullish iFVG (buy zone).
    The flip zone = untapped resistance/support.
    """
    if df is None or len(df) < 5:
        return []
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    ifvgs = []

    for i in range(2, n - 2):
        if (n - i) > max_age:
            continue

        if direction == "bearish":
            # Look for bullish FVG
            if l[i] > h[i - 2]:
                fvg_top = l[i]
                fvg_bot = h[i - 2]
                # Check if price broke below this FVG later
                for j in range(i + 1, n):
                    if c[j] < fvg_bot:
                        # FVG broken → now a sell zone (iFVG)
                        # Check if price has come back up to it (tapped)
                        tapped = any(h[k] >= fvg_bot for k in range(j + 1, n))
                        ifvgs.append({
                            "top": fvg_top, "bot": fvg_bot,
                            "bar": j, "tapped": tapped,
                            "direction": "bearish", "type": "ifvg"
                        })
                        break

        elif direction == "bullish":
            # Look for bearish FVG
            if h[i] < l[i - 2]:
                fvg_top = l[i - 2]
                fvg_bot = h[i]
                # Check if price broke above this FVG later
                for j in range(i + 1, n):
                    if c[j] > fvg_top:
                        tapped = any(l[k] <= fvg_top for k in range(j + 1, n))
                        ifvgs.append({
                            "top": fvg_top, "bot": fvg_bot,
                            "bar": j, "tapped": tapped,
                            "direction": "bullish", "type": "ifvg"
                        })
                        break

    return ifvgs


def detect_failed_sr(df_daily, df_1h, direction="bearish"):
    """
    Failed support/resistance:
    Previous daily/4H level that broke → flipped to opposite zone.
    Returns the level as a zone.
    """
    if df_daily is None or len(df_daily) < 10:
        return None
    h = df_daily["high"].values
    l = df_daily["low"].values
    c = df_daily["close"].values
    n = len(df_daily)

    if direction == "bearish":
        # Find a previous support (swing low) that price broke below
        ph, pl = _swing_points(h, l, 3)
        swing_lows = [(i, l[i]) for i in range(n) if pl[i]]
        for idx, level in reversed(swing_lows[-5:]):
            # Check if price later broke below this level
            for j in range(idx + 1, n):
                if c[j] < level:
                    # Support broken → resistance zone
                    zone_top = level * 1.002
                    zone_bot = level * 0.998
                    tapped = any(h[k] >= zone_bot for k in range(j + 1, n))
                    return {"top": zone_top, "bot": zone_bot,
                            "tapped": tapped, "direction": "bearish",
                            "type": "failed_sr", "level": level}
                    break
    else:
        ph, pl = _swing_points(h, l, 3)
        swing_highs = [(i, h[i]) for i in range(n) if ph[i]]
        for idx, level in reversed(swing_highs[-5:]):
            for j in range(idx + 1, n):
                if c[j] > level:
                    zone_top = level * 1.002
                    zone_bot = level * 0.998
                    tapped = any(l[k] <= zone_top for k in range(j + 1, n))
                    return {"top": zone_top, "bot": zone_bot,
                            "tapped": tapped, "direction": "bullish",
                            "type": "failed_sr", "level": level}
                    break
    return None


def detect_range_extreme(df, direction="bullish", lookback=50):
    """
    Range extreme: if price has been ranging, identify floor and ceiling.
    Bottom of range = buy zone. Top of range = sell zone.
    """
    if df is None or len(df) < lookback:
        return None
    recent = df.tail(lookback)
    h = recent["high"].values
    l = recent["low"].values
    c = recent["close"].values

    # Check if ranging: price bouncing between levels
    high_max = float(np.max(h))
    high_min = float(np.min(h))
    low_max  = float(np.max(l))
    low_min  = float(np.min(l))
    price_range = high_max - low_min

    # Range detected if price oscillates within 2% band
    if price_range / high_max > 0.02:
        return None  # trending, not ranging

    current = float(c[-1])

    if direction == "bullish":
        # Bottom of range
        zone_bot = low_min
        zone_top = low_min * 1.003
        tapped = current <= zone_top
        return {"top": zone_top, "bot": zone_bot,
                "tapped": tapped, "direction": "bullish",
                "type": "range_extreme"}
    else:
        # Top of range
        zone_top = high_max
        zone_bot = high_max * 0.997
        tapped = current >= zone_bot
        return {"top": zone_top, "bot": zone_bot,
                "tapped": tapped, "direction": "bearish",
                "type": "range_extreme"}


def build_zones(df_daily, df_1h, df_15min, direction):
    """
    Build all candidate zones for the given direction.
    Priority: most recent untapped zone first.
    Returns list sorted by bar index (most recent first).
    """
    zones = []

    # 1H FVGs (untapped)
    fvgs_1h = get_fvgs(df_1h, direction, max_age=80)
    for z in fvgs_1h:
        if not z["tapped"]:
            z["tf"] = "1H"
            z["confluence"] = ["1H FVG"]
            zones.append(z)

    # 15min FVGs (untapped)
    fvgs_15 = get_fvgs(df_15min, direction, max_age=50)
    for z in fvgs_15:
        if not z["tapped"]:
            z["tf"] = "15min"
            z["confluence"] = ["15min FVG"]
            zones.append(z)

    # iFVGs on 1H (untapped)
    ifvgs_1h = detect_ifvg(df_1h, direction, max_age=80)
    for z in ifvgs_1h:
        if not z["tapped"]:
            z["tf"] = "1H"
            z["confluence"] = ["1H iFVG (flipped)"]
            zones.append(z)

    # iFVGs on 15min (untapped)
    ifvgs_15 = detect_ifvg(df_15min, direction, max_age=50)
    for z in ifvgs_15:
        if not z["tapped"]:
            z["tf"] = "15min"
            z["confluence"] = ["15min iFVG (flipped)"]
            zones.append(z)

    # Failed S/R from daily
    fsr = detect_failed_sr(df_daily, df_1h, direction)
    if fsr and not fsr["tapped"]:
        fsr["tf"] = "Daily"
        fsr["confluence"] = ["Failed S/R (daily level flipped)"]
        zones.append(fsr)

    # Range extreme
    re_1h = detect_range_extreme(df_1h, direction)
    if re_1h and not re_1h["tapped"]:
        re_1h["tf"] = "1H"
        re_1h["confluence"] = ["Range extreme"]
        zones.append(re_1h)

    # Sort by bar index — most recent first
    zones.sort(key=lambda x: x.get("bar", 0), reverse=True)

    # Merge zones that overlap — add to confluence list
    merged = []
    used = set()
    for i, z in enumerate(zones):
        if i in used:
            continue
        current = dict(z)
        for j, z2 in enumerate(zones):
            if j <= i or j in used:
                continue
            # Check overlap
            overlap = (current["bot"] <= z2["top"] and z2["bot"] <= current["top"])
            if overlap:
                current["top"] = max(current["top"], z2["top"])
                current["bot"] = min(current["bot"], z2["bot"])
                current["confluence"] = current.get("confluence", []) + z2.get("confluence", [])
                used.add(j)
        merged.append(current)
        used.add(i)

    return merged


def price_in_zone(price, zone):
    if zone is None:
        return False
    return zone["bot"] <= price <= zone["top"]


# ── SESSION LEVELS ────────────────────────────────────────────────────────────
def get_session_levels(df_1h):
    if df_1h is None or "datetime" not in df_1h.columns:
        return {}
    df = df_1h.copy()
    df["hour_wat"] = (df["datetime"].dt.hour + 1) % 24

    sessions = {
        "Asian":    ((1,  5)),
        "London":   ((7,  10)),
        "NY AM":    ((14, 17)),
        "NY Lunch": ((17, 18)),
        "NY PM":    ((18, 21)),
    }
    result = {}
    for name, (start, end) in sessions.items():
        mask = (df["hour_wat"] >= start) & (df["hour_wat"] < end)
        sub = df[mask]
        if len(sub) >= 1:
            result[name] = {
                "high": float(sub["high"].max()),
                "low":  float(sub["low"].min()),
            }
    return result


def get_tp(direction, current_price, session_levels, df_4h):
    """Next meaningful session level or 4H swing as TP."""
    min_dist = 0.003  # at least 0.3% away
    tp = None

    if direction == "bullish":
        candidates = [(n, v["high"]) for n, v in session_levels.items()
                      if v["high"] > current_price * (1 + min_dist)]
        if candidates:
            tp = max(candidates, key=lambda x: x[1])[1]
        if tp is None and df_4h is not None and len(df_4h) >= 10:
            tp = float(df_4h["high"].tail(50).max())
    else:
        candidates = [(n, v["low"]) for n, v in session_levels.items()
                      if v["low"] < current_price * (1 - min_dist)]
        if candidates:
            tp = min(candidates, key=lambda x: x[1])[1]
        if tp is None and df_4h is not None and len(df_4h) >= 10:
            tp = float(df_4h["low"].tail(50).min())

    return round(tp, 2) if tp else None


# ── CONFIRMATIONS (1min / 5min) ───────────────────────────────────────────────
def confirm_choch(df, direction="bullish", swing_n=2):
    """
    CHoCH: body candle close breaks most recent structure.
    Bullish: body close above most recent lower high.
    Bearish: body close below most recent higher low.
    Returns (confirmed, level)
    """
    if df is None or len(df) < 10:
        return False, None
    h = df["high"].values[-30:]
    l = df["low"].values[-30:]
    o = df["open"].values[-30:]
    c = df["close"].values[-30:]
    ph, pl = _swing_points(h, l, swing_n)

    if direction == "bullish":
        swing_highs = [h[i] for i in range(len(h)) if ph[i]]
        if not swing_highs:
            return False, None
        last_lh = swing_highs[-1]
        body_high = max(o[-1], c[-1])
        if body_high > last_lh and c[-2] <= last_lh:
            return True, last_lh
    else:
        swing_lows = [l[i] for i in range(len(l)) if pl[i]]
        if not swing_lows:
            return False, None
        last_hl = swing_lows[-1]
        body_low = min(o[-1], c[-1])
        if body_low < last_hl and c[-2] >= last_hl:
            return True, last_hl

    return False, None


def confirm_ifvg(df, direction="bullish", lookback=20):
    """
    iFVG retest on entry TF:
    A FVG formed on entry TF that price has now retested.
    """
    if df is None or len(df) < 5:
        return False, None
    h = df["high"].values[-lookback:]
    l = df["low"].values[-lookback:]
    c = df["close"].values[-lookback:]
    n = len(h)

    for i in range(2, n - 1):
        if direction == "bullish" and l[i] > h[i - 2]:
            top = l[i]
            bot = h[i - 2]
            # Check retest in last 3 bars
            for j in range(max(i + 1, n - 3), n):
                if l[j] <= top and c[j] >= bot:
                    return True, (top + bot) / 2
        elif direction == "bearish" and h[i] < l[i - 2]:
            top = l[i - 2]
            bot = h[i]
            for j in range(max(i + 1, n - 3), n):
                if h[j] >= bot and c[j] <= top:
                    return True, (top + bot) / 2

    return False, None


def confirm_engulfing(df, direction="bullish"):
    """Engulfing candle on entry TF."""
    if df is None or len(df) < 3:
        return False
    idx = len(df) - 2
    o = df["open"].values
    c = df["close"].values
    if direction == "bullish":
        if (c[idx] > o[idx] and c[idx-1] < o[idx-1]
                and o[idx] <= c[idx-1] and c[idx] >= o[idx-1]):
            return True
    else:
        if (c[idx] < o[idx] and c[idx-1] > o[idx-1]
                and o[idx] >= c[idx-1] and c[idx] <= o[idx-1]):
            return True
    return False


def get_confirmation(df_5min, df_1min, direction):
    """
    Check all 3 confirmation types on 5min first, then 1min.
    Returns (confirmed, type, timeframe, level)
    """
    for tf_name, df in [("5min", df_5min), ("1min", df_1min)]:
        if df is None or len(df) < 5:
            continue

        # 1. CHoCH (strongest)
        ok, level = confirm_choch(df, direction)
        if ok:
            return True, "CHoCH", tf_name, level

        # 2. iFVG retest
        ok, level = confirm_ifvg(df, direction)
        if ok:
            return True, "iFVG retest", tf_name, level

        # 3. Engulfing
        if confirm_engulfing(df, direction):
            return True, "Engulfing", tf_name, None

    return False, None, None, None


# ── MASTER ANALYSIS ───────────────────────────────────────────────────────────
def run_full_analysis(df_daily, df_4h, df_1h, df_15min,
                      df_5min=None, df_1min=None):

    result = {
        "biases":       {},
        "direction":    "ranging",
        "bull_count":   0,
        "bear_count":   0,
        "kill_zone":    None,
        "zones":        [],
        "active_zone":  None,
        "zone_tapped":  False,
        "confirmation": {"confirmed": False, "type": None,
                         "tf": None, "level": None},
        "trade":        {"signal": None, "entry": None, "sl": None,
                         "tp": None, "zone": None, "quality": None},
        "session_levels": {},
    }

    # Bias
    biases, direction, bull, bear = get_overall_bias(
        df_daily, df_4h, df_1h, df_15min)
    result["biases"]     = biases
    result["direction"]  = direction
    result["bull_count"] = bull
    result["bear_count"] = bear

    # Kill zone
    if df_15min is not None and "datetime" in df_15min.columns and len(df_15min) >= 2:
        last_dt = df_15min["datetime"].iloc[-2]
        if hasattr(last_dt, "to_pydatetime"):
            last_dt = last_dt.to_pydatetime()
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        result["kill_zone"] = get_kill_zone(last_dt)

    if direction == "ranging":
        return result

    # Current price
    if df_15min is None or len(df_15min) < 2:
        return result
    current_price = float(df_15min["close"].iloc[-2])

    # Session levels
    session_levels = get_session_levels(df_1h)
    result["session_levels"] = session_levels

    # Build zones
    zones = build_zones(df_daily, df_1h, df_15min, direction)
    result["zones"] = zones

    if not zones:
        return result

    # Active zone = most recent untapped zone
    active_zone = zones[0]
    result["active_zone"] = active_zone

    # Check if price is in zone
    zone_tapped = price_in_zone(current_price, active_zone)
    result["zone_tapped"] = zone_tapped

    # If zone is tapped check for confirmation
    if zone_tapped:
        confirmed, conf_type, conf_tf, conf_level = get_confirmation(
            df_5min, df_1min, direction)

        result["confirmation"] = {
            "confirmed": confirmed,
            "type":      conf_type,
            "tf":        conf_tf,
            "level":     conf_level,
        }

        if confirmed:
            signal = "BUY" if direction == "bullish" else "SELL"

            # Entry at zone edge or confirmation level
            if conf_level:
                entry = round(conf_level, 2)
            else:
                entry = round(active_zone["top"] if signal == "BUY"
                              else active_zone["bot"], 2)

            # SL just beyond zone
            buf = 0.0003
            sl = round(active_zone["bot"] * (1 - buf), 2) if signal == "BUY" \
                 else round(active_zone["top"] * (1 + buf), 2)

            # TP
            tp = get_tp(direction, current_price, session_levels, df_4h)

            # Quality: number of confluences
            conf_count = len(active_zone.get("confluence", []))
            quality = "HIGH ✅" if conf_count >= 2 else "MEDIUM ⚠️"

            result["trade"] = {
                "signal":  signal,
                "entry":   entry,
                "sl":      sl,
                "tp":      tp,
                "zone":    active_zone,
                "quality": quality,
            }

    return result
