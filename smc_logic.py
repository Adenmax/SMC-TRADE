"""
SMC signal detection logic (Python port of the SMC_Signals.pine indicator).

Given a DataFrame with columns: datetime, open, high, low, close (ascending
order, oldest first), compute_indicators() returns the same DataFrame with
extra boolean columns marking SMC events on each bar:

    bullish_choch, bullish_bos, bearish_choch, bearish_bos
    bull_fvg, bear_fvg
    bullish_sweep, bearish_sweep
    bull_ob_retest, bear_ob_retest
    long_setup, short_setup
"""

import numpy as np
import pandas as pd


def _find_pivots(high, low, swing_len):
    n = len(high)
    pivot_high = [False] * n
    pivot_low = [False] * n
    for i in range(swing_len, n - swing_len):
        window_h = high[i - swing_len : i + swing_len + 1]
        if high[i] == window_h.max():
            pivot_high[i] = True
        window_l = low[i - swing_len : i + swing_len + 1]
        if low[i] == window_l.min():
            pivot_low[i] = True
    return pivot_high, pivot_low


def _atr(high, low, close, length):
    n = len(high)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return pd.Series(tr).rolling(length, min_periods=1).mean().values


def compute_indicators(
    df,
    swing_len=5,
    ob_search_back=15,
    ob_max_age=80,
    atr_len=14,
    min_sweep_atr=0.15,
):
    df = df.copy().reset_index(drop=True)
    n = len(df)

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    open_ = df["open"].values

    pivot_high, pivot_low = _find_pivots(high, low, swing_len)
    atr = _atr(high, low, close, atr_len)

    last_swing_high = np.nan
    last_swing_low = np.nan
    trend = 0  # 1 bullish structure, -1 bearish structure, 0 unknown

    bull_ob = None  # dict: top, bot, bar, touched
    bear_ob = None

    cols = [
        "bullish_choch", "bullish_bos", "bearish_choch", "bearish_bos",
        "bull_fvg", "bear_fvg", "bullish_sweep", "bearish_sweep",
        "bull_ob_retest", "bear_ob_retest", "long_setup", "short_setup",
    ]
    results = {c: [False] * n for c in cols}

    for j in range(n):
        # A pivot at index (j - swing_len) only becomes "known" swing_len
        # bars later, same lag as ta.pivothigh/pivotlow in Pine.
        confirm_idx = j - swing_len
        if confirm_idx >= 0:
            if pivot_high[confirm_idx]:
                last_swing_high = high[confirm_idx]
            if pivot_low[confirm_idx]:
                last_swing_low = low[confirm_idx]

        bull_break = (
            j > 0
            and not np.isnan(last_swing_high)
            and close[j] > last_swing_high
            and close[j - 1] <= last_swing_high
        )
        bear_break = (
            j > 0
            and not np.isnan(last_swing_low)
            and close[j] < last_swing_low
            and close[j - 1] >= last_swing_low
        )

        bullish_choch = bullish_bos = bearish_choch = bearish_bos = False
        if bull_break:
            if trend <= 0:
                bullish_choch = True
            else:
                bullish_bos = True
            trend = 1
        if bear_break:
            if trend >= 0:
                bearish_choch = True
            else:
                bearish_bos = True
            trend = -1

        results["bullish_choch"][j] = bullish_choch
        results["bullish_bos"][j] = bullish_bos
        results["bearish_choch"][j] = bearish_choch
        results["bearish_bos"][j] = bearish_bos

        # ---- Order blocks ----
        if bullish_bos or bullish_choch:
            top = bot = fb = None
            for k in range(j - 1, max(j - 1 - ob_search_back, -1), -1):
                if close[k] < open_[k]:
                    top, bot, fb = high[k], low[k], k
                    break
            if top is not None:
                bull_ob = {"top": top, "bot": bot, "bar": fb, "touched": False}

        if bearish_bos or bearish_choch:
            top = bot = fb = None
            for k in range(j - 1, max(j - 1 - ob_search_back, -1), -1):
                if close[k] > open_[k]:
                    top, bot, fb = high[k], low[k], k
                    break
            if top is not None:
                bear_ob = {"top": top, "bot": bot, "bar": fb, "touched": False}

        bull_ob_retest = False
        if bull_ob is not None:
            if (
                low[j] <= bull_ob["top"]
                and low[j] >= bull_ob["bot"]
                and j > bull_ob["bar"]
                and not bull_ob["touched"]
            ):
                bull_ob_retest = True
                bull_ob["touched"] = True
            if close[j] < bull_ob["bot"] or (j - bull_ob["bar"]) > ob_max_age:
                bull_ob = None

        bear_ob_retest = False
        if bear_ob is not None:
            if (
                high[j] >= bear_ob["bot"]
                and high[j] <= bear_ob["top"]
                and j > bear_ob["bar"]
                and not bear_ob["touched"]
            ):
                bear_ob_retest = True
                bear_ob["touched"] = True
            if close[j] > bear_ob["top"] or (j - bear_ob["bar"]) > ob_max_age:
                bear_ob = None

        results["bull_ob_retest"][j] = bull_ob_retest
        results["bear_ob_retest"][j] = bear_ob_retest

        # ---- Fair value gaps (3-bar imbalance) ----
        bull_fvg = j >= 2 and low[j] > high[j - 2]
        bear_fvg = j >= 2 and high[j] < low[j - 2]
        results["bull_fvg"][j] = bull_fvg
        results["bear_fvg"][j] = bear_fvg

        # ---- Liquidity sweeps ----
        bullish_sweep = (
            not np.isnan(last_swing_low)
            and not np.isnan(atr[j])
            and low[j] < last_swing_low
            and close[j] > last_swing_low
            and (last_swing_low - low[j]) > min_sweep_atr * atr[j]
        )
        bearish_sweep = (
            not np.isnan(last_swing_high)
            and not np.isnan(atr[j])
            and high[j] > last_swing_high
            and close[j] < last_swing_high
            and (high[j] - last_swing_high) > min_sweep_atr * atr[j]
        )
        results["bullish_sweep"][j] = bullish_sweep
        results["bearish_sweep"][j] = bearish_sweep

        # ---- Premium / discount zone ----
        eq_level = np.nan
        if not np.isnan(last_swing_high) and not np.isnan(last_swing_low):
            eq_level = (last_swing_high + last_swing_low) / 2
        in_discount = not np.isnan(eq_level) and close[j] < eq_level
        in_premium = not np.isnan(eq_level) and close[j] > eq_level

        results["long_setup"][j] = (
            bullish_choch or bullish_bos or bull_ob_retest
        ) and in_discount
        results["short_setup"][j] = (
            bearish_choch or bearish_bos or bear_ob_retest
        ) and in_premium

    for c in cols:
        df[c] = results[c]

    return df
