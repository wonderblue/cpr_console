"""
CPR Chart Plotter with TradingView Integration
Usage:
    python plot_cpr_charts.py --dashboard
    python plot_cpr_charts.py --symbol WELCORP
"""

import argparse
import pandas as pd
import plotly.graph_objects as go
import webbrowser
import os

def plot_stock_cpr(row):
    sym = row['SYMBOL']
    name = row['NAME']
    ltp = row['LTP']
    aug_close = row['AUG_CLOSE']
    pivot = row['Pivot']
    cpr_top = row['CPR_Top']
    cpr_bot = row['CPR_Bottom']
    chg = row['DAY_CHG_PCT']
    
    fig = go.Figure()
    
    # CPR Horizontal Level Bands
    fig.add_hline(y=cpr_top, line_dash="dash", line_color="#22c55e", annotation_text=f"CPR Top (TC): ₹{cpr_top}", annotation_position="top right")
    fig.add_hline(y=pivot, line_dash="solid", line_color="#f59e0b", annotation_text=f"Pivot: ₹{pivot}", annotation_position="right")
    fig.add_hline(y=cpr_bot, line_dash="dash", line_color="#ef4444", annotation_text=f"CPR Bottom (BC): ₹{cpr_bot}", annotation_position="bottom right")
    
    # Reference and LTP points
    fig.add_trace(go.Scatter(
        x=['Aug Close', 'Sep 1 LTP'],
        y=[aug_close, ltp],
        mode='lines+markers+text',
        name='Price Action',
        text=[f'₹{aug_close}', f'₹{ltp} ({chg:+}%)'],
        textposition=['top left', 'top right'],
        line=dict(color='#3b82f6', width=3),
        marker=dict(size=12, color=['#60a5fa', '#22c55e' if chg >= 0 else '#ef4444'])
    ))
    
    fig.update_layout(
        title=f"<b>{sym}</b> — {name} | Monthly CPR (Sep 2026)<br><sup>LTP: ₹{ltp} ({chg:+}% on Sep 1) · Score: {row['UNIFIED_SCORE']} | {row['CATEGORY']}</sup>",
        xaxis_title="Timeline",
        yaxis_title="Price (INR)",
        template="plotly_dark",
        height=500
    )
    return fig

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plot CPR Charts")
    parser.add_argument("--symbol", type=str, help="Stock Symbol (e.g. NETWEB, WELCORP)")
    parser.add_argument("--dashboard", action="store_true", help="Open TradingView Dashboard in browser")
    args = parser.parse_args()
    
    df = pd.read_csv("top_100_cpr_watchlist_Sep2026.csv")
    
    if args.symbol:
        match = df[df['SYMBOL'] == args.symbol.upper()]
        if not match.empty:
            fig = plot_stock_cpr(match.iloc[0])
            fig.show()
        else:
            print(f"Symbol {args.symbol} not found in watchlist.")
    else:
        print("Opening interactive TradingView Dashboard...")
        webbrowser.open("file://" + os.path.abspath("cpr_tradingview_dashboard.html"))
