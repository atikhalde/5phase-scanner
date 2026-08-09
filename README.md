# 5-Phase Pattern Scanner — Same Logic as 1291-Trades Backtest

**Logic (exact same as backtest that found 1291 trades on full NSE EQ 2075):**

- **Phase1 Anchor:** Max high 90-180 trading days old, not broken by CLOSE last 90 days, Days 90-180
- **Phase2 Dry:** Dry90 >=8 (VolRatio<0.5 count in last 90)
- **Phase3 Breakout:** Close > Anchor + VolRatio>1.5 + Close>EMA50
- **Phase4a Rally Continuation:** Max high in breakout+1 to +7 days must be > breakout High *1.01 (proves survival, filters ABDL failed breakout)
- **Phase4b Shakeout:** Low vol <1.0 in rally+1 to rally+15, drop 4-25% from shake_high (max high breakout to low)
- **Phase5 Reversal:** Bullish close>prev high and >shake low high + VolRatio>0.6 and increasing

This fixes:
- ABDL 06/07/2026 → 0 trades (failed breakout, no rally)
- CNL 09/07 rally 13/07 996 → shake 22/07 Vol 0.08 + 23/07 Vol 0.07 → reversal 24/07 Vol 9.21
- AEGISVOPAK 07/07 rally 13/07 shake 14-16/07, 17/07 Doji/MorningStar → entry 20/07 (not 21/07)
- CYIENTDLM 03/07 rally till 10/07 vol dropping till 15/07 → reversal 16/07
- PANAMAPET 22/05 rally till 29/05 no-vol, fall 29/05+01/06 no-vol → reversal 03/06

## Workflows

### 1. Daily Scanner (`daily_scanner.yml`)
- **Schedule:** Every 15 min during NSE market hours 9:30-15:30 IST (04:00-10:00 UTC Mon-Fri) + EOD 15:45 IST (10:15 UTC)
- **Universe:** Nifty500 (as per your selection) — change in `daily_scanner.py` to full EQ 2075 if needed
- **Alerts:**
  - Watchlist: breakout in last 7 days waiting shakeout
  - Breakout Today: Phase3 breakout today
  - Reversal Entry Today: Phase5 entry today
- **Telegram:** Requires GitHub Secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

**Setup Secrets:**
1. Create bot via @BotFather, get token
2. Get chat ID: send message to bot, then `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. In GitHub repo → Settings → Secrets and variables → Actions → New repository secret → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

### 2. Past Backtest ( `backtest_5y.yml` )
- **Runs:** Last 5 years on Nifty500 (500 tickers) with same 1291-trade logic
- **Output:** `backtest_5y_nifty500.csv` (sorted latest breakout first, symbol first) + `backtest_5y_report.pdf` with:
  - Summary stats, yearly breakdown, trades/month
  - Top 20 latest trades table
  - 4 example charts (CNL, AEGISVOPAK, CYIENTDLM, PANAMAPET)
  - Forward performance: TP1/TP2 hit rate, max gain 130d

**Expected counts:**
- Full NSE 2075 EQ with this logic = 1291 trades / 5Y = 21.5/month → with rally>breakout filter = 3.45/month high-quality
- Nifty500 with same logic = ~311 trades /5Y = 5.1/month → with rally filter ~150-200 trades

## Files
- `scanner.py` — exact same conditions
- `daily_scanner.py` — daily watchlist + live alerts
- `telegram_helper.py` — Telegram sender
- `backtest_5y.py` — 5Y backtest + PDF
- `.github/workflows/daily_scanner.yml`
- `.github/workflows/backtest_5y.yml`
- `requirements.txt`

## Local Test
```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=xxx
export TELEGRAM_CHAT_ID=yyy
python daily_scanner.py
python backtest_5y.py
```
