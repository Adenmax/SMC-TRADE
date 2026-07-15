# SMC Signal Bot (Free, Telegram Alerts)

Polls Gold (XAU/USD) and GBP/JPY price data, runs the SMC detection logic
(BOS/CHoCH, order blocks, FVGs, liquidity sweeps, premium/discount setups),
and sends new signals to Telegram. Runs entirely on free infrastructure —
no TradingView paid plan required.

## How it works

`run_alerts.py` runs on a schedule (every 15 minutes via GitHub Actions).
Each run it:

1. Pulls the latest ~150 bars for XAU/USD and GBP/JPY from Twelve Data's
   free API.
2. Runs `smc_logic.py` over those bars to compute structure shifts, order
   blocks, FVGs, and liquidity sweeps.
3. Checks the last fully closed bar. If it's a new bar (tracked in
   `state.json`) and it has an active signal, sends a Telegram message.
4. Commits the updated `state.json` back to the repo so the next run knows
   what's already been alerted.

## One-time setup

1. **Twelve Data API key** (free): sign up at twelvedata.com, grab your API
   key from the dashboard. The free tier covers XAU/USD and GBP/JPY with
   800 requests/day — plenty for checking 2 symbols every 15 minutes
   (~192 requests/day).

2. **Telegram bot**: message @BotFather on Telegram, run `/newbot`, save
   the bot token it gives you. Then send any message to your new bot and
   visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   to find your numeric chat ID in the response.

3. **Create a GitHub repo** and push these files to it (a **public** repo
   is recommended — GitHub Actions minutes are unlimited on public repos;
   private repos get 2,000 free minutes/month, and running every 15
   minutes can use roughly that much, so widen the cron interval to
   `*/30` if you keep it private).

4. **Add repo secrets**: Settings → Secrets and variables → Actions → New
   repository secret. Add `TWELVE_DATA_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`.

5. The workflow in `.github/workflows/smc_alerts.yml` will start running
   automatically on its schedule. You can also trigger it manually from
   the Actions tab (workflow_dispatch) to test it immediately.

## Tuning

All the detection parameters are arguments to `compute_indicators()` in
`smc_logic.py` — swing lookback, order block search-back window, FVG/OB
max age, ATR length, and minimum sweep wick size. Defaults are reasonable
generic starting points; Gold's volatility profile is different from
GBP/JPY's, so it's worth backtesting and adjusting `min_sweep_atr` and
`swing_len` per symbol once you've watched it run for a while.

`SMC_INTERVAL` (default `15min`) controls the candle timeframe pulled from
Twelve Data. Valid values include `1min`, `5min`, `15min`, `1h`, `4h`,
`1day`. If you change it, also adjust the cron schedule so you're not
polling much faster than new bars actually form.

## Honest limitations

- GitHub Actions' scheduled cron isn't second-precise — expect alerts to
  lag the actual bar close by anywhere from under a minute to several
  minutes, especially on the free tier during busy periods. Fine for swing
  setups on 15min/1h/4h charts, not built for scalping.
- Twelve Data's free tier delivers data with some delay versus true
  real-time institutional feeds. Good enough for confirmation-based SMC
  entries, not for sub-second execution.
- This is rule-based pattern detection, not a predictive model — it flags
  when price action matches the SMC criteria you defined, the same way a
  human would scan a chart. It doesn't know whether a setup will work.
