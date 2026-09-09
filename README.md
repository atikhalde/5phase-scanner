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
- **Telegram:** Requires GitHub Secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

### Shakeout / Pullback alert — verified status (2026-09-09)

`tools/verify_shakeout_alert.py` replays the alert **bar by bar** over charts
reconstructed from the real 1291-trade backtest (`sample_1291_trades.csv`, real
anchor / breakout / rally / shake prices, volumes and bar gaps). Recall on a
300-trade sample (alert fires on the actual Phase 4b shakeout day):

| | recall | main rejection reason |
|---|---|---|
| as shipped | 20.7% | 20.7% breakout invisible to the detector |
| + 8-bar blind-spot fix | 27.7% | 41.7% "breakdown", 29.7% level not reached |
| + 12% wick tolerance | **64.0%** | 29.7% level not reached (by design) |

Two bugs found and fixed:

1. **8-bar blind spot.** `detect_pending_breakouts()` stopped at
   `max_i = n - 8`, i.e. it refused to look at any breakout younger than 8
   bars. A fresh breakout could therefore not fire the shakeout alert *and*
   not reach the 30d/60d watchlist for its first ~11 calendar days. Replaying
   the backtest, **20.7% of real shakeouts happen within 7 bars of the
   breakout** (as few as 2) and could never alert. `_check_breakout_confirmed()`
   already tolerates a partial rally window (`min(i + 8, n)`), so the guard is
   now `max_i = n - 1`. Regression tests: `test_young_breakout_*`.
2. **Wick tolerance was 2%, far too tight for this strategy.** The alert demands
   `anchor_high * (1 - wick) <= low <= breakout_close`. The old 2% discarded
   **42% of the strategy's own shakeouts** as "breakdowns", although real
   Phase 4b lows routinely pierce the anchor (low vs anchor: p25 = −5.2%,
   p10 = −9.2%). Real example that was silently dropped: **RAJRATAN.NS
   2026-08-25** — low 483.05 vs breakout close 540.05 (−10.6%, Vol 0.47x,
   −14.35% off the rally high) — which went on to fire a reversal entry at
   516.0 on 2026-09-09. Default is now **12%** (`SHAKEOUT_WICK_PCT`), which
   keeps 93% of the shakeouts that actually reach the level:

   | wick | 2% (old) | 5% | 8% | 10% | **12%** | 15% |
   |---|---|---|---|---|---|---|
   | backtest shakeouts alerted | 29.0% | 44.6% | 57.0% | 62.7% | **66.3%** | 69.2% |
   | …of those that touch the level | 40.7% | 62.7% | 80.2% | 88.1% | **93.2%** | 97.3% |

   Tune it per environment without touching code:
   `SHAKEOUT_WICK_PCT=8` (percent below the anchor). The zone **top** is
   unchanged — a low that stays above the breakout close never alerts, which
   is the DCBBANK/FMGOETZE false-positive guard; that is the remaining 29.7%
   of misses and it is intentional.

```bash
python tools/verify_shakeout_alert.py --limit 300                  # backtest geometry
python tools/verify_shakeout_alert.py --mode bars --bars-dir data  # real OHLCV CSVs
```

> ⚠️ Known ops issue (left as-is by request): `daily_scanner.yml` runs every
> 15 minutes with no de-duplication across runs, so an alert that fires on
> today's bar is re-sent on **every** run that day (up to ~25 copies), and
> during market hours it can fire off a *partial* intraday bar.

### 2. Full NSE EQ Scanner (`full_scanner.py`) — NEW
> ⚙️ **Activate the workflow:** the CI file is provided at `workflow_templates/full_scanner.yml`. Copy it to `.github/workflows/full_scanner.yml` and commit (this must be done by a user/token with the GitHub `workflows` permission — the automated agent cannot create workflow files). Once committed it runs on the schedule below. You can also just run `python full_scanner.py` anytime.

- **Schedule:** Once per trading day at EOD 15:45 IST (10:15 UTC Mon-Fri) + manual dispatch
- **Universe:** FULL NSE cash equity (~2000 symbols, `SERIES == EQ` from NSE `EQUITY_L.csv`) — NOT just Nifty500
- **Why:** `daily_scanner.py` only scans Nifty500, so stocks outside that index (e.g. **TVSSRICHAK** / TVS Srichakra) are never fetched and can never fire an alert — even though the full-universe backtest produced trades for them. This scanner covers them.
- **Logic:** *Identical* 5-phase engine as the daily scanner. `full_scanner.py` reuses `scanner.py`, the data-fetch/freshness helpers from `daily_scanner.py`, and the formatters from `telegram_helper.py` — nothing in the existing files is modified.
- **Alerts:** four — Watchlist (breakout waiting shakeout/reversal), Breakout Today (Phase3), Shakeout / Pullback Today (Phase4b), Reversal Entry Today (Phase5)
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

## Files
- `scanner.py` — exact same conditions
- `daily_scanner.py` — daily watchlist + live alerts (Nifty500)
- `full_scanner.py` — same logic, FULL NSE EQ (~2000 symbols); standalone, reuses `scanner.py`/`daily_scanner.py`/`telegram_helper.py` without modifying them
- `telegram_helper.py` — Telegram sender
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
