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
    entry = sl = tp1 = tp2 = None
    buffer_pct = 0.0003

    if signal == "BUY":
        fvg15 = conf.get("bull_fvg_15min")
        fvg1h = poi.get("bull_fvg_1h")
        ob    = poi.get("bull_ob_1h")
        if fvg15:
            entry = fvg15["top"]
            sl    = fvg15["bot"] * (1 - buffer_pct)
        elif fvg1h:
            entry = fvg1h["top"]
            sl    = fvg1h["bot"] * (1 - buffer_pct)
        elif ob:
            entry = (ob["top"] + ob["bot"]) / 2
            sl    =
