# HEG — Daily Timeframe Deep Analysis
**Window: 29/04/2025 → 14/08/2026, with special focus on 07/09/2026**
*Analyzed 09/09/2026 · Data: NSE daily bars Aug-2024 → Sep-2026 (522 trading bars)*

## 0. Data & method (read this first — it matters)
- Full daily OHLCV pulled from Yahoo Finance (NSE flap) in 9 contiguous blocks and
  independently validated: monotonic timestamps, NSE weekday calendar, OHLC logic,
  block-boundary continuity (all overnight gaps < 1% except one −3.1% weekend gap).
- Prices are **split-adjusted** (1:5 split, ex/record 18-Oct-2024) and **dividend-adjusted
  yfinance-style** (₹1.80 ex-13-Aug-2025, ₹3.40 ex-22-Jul-2026). Adjustment verified to the
  paisa against Yahoo adjclose (440.43 / 580.27 / 665.25 spot-checks ✅).
- The 5-phase engine (`scanner.py`) was **proven working** on a synthetic planted pattern
  (1 trade, correct breakout/reversal/entry) before judging HEG — so "0 trades" below is a
  genuine verdict, not a bug.
- **Regime break:** 07/09/2026 is the graphite-business **demerger ex-date**. All scanner
  work stops at 04/09/2026 (last cum-trading day). Pre/post prices are different companies
  and must never be mixed (no ratio-adjusting across a demerger).

## 1. Window verdict (29/04/2025 → 14/08/2026, 322 bars)
| Metric | Value |
|---|---|
| Start → End (close) | 472.25 → 701.00 = **+48.4%** |
| Window high / low | 711.2 (14/08/26) / 416.05 intraday (09/05/25) |
| Max drawdown (closes) | **−25.1%** |
| Avg volume | ~20.2 lakh/day; max 473 lakh (27/03/26) |
| Green / Red days | 145 / 177 (uptrend carried by a few explosions) |
| Time above EMA50 | 208/322 bars (65%), ended +14.2% above a +11.2%/20d rising EMA50 |

Character: **news-driven, high-beta cyclical** — long bleeds punctuated by violent
result/event-day volume explosions (9 days with VolRatio > 6.6x in the window). It trends
*in legs*, not in bases — which is exactly why the 5-phase pattern never completes here.

## 2. Swing map of the window (major legs, raw prices)
```
19/05/25 H 550.9 (14.3M) ──► 19/06/25 L 481.0 (−12.7%)
 ──► 31/07/25 H 619.0 (46.7M) (+28.7%) ──► 29/08/25 L 459.8 (−25.7%)
 ──► 09/10/25 H 551.2 (+19.9%) ──► 20/10/25 L 504.1 (−8.5%)
 ──► 03/11/25 H 606.5 (+20.3%) ──► 24/11/25 L 490.9 (−19.1%)
 ──► 31/12/25 H 672.0 (25.5M, Phase-3★) (+36.9%) ──► 02/02/26 L 506.0 (−24.7%)
 ──► 11/02/26 H 600.0 (+18.6%, intraday −11% reversal) ──► 23/03/26 L 470.8 (−21.5%)
 ──► 29/04/26 H 690.0 (+46.6% off 47.3M base breakout 27/03) ──► 04/05/26 L 578.2 (−16.2%)
 ──► 15/05/26 H 637.5 (+10.2%) ──► 11/06/26 L 505.0 (−20.8%)
 ──► 18/06/26 H 552.9 (+9.5%) ──► 09/07/26 L 509.1 (−7.9%)
 ──► 23/07/26 H 675.9 (18.8M) (+32.8%) ──► drift/consolidation ──► 14/08/26 C 701.0 (window top)
```
Every −20/−25% drawdown was followed by a V-shaped volume explosion — textbook
climax-reversal trading stock, not a base-breakout stock.

## 3. 5-phase scanner verdict: **NO TRADE — and the engine is right**
- **Completed trades in dataset: 0. Pending/watchlist as of 04/09/26: 0.**
- Exactly **ONE Phase-3 bar** in 2 years of scannable history:
  **31/12/2025** — close 623.85 > anchor 619.0 (31/07/25 high), 9.31x volume, 103 days
  after anchor. It then **failed Phase-4a**: breakout high was 672.0 (needed > 678.7
  within 7 days; best was 644.9). The bar itself faded −7.2% from high intraday — a
  **buying climax**, precisely what the 4a rally filter exists to reject (ABDL-class).
- **Why nothing else fired:**
  - Mar-25 / Jul-25 / Apr-26 explosions never closed above their 90–180d anchors
    (Nov-24's 619.5 / Jan-26's 672 capped them).
  - The entire Aug-26 run-up (closes 701 → 739) is **extension, not breakout**: the
    Dec-25 anchor had already been closed-above on 29/07/26, voiding Phase-1; late-Aug
    bars also failed volume (0.2–1.0x on a spike-inflated VolMA20). The scanner correctly
    refuses to chase a news-driven extension into a corporate action.
  - 04/09/26 (last cum day): close 728.25 > anchor, but VolRatio **1.45x < 1.50x** —
    misses by 0.05x, and no 4a window exists (demerger next trading day).
- Structural note: HEG's Dry90 never drops below 21 (avg 39) — the giant event-day
  volumes inflate VolMA20 so ordinary days always look "dry". Phase-2 is therefore
  non-binding on this ticker; Phases 1/3/4a do all the filtering work.

## 4. ★ 07/09/2026 — demerger ex-date (the "especially" day)
**Candle: O 260.0 · H 273.0 · L 250.0 · C 272.20 · V 3.62M (Monday)**
- **What happened:** HEG's graphite-electrodes business demerged 1:1 into HEG Graphite Ltd
  (to be renamed HEG Ltd; listing expected H2-Oct-2026). Record date = ex-date = 07/09/26
  (T+1). The listed entity (renamed **HEG Advanced Materials** w.e.f. 02/09/26) keeps
  advanced materials + battery solutions + green power, and separately absorbs Bhilwara
  Energy (8-for-7 share swap). [NDTV Profit](https://www.ndtvprofit.com/markets/heg-demerger-record-date-today-what-it-means-for-shareholders-all-you-need-to-know-12011349)
- **The −62.6% is a value split, NOT a crash:** Fri cum-close 728.25 → Mon ex-close 272.20.
  Implied market split: **graphite ≈ ₹456 (62.6%)**, residual ≈ ₹272 (37.4%).
- **Intraday anatomy (bullish absorption):** opened −64.3% at 260, flushed to **250.0 =
  new 52-week low**, then ripped +8.9% off the lows to close at 272.2, 99.6% of the day's
  high — a near-marubozu close (+4.7% body) on the 50th-biggest volume in 2 years.
  Ex-date weak hands were absorbed, not panicked out.
- **Context:** 52-week high 753.9 printed 02/09/26 (2 sessions before ex-date) — classic
  pre-event run-up; the "breakdown" under old supports (690/672/650) is meaningless.
- **Next two days:** Tue 08/09 — all-down bar O=H=265.4 → L=C=258.6 (−5.0%, thin 1.33M,
  digestion); Wed 09/09 — O 257 / H 271.5 / L 255 / C 262.75 (+1.6%, 3.29M, long lower
  wick defending ~255). A 250–273 embryonic base is forming.

## 5. Levels: old ones are dead, new ones are being born
- ❌ Dead (pre-demerger company): 753.9 / 728 / 690 / 672 / 650 / 619 — never use for the
  ex-stock. Same for the old EMA50/VolMA (they span the break).
- ✅ Live (HEG Advanced Materials, 3 bars): support **250–255** (ex-day low + wick cluster),
  pivot **258–260** (Tue close / Mon open gap-test), resistance **271.5–273** (ex-day high).
  A close above 273 with expanding volume = first genuine post-demerger breakout signal.
- ⚠️ Overhang: HEG Graphite lists ~mid/late-Oct-2026; combined-value arbitrage + BEL-swap
  paper can keep the residual choppy until then.

## 6. What I'd watch (scanner operator's view)
1. Let the base build — the 5-phase engine needs ~180 post-demerger bars (~9 months) before
   its anchors mean anything; until then, trade price/volume levels above, not the model.
2. Signals that matter now: holds above 250–255 on drying volume → then a >1.5x-volume
   close above 273 = first valid impulse of the new entity.
3. Invalidation: a daily close under 250 with volume = ex-day low fails, air below.
4. Do NOT backfill pre-Sep-07 prices into any indicator for this ticker — every moving
   average/volume mean crossing 07/09/26 is contaminated.

## 7. Bottom line
HEG +48% across your window with zero 5-phase trades is not a scanner miss — it's the
scanner working: every rally was a V-climax or news extension, and the lone Phase-3
(31/12/25) was correctly killed by the rally-continuation filter at the top. The real
event is structural: **07/09/26 converted HEG into a new company at a new price**, the
market priced graphite at ~63% / residual at ~37%, and ex-day price action (low 250 →
close 272.2 near highs) shows absorption, not distress. Treat everything before 07/09/26
as history of a different security.
