# Dhan live CPR tracker (sub-project)

Standalone live CPR board using [DhanHQ](https://dhanhq.co/docs/v2/) market data. It does not change the Shah console (8501), breakout screener (8502), or EOD site (8503).

**Market data only. This app never places orders.**

## Setup

1. Create a DhanHQ access token (Dhan web → profile → DhanHQ APIs). Data API access is required.
2. Copy `dhan_live/dhan_credentials.example` to `dhan_live/dhan_credentials.env` and put your client ID and access token there. That file is gitignored — do not commit it.

3. From the repo root (same venv as the other apps):

```bash
streamlit run dhan_live/app.py --server.port 8505
```

## How it works

1. **Load previous session** (once per day): Dhan daily candles → yesterday’s H/L/C → today’s CPR. Cached under `dhan_live/.cache/`.
2. **Refresh live quotes**: Dhan OHLC snapshot (up to 1000 names, 1 request/sec) → LTP vs CPR Top/Bottom, virgin CPR, TC/BC crosses.

Use a small universe first (Nifty 50). Nifty 500 previous-session load is one historical call per name.
