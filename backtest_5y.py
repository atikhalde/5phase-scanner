"""
Past Backtest Last 5 Years — Same logic that found 1291 trades on full NSE EQ 2075
Now running on Nifty500 as per user selection (user chose Nifty500 for daily scanner)
Generates CSV + PDF with performance

PDF contains:
- Summary stats
- Yearly breakdown
- Top 20 latest trades table
- 4 example charts (CNL, AEGISVOPAK, CYIENTDLM, PANAMAPET) with structure
- Forward performance: TP1/TP2 hit rate, max gain
"""

import os
import csv
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
    # fallback 500 EQ
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

def run_backtest():
    symbols=load_nifty500()
    print(f"Backtesting {len(symbols)} symbols last 5Y")
    start_date=datetime.now()-timedelta(days=5*365)
    end_date=datetime.now()

    all_trades=[]
    for idx, sym in enumerate(tqdm(symbols)):
        ns=f"{sym}.NS"
        try:
            t=yf.Ticker(ns)
            hist=t.history(start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), auto_adjust=True)
            if hist.empty or len(hist)<250:
                continue
            hist=hist.reset_index()
            hist['Date']=pd.to_datetime(hist['Date']).dt.tz_localize(None)
            df=hist[['Date','Open','High','Low','Close','Volume']].copy()
            trades=scan_5phase(df)
            for tr in trades:
                tr['ticker']=ns
                # Convert dates to string for CSV
                for k in ['anchor_date','breakout_date','rally_high_date','shake_low_date','reversal_date']:
                    if k in tr and hasattr(tr[k], 'strftime'):
                        tr[k]=tr[k].strftime('%Y-%m-%d')
                all_trades.append(tr)
        except Exception as e:
            print(f"{ns} err {e}")
            continue

    df_trades=pd.DataFrame(all_trades)
    if df_trades.empty:
        print("No trades found")
        return

    # Sort by breakout latest
    df_trades['breakout_date']=pd.to_datetime(df_trades['breakout_date'])
    df_trades['reversal_date']=pd.to_datetime(df_trades['reversal_date'])
    df_trades=df_trades.sort_values('breakout_date', ascending=False)

    # Save CSV
    csv_path="backtest_5y_nifty500.csv"
    df_trades.to_csv(csv_path, index=False)
    print(f"Saved {len(df_trades)} trades to {csv_path}")

    # Forward performance
    performance=[]
    for _, row in df_trades.iterrows():
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

    story.append(Paragraph(f"5-Phase Pattern Backtest — Last 5 Years ({start_date.date()} to {end_date.date()})", styles['Title']))
    story.append(Spacer(1,12))
    story.append(Paragraph(f"Universe: Nifty500 ({len(symbols)} symbols) | Total Trades: {len(df_trades)} | Unique Tickers: {df_trades['ticker'].nunique()}", styles['Normal']))
    story.append(Paragraph(f"Trades per Month: {len(df_trades)/60:.2f} | Avg Max Gain 130d: {avg_max_gain:.1f}% | TP1 Hit: {tp1_rate*100:.1f}% | TP2 Hit: {tp2_rate*100:.1f}%", styles['Normal']))
    story.append(Spacer(1,12))

    # Yearly breakdown
    df_trades['year']=df_trades['breakout_date'].dt.year
    yearly=df_trades['year'].value_counts().sort_index()
    story.append(Paragraph("Yearly Breakdown:", styles['Heading2']))
    data=[['Year','Trades']]
    for y,c in yearly.items():
        data.append([str(y), str(c)])
    t=Table(data)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),('ALIGN',(0,0),(-1,-1),'CENTER'),('GRID',(0,0),(-1,-1),0.5,colors.black)]))
    story.append(t)
    story.append(Spacer(1,12))

    # Top 20 latest
    story.append(Paragraph("Top 20 Latest Trades (Symbol First):", styles['Heading2']))
    top20=df_trades.head(20)
    data=[['Ticker','Breakout','Rally High','Shake Low','Reversal','Entry','Drop%']]
    for _, r in top20.iterrows():
        data.append([r['ticker'], r['breakout_date'].strftime('%Y-%m-%d'), r['rally_high_date'].strftime('%Y-%m-%d') if hasattr(r['rally_high_date'],'strftime') else str(r['rally_high_date']), r['shake_low_date'].strftime('%Y-%m-%d') if hasattr(r['shake_low_date'],'strftime') else str(r['shake_low_date']), r['reversal_date'].strftime('%Y-%m-%d'), str(r['entry']), str(r['drop_pct'])])
    t=Table(data, repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),('FONTSIZE',(0,0),(-1,-1),7),('ALIGN',(0,0),(-1,-1),'CENTER'),('GRID',(0,0),(-1,-1),0.25,colors.black)]))
    story.append(t)
    story.append(PageBreak())

    # Example charts
    story.append(Paragraph("Example Trades with Correct Structure (CNL, AEGISVOPAK, CYIENTDLM, PANAMAPET):", styles['Heading2']))
    examples=["CNL.NS","AEGISVOPAK.NS","CYIENTDLM.NS","PANAMAPET.NS"]
    for ticker in examples:
        try:
            sub=df_trades[df_trades['ticker']==ticker]
            if sub.empty:
                continue
            tr=sub.iloc[0]
            t=yf.Ticker(ticker)
            hist=t.history(start=(pd.to_datetime(tr['breakout_date'])-timedelta(days=20)).strftime("%Y-%m-%d"), end=(pd.to_datetime(tr['reversal_date'])+timedelta(days=10)).strftime("%Y-%m-%d"), auto_adjust=True)
            if hist.empty:
                continue
            plt.figure(figsize=(8,3))
            plt.plot(hist['Close'])
            plt.title(f"{ticker} Breakout {tr['breakout_date'].strftime('%Y-%m-%d')} Rally {tr['rally_high_date'].strftime('%Y-%m-%d')} Shake {tr['shake_low_date'].strftime('%Y-%m-%d')} Reversal {tr['reversal_date'].strftime('%Y-%m-%d')}")
            plt.tight_layout()
            img_path=f"{ticker}_chart.png"
            plt.savefig(img_path)
            plt.close()
            story.append(Image(img_path, width=450, height=180))
            story.append(Spacer(1,12))
        except Exception as e:
            print(f"Chart {ticker} err {e}")

    story.append(Paragraph("Full trades CSV: backtest_5y_nifty500.csv sorted by latest breakout first, symbol first column", styles['Normal']))

    doc.build(story)
    print(f"PDF saved {pdf_path}")

if __name__=="__main__":
    run_backtest()
