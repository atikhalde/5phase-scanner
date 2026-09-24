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
  - Watchlist: breakout waiting shakeout / reversal
  - Breakout Today: Phase3 breakout today
  - Shakeout / Pullback Today: Phase4b low-volume pullback whose low actually reaches the SSL/Supply/OB zone (anchor high → breakout close) today — a mere fall off the high does NOT alert
  - Reversal Entry Today: Phase5 entry today
  - Shake-Low → Green Next Day: Phase4b shake low made on the previous session + GREEN candle (Close > Open) on the latest bar — see "Add-on alert" below
- **Telegram:** Requires GitHub Secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

**Setup Secrets:**
1. Create bot via @BotFather, get token
2. Get chat ID: send message to bot, then `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. In GitHub repo → Settings → Secrets and variables → Actions → New repository secret → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

### 2. Full NSE EQ Scanner (`full_scanner.py`) — NEW
> ⚙️ **Activate the workflow:** the CI file is provided at `workflow_templates/full_scanner.yml`. Copy it to `.github/workflows/full_scanner.yml` and commit (this must be done by a user/token with the GitHub `workflows` permission — the automated agent cannot create workflow files). Once committed it runs on the schedule below. You can also just run `python full_scanner.py` anytime.

- **Schedule:** Once per trading day at EOD 15:45 IST (10:15 UTC Mon-Fri) + manual dispatch
- **Universe:** FULL NSE cash equity (~2000 symbols, `SERIES == EQ` from NSE `EQUITY_L.csv`) — NOT just Nifty500
- **Why:** `daily_scanner.py` only scans Nifty500, so stocks outside that index (e.g. **TVSSRICHAK** / TVS Srichakra) are never fetched and can never fire an alert — even though the full-universe backtest produced trades for them. This scanner covers them.
- **Logic:** *Identical* 5-phase engine as the daily scanner. `full_scanner.py` reuses `scanner.py`, the data-fetch/freshness helpers from `daily_scanner.py`, and the formatters from `telegram_helper.py` — nothing in the existing files is modified.
- **Alerts:** five — Watchlist (breakout waiting shakeout/reversal), Breakout Today (Phase3), Shakeout / Pullback Today (Phase4b), Reversal Entry Today (Phase5), Shake-Low → Green Next Day
- **Artifacts:** `full_*` prefixed CSVs so they never collide with the Nifty500 scanner's output
- **Same secrets** as the daily scanner (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`)
- **Run time:** a ~2000-symbol scan takes far longer than the 15-min Nifty500 cadence, hence EOD-only (workflow timeout 350 min)

Local test (cap symbols for speed):
```bash
export MAX_SYMBOLS=50
python full_scanner.py
```

### 3. Past Backtest ( `backtest_5y.yml` )
- **Runs:** Last 5 years on Nifty500 (500 tickers) with same 1291-trade logic
- **Output:** `backtest_5y_nifty500.csv` (sorted latest breakout first, symbol first) + `backtest_5y_report.pdf` with:
  - Summary stats, yearly breakdown, trades/month
  - Top 20 latest trades table
  - 4 example charts (CNL, AEGISVOPAK, CYIENTDLM, PANAMAPET)
  - Forward performance: TP1/TP2 hit rate, max gain 130d

**Expected counts:**
- Full NSE 2075 EQ with this logic = 1291 trades / 5Y = 21.5/month → with rally>breakout filter = 3.45/month high-quality
- Nifty500 with same logic = ~311 trades /5Y = 5.1/month → with rally filter ~150-200 trades

## Add-on alert: Shake-Low → Green Next Day (2026-09-25)

A **purely additive** 5th alert. Every original phase, condition and alert is
untouched — this only adds a new trigger on top.

**Rule:** whenever a stock makes a **Phase 4b shake low** and the **very next
trading session prints a GREEN candle**, alert on that next session.

| # | Condition | Detail |
|---|-----------|--------|
| 1 | Shake low | The **same** Phase 4b shake low the engine already uses: lowest `Low` among `VolRatio < 1.0` bars in `rally_idx+1 .. rally_idx+15`, with a **4–25%** drop from the shake high. This is exactly the `Shake 97.4 (17.26%)` number the watchlist prints — no new/looser definition. |
| 2 | Timing | Strictly the **next bar (T+1)**. A green candle two or more sessions later does **not** alert. |
| 3 | Green | **Close > Open** (the same bullish-bar test Phase 5 uses). |
| 4 | Phase 5 | **Not** required. It fires on soft green bars the strict reversal rejects, **and** on bars where the strict `REVERSAL ENTRY` also fires — in that case **both** alerts are sent. |

Worked example (the one that motivated it): **ALEMBICLTD.NS** made its shake
low on **2026-09-16** (97.4, −17.26% off the 117.72 rally high) and printed a
green candle on **2026-09-17** → alert dated **2026-09-17**. The strict Phase 5
reversal never fired, which is why ALEMBICLTD still shows *"awaiting
reversal"* in the watchlist while the green candle had already appeared.

Notes
- If the would-be confirmation bar itself makes a **new low on low volume**, it
  becomes the shake low instead: no alert that day, and the alert fires the
  next session if *that* bar turns green (the shakeout is still extending).
- Runs in both scanners (`daily_scanner.py` + `full_scanner.py`). Output CSVs:
  `shake_green_today_*.csv` and `full_shake_green_today_*.csv`.
- Intraday runs use the latest (in-progress) bar as "today", exactly like the
  existing breakout / shakeout / reversal alerts.
- Cost: ~4% of `detect_pending_breakouts` per symbol (a fast low-volume gate
  skips the scan outright unless the previous bar really is low-volume).

Tests: `python3 test_shake_green_alert.py` (49 checks, including the
ALEMBICLTD geometry, "T+1 only", "red bar → silent", "both alerts fire", and
end-to-end runs of both scanners with the data fetch stubbed out).

## Files
- `scanner.py` — exact same conditions
- `daily_scanner.py` — daily watchlist + live alerts (Nifty500)
- `full_scanner.py` — same logic, FULL NSE EQ (~2000 symbols); standalone, reuses `scanner.py`/`daily_scanner.py`/`telegram_helper.py` without modifying them
- `telegram_helper.py` — Telegram sender
- `test_shake_green_alert.py` — tests for the Shake-Low → Green Next Day add-on
- `backtest_5y.py` — 5Y backtest + PDF
- `.github/workflows/daily_scanner.yml`
- `workflow_templates/full_scanner.yml` — copy to `.github/workflows/` to enable the full-EQ scan (needs `workflows` permission to commit)
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
