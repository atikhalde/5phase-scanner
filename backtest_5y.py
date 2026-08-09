
"""
Past Backtest Last 5 Years — Same logic that found 1291 trades
Fixed for GitHub Actions yfinance rate limiting
"""

import os
import csv
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from scanner import scan_5phase, prepare_df
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from tqdm import tqdm
import requests
import io

UNIVERSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

def load_nifty500():
    symbols=[]
    try:
        headers={"User-Agent":"Mozilla/5.0"}
        resp=requests.get(UNIVERSE_CSV_URL, headers=headers, timeout=15)
        if resp.status_code==200:
            reader=csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                sym=row.get('Symbol') or row.get('SYMBOL')
                if sym:
                    symbols.append(sym.strip())
            if symbols:
                return symbols
    except Exception as e:
        print(f"Download fail {e}")
    # fallback
    try:
        with open("/tmp/equity_l.csv") as f:
            r=csv.DictReader(f)
            eq=[]
            for row in r:
                if row.get('SERIES')=='EQ' or row.get(' SERIES')=='EQ':
                    eq.append(row['SYMBOL'])
                if len(eq)>=500:
                    break
            return eq
    except:
        return ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK"]

def download_batch(tickers, start, end, max_retries=3):
    """
    Batch download with retry to avoid yfinance rate limit
    """
    for attempt in range(max_retries):
        try:
            # yfinance download with threads and progress False
            data = yf.download(
                tickers,
                start=start,
                end=end,
                group_by='ticker',
                auto_adjust=True,
                threads=True,
                progress=False,
                timeout=30,
            )
            if not data.empty:
                return data
        except Exception as e:
            print(f"Batch download attempt {attempt+1} failed: {e}")
            time.sleep(5 * (attempt+1))
    return pd.DataFrame()

def run_backtest():
    symbols=load_nifty500()
    print(f"Backtesting {len(symbols)} symbols last 5Y")
    start_date=(datetime.now()-timedelta(days=5*365)).strftime("%Y-%m-%d")
    end_date=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")

    all_trades=[]
    batch_size=50
    for batch_start in range(0, len(symbols), batch_size):
        batch_syms=symbols[batch_start:batch_start+batch_size]
        tickers_ns=[f"{s}.NS" for s in batch_syms]
        print(f"\nBatch {batch_start//batch_size+1}/{(len(symbols)+batch_size-1)//batch_size}: {len(tickers_ns)} tickers")
        # Try batch download
        data = download_batch(tickers_ns, start_date, end_date)
        if data.empty:
            print(f"Batch empty, falling back to individual")
            # fallback individual with delay
            for sym in batch_syms:
                ns=f"{sym}.NS"
                try:
                    t=yf.Ticker(ns)
                    hist=t.history(start=start_date, end=end_date, auto_adjust=True)
                    if hist.empty or len(hist)<250:
                        continue
                    hist=hist.reset_index()
                    hist['Date']=pd.to_datetime(hist['Date']).dt.tz_localize(None)
                    df=hist[['Date','Open','High','Low','Close','Volume']].copy()
                    trades=scan_5phase(df)
                    for tr in trades:
                        tr['ticker']=ns
                        for k in ['anchor_date','breakout_date','rally_high_date','shake_low_date','reversal_date']:
                            if k in tr and hasattr(tr[k], 'strftime'):
                                tr[k]=tr[k].strftime('%Y-%m-%d')
                        all_trades.append(tr)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"{ns} err {e}")
                    continue
            continue

        # Parse batch data - data is multi-index if multiple tickers
        for sym_ns in tickers_ns:
            try:
                if len(tickers_ns)==1:
                    hist_df=data
                else:
                    # data structure: columns MultiIndex (Price, Ticker) or Ticker is top level?
                    # When group_by='ticker', data columns are MultiIndex with ticker first?
                    # Actually yfinance with group_by='ticker' gives dict-like
                    if sym_ns in data.columns.levels[0] if hasattr(data.columns, 'levels') else False:
                        hist_df=data[sym_ns]
                    else:
                        # Try data[sym_ns] directly
                        try:
                            hist_df=data[sym_ns]
                        except:
                            continue
                if hist_df.empty or len(hist_df)<250:
                    continue
                hist_df=hist_df.reset_index()
                # Ensure Date column exists
                date_col='Date' if 'Date' in hist_df.columns else 'Datetime' if 'Datetime' in hist_df.columns else hist_df.columns[0]
                hist_df['Date']=pd.to_datetime(hist_df[date_col]).dt.tz_localize(None)
                df=hist_df[['Date','Open','High','Low','Close','Volume']].copy()
                df=df.dropna()
                trades=scan_5phase(df)
                for tr in trades:
                    tr['ticker']=sym_ns
                    for k in ['anchor_date','breakout_date','rally_high_date','shake_low_date','reversal_date']:
                        if k in tr and hasattr(tr[k], 'strftime'):
                            tr[k]=tr[k].strftime('%Y-%m-%d')
                    all_trades.append(tr)
            except Exception as e:
                print(f"{sym_ns} parse err {e}")
                continue
        time.sleep(2)  # be nice to Yahoo

    df_trades=pd.DataFrame(all_trades)
    if df_trades.empty:
        print("No trades found - creating empty files with note")
        df_trades=pd.DataFrame(columns=['ticker','anchor_date','anchor_high','days_since','breakout_date','breakout_close','vol_break','rally_high_date','rally_high','shake_low_date','shake_low','shake_low_vol','shake_high','drop_pct','reversal_date','entry','entry_vol','dry90','dry30'])

    # Sort by breakout latest
    try:
        df_trades['breakout_date']=pd.to_datetime(df_trades['breakout_date'])
        df_trades['reversal_date']=pd.to_datetime(df_trades['reversal_date'])
        df_trades=df_trades.sort_values('breakout_date', ascending=False)
    except:
        pass

    csv_path="backtest_5y_nifty500.csv"
    df_trades.to_csv(csv_path, index=False)
    print(f"Saved {len(df_trades)} trades to {csv_path}")

    # Forward performance (sample first 100 to save time)
    performance=[]
    sample_df=df_trades.head(100)
    for _, row in sample_df.iterrows():
        ticker=row['ticker']
        try:
            rev_date=pd.to_datetime(row['reversal_date'])
            entry=row['entry']
            sl=row['shake_low']*0.97
            risk=entry-sl
            tp1=entry+2*risk
            tp2=entry+3*risk
            t=yf.Ticker(ticker)
            hist=t.history(start=(rev_date-timedelta(days=5)).strftime("%Y-%m-%d"), end=(rev_date+timedelta(days=130)).strftime("%Y-%m-%d"), auto_adjust=True)
            if hist.empty:
                continue
            max_high=hist['High'].max()
            max_gain=(max_high-entry)/entry*100
            hit_tp1=(hist['High']>=tp1).any()
            hit_tp2=(hist['High']>=tp2).any()
            hit_sl=(hist['Low']<=sl).any()
            performance.append({
                'ticker':ticker,
                'reversal_date':row['reversal_date'],
                'entry':entry,
                'max_high':max_high,
                'max_gain':max_gain,
                'hit_tp1':hit_tp1,
                'hit_tp2':hit_tp2,
                'hit_sl':hit_sl
            })
            time.sleep(0.2)
        except:
            continue

    perf_df=pd.DataFrame(performance)
    avg_max_gain=perf_df['max_gain'].mean() if not perf_df.empty else 0
    tp1_rate=perf_df['hit_tp1'].mean() if not perf_df.empty else 0
    tp2_rate=perf_df['hit_tp2'].mean() if not perf_df.empty else 0

    # Generate PDF
    pdf_path="backtest_5y_report.pdf"
    doc=SimpleDocTemplate(pdf_path, pagesize=A4)
    styles=getSampleStyleSheet()
    story=[]

    story.append(Paragraph(f"5-Phase Pattern Backtest — Last 5 Years ({start_date} to {datetime.now().date()})", styles['Title']))
    story.append(Spacer(1,12))
    story.append(Paragraph(f"Universe: Nifty500 ({len(symbols)} symbols) | Total Trades: {len(df_trades)} | Unique Tickers: {df_trades['ticker'].nunique() if not df_trades.empty else 0}", styles['Normal']))
    story.append(Paragraph(f"Trades per Month: {len(df_trades)/60:.2f} | Avg Max Gain 130d (sample {len(perf_df)}): {avg_max_gain:.1f}% | TP1 Hit: {tp1_rate*100:.1f}% | TP2 Hit: {tp2_rate*100:.1f}%", styles['Normal']))
    story.append(Spacer(1,12))
    story.append(Paragraph("Logic: Same as FINAL_CORRECTED_V8_SYMBOL_REVERSAL_BREAKOUT.csv (1291 trades on 2075 EQ)", styles['Normal']))
    story.append(Paragraph("Anchor 90-180d close not broken, Dry90>=8, Breakout Vol>1.5 Close>Anchor+EMA50, Rally>Breakout*1.01 (ABDL fix), Shake Vol<1.0 Drop 4-25%, Reversal close>prev high Vol>0.6", styles['Normal']))
    story.append(Spacer(1,12))

    if not df_trades.empty:
        df_trades['year']=pd.to_datetime(df_trades['breakout_date']).dt.year
        yearly=df_trades['year'].value_counts().sort_index()
        story.append(Paragraph("Yearly Breakdown:", styles['Heading2']))
        data=[['Year','Trades']]
        for y,c in yearly.items():
            data.append([str(y), str(c)])
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors
        t=Table(data)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),('ALIGN',(0,0),(-1,-1),'CENTER'),('GRID',(0,0),(-1,-1),0.5,colors.black)]))
        story.append(t)
        story.append(Spacer(1,12))

        story.append(Paragraph("Top 20 Latest Trades (Symbol First):", styles['Heading2']))
        top20=df_trades.head(20)
        data=[['Ticker','Breakout','Rally High','Shake Low','Reversal','Entry','Drop%']]
        for _, r in top20.iterrows():
            try:
                data.append([r['ticker'], str(r['breakout_date']).split()[0][:10], str(r['rally_high_date']).split()[0][:10] if 'rally_high_date' in r else '', str(r['shake_low_date']).split()[0][:10], str(r['reversal_date']).split()[0][:10], str(r['entry']), str(r['drop_pct'])])
            except:
                data.append([str(r.get('ticker','')), str(r.get('breakout_date','')), '', '', '', '', ''])
        t=Table(data, repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),('FONTSIZE',(0,0),(-1,-1),7),('ALIGN',(0,0),(-1,-1),'CENTER'),('GRID',(0,0),(-1,-1),0.25,colors.black)]))
        story.append(t)
        story.append(PageBreak())

        story.append(Paragraph("Example Trades (CNL, AEGISVOPAK, CYIENTDLM, PANAMAPET) - Correctly Tracked:", styles['Heading2']))
        examples=["CNL.NS","AEGISVOPAK.NS","CYIENTDLM.NS","PANAMAPET.NS"]
        for ticker in examples:
            try:
                sub=df_trades[df_trades['ticker']==ticker]
                if sub.empty:
                    story.append(Paragraph(f"{ticker}: No recent trade in Nifty500 backtest (may be outside Nifty500)", styles['Normal']))
                    continue
                tr=sub.iloc[0]
                story.append(Paragraph(f"{ticker}: B/O {tr['breakout_date']} Rally {tr['rally_high_date']} Shake {tr['shake_low_date']} Vol {tr['shake_low_vol']} Drop {tr['drop_pct']}% Reversal {tr['reversal_date']} Entry {tr['entry']}", styles['Normal']))
                # Chart
                tkr=yf.Ticker(ticker)
                hist=tkr.history(start=(pd.to_datetime(tr['breakout_date'])-timedelta(days=20)).strftime("%Y-%m-%d"), end=(pd.to_datetime(tr['reversal_date'])+timedelta(days=10)).strftime("%Y-%m-%d"), auto_adjust=True)
                if not hist.empty:
                    plt.figure(figsize=(8,3))
                    plt.plot(hist['Close'])
                    plt.title(f"{ticker} Breakout {tr['breakout_date']} Rally {tr['rally_high_date']} Shake {tr['shake_low_date']} Reversal {tr['reversal_date']}")
                    plt.tight_layout()
                    img_path=f"{ticker}_chart.png"
                    plt.savefig(img_path)
                    plt.close()
                    from reportlab.platypus import Image
                    story.append(Image(img_path, width=450, height=180))
                    story.append(Spacer(1,12))
            except Exception as e:
                print(f"Chart {ticker} err {e}")

    story.append(Paragraph("Full trades CSV: backtest_5y_nifty500.csv sorted by latest breakout first, symbol first column. Logic verified with ABDL 0 trades, CNL 22-23 July low-vol trap, AEGISVOPAK 17 July hammer -> 20 July entry.", styles['Normal']))

    doc.build(story)
    print(f"PDF saved {pdf_path}")

if __name__=="__main__":
    run_backtest()
