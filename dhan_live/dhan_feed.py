"""
DhanHQ REST helpers for the live CPR tracker.

Does not place orders. Market data only.
Instrument master: https://images.dhan.co/api-data/api-scrip-master.csv
Quotes: POST /v2/marketfeed/ohlc  (max 1000, 1 req/sec)
Daily bars: POST /v2/charts/historical
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd
import requests
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
API_BASE = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
QUOTE_BATCH = 1000
CACHE_DIR_NAME = ".cache"


class DhanError(Exception):
    pass


def _ts_to_ist(ts) -> datetime:
    value = float(ts)
    if value > 1e12:
        value /= 1000.0
    return datetime.fromtimestamp(value, tz=IST)


def unwrap_chart(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if isinstance(data, dict) and "open" in data:
        return data
    return payload if "open" in payload else {}


class DhanFeed:
    def __init__(
        self,
        client_id: str,
        access_token: str,
        cache_dir: Optional[Path] = None,
        session: Optional[requests.Session] = None,
    ):
        if not client_id or not access_token:
            raise DhanError("DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are required")
        self.client_id = str(client_id).strip()
        self.access_token = str(access_token).strip()
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parent / CACHE_DIR_NAME
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self._master: Optional[pd.DataFrame] = None
        self._id_map: Dict[str, Dict[str, str]] = {}

    def _headers(self) -> dict:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        url = f"{API_BASE}{path}"
        response = self.session.post(url, headers=self._headers(), json=body, timeout=30)
        if response.status_code == 429:
            time.sleep(1.2)
            response = self.session.post(url, headers=self._headers(), json=body, timeout=30)
        if response.status_code >= 400:
            msg = response.text[:200].replace(self.access_token, "[REDACTED]") if self.access_token else response.text[:200]
            raise DhanError(f"Dhan API request {path} failed with HTTP {response.status_code}: {msg}")
        payload = response.json()
        if isinstance(payload, dict) and str(payload.get("status", "")).lower() == "failure":
            err_msg = str(payload.get("remarks") or payload.get("message") or "API call returned failure status")
            if self.access_token:
                err_msg = err_msg.replace(self.access_token, "[REDACTED]")
            raise DhanError(err_msg)
        return payload

    def load_master(self, force: bool = False) -> pd.DataFrame:
        cache_path = self.cache_dir / "scrip_master.csv"
        stale = True
        if cache_path.exists() and not force:
            age_h = (time.time() - cache_path.stat().st_mtime) / 3600
            stale = age_h > 12
        if stale:
            response = self.session.get(SCRIP_MASTER_URL, timeout=60)
            response.raise_for_status()
            cache_path.write_bytes(response.content)
        self._master = pd.read_csv(cache_path, low_memory=False)
        self._master.columns = [str(c).strip() for c in self._master.columns]
        self._rebuild_id_map()
        return self._master

    def _rebuild_id_map(self) -> None:
        df = self._master
        if df is None or df.empty:
            self._id_map = {}
            return
        needed = {"SEM_EXM_EXCH_ID", "SEM_INSTRUMENT_NAME", "SEM_TRADING_SYMBOL", "SEM_SMST_SECURITY_ID"}
        if not needed.issubset(set(df.columns)):
            raise DhanError(f"Unexpected scrip master columns: {list(df.columns)[:12]}")
        eq = df[
            (df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE")
            & (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "EQUITY")
        ].copy()
        if "SEM_SERIES" in eq.columns:
            series = eq["SEM_SERIES"].astype(str).str.upper()
            eq["_rank"] = series.map({"EQ": 0, "BE": 1, "BZ": 2}).fillna(9)
            eq = eq.sort_values("_rank")
        eq["SEM_TRADING_SYMBOL"] = eq["SEM_TRADING_SYMBOL"].astype(str).str.strip().str.upper()
        eq = eq.drop_duplicates("SEM_TRADING_SYMBOL", keep="first")
        mapping = {}
        for row in eq.itertuples(index=False):
            symbol = str(getattr(row, "SEM_TRADING_SYMBOL"))
            sid = str(int(float(getattr(row, "SEM_SMST_SECURITY_ID"))))
            name = ""
            if hasattr(row, "SM_SYMBOL_NAME"):
                name = str(getattr(row, "SM_SYMBOL_NAME") or "")
            elif hasattr(row, "SEM_CUSTOM_SYMBOL"):
                name = str(getattr(row, "SEM_CUSTOM_SYMBOL") or "")
            mapping[symbol] = {"security_id": sid, "name": name, "segment": "NSE_EQ"}
        self._id_map = mapping

    def resolve(self, symbol: str) -> Optional[Dict[str, str]]:
        if self._master is None:
            self.load_master()
        bare = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        return self._id_map.get(bare)

    def historical_daily(self, security_id: str, from_date: str, to_date: str) -> pd.DataFrame:
        payload = self._post(
            "/charts/historical",
            {
                "securityId": str(security_id),
                "exchangeSegment": "NSE_EQ",
                "instrument": "EQUITY",
                "expiryCode": 0,
                "oi": False,
                "fromDate": from_date,
                "toDate": to_date,
            },
        )
        data = unwrap_chart(payload)
        if not data or "timestamp" not in data:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(
            {
                "open": data.get("open", []),
                "high": data.get("high", []),
                "low": data.get("low", []),
                "close": data.get("close", []),
                "volume": data.get("volume", []),
                "timestamp": data.get("timestamp", []),
            }
        )
        if frame.empty:
            return frame
        idx = [_ts_to_ist(ts) for ts in frame["timestamp"]]
        frame.index = pd.DatetimeIndex(idx)
        return frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna(subset=["close"])

    def previous_session(self, security_id: str, now: Optional[datetime] = None) -> Optional[dict]:
        now = now or datetime.now(IST)
        today = now.astimezone(IST).date()
        to_date = (today + timedelta(days=1)).isoformat()
        from_date = (today - timedelta(days=40)).isoformat()
        daily = self.historical_daily(security_id, from_date, to_date)
        if daily.empty:
            return None
        completed = daily[daily.index.date < today]
        if completed.empty:
            return None
        row = completed.iloc[-1]
        return {
            "date": completed.index[-1].date().isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]) if pd.notna(row.get("volume")) else None,
        }

    def load_prev_sessions(
        self,
        symbols: Iterable[str],
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, dict]:
        today = datetime.now(IST).date().isoformat()
        cache_path = self.cache_dir / f"prev_session_{today}.json"
        cached: Dict[str, dict] = {}
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = {}

        to_fetch = []
        for symbol in symbols:
            info = self.resolve(symbol)
            if not info:
                continue
            bare = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
            if bare not in cached:
                to_fetch.append((bare, info))

        total = len(to_fetch)
        for i, (bare, info) in enumerate(to_fetch, start=1):
            if progress:
                progress(i, total, bare)
            try:
                prev = self.previous_session(info["security_id"])
            except DhanError:
                prev = None
            if prev:
                cached[bare] = {**info, **prev, "symbol": bare}
            time.sleep(0.12)

        cache_path.write_text(json.dumps(cached), encoding="utf-8")
        return cached

    def live_ohlc(self, security_ids: List[str]) -> Dict[str, dict]:
        ids = [str(int(float(sid))) for sid in security_ids if sid]
        out: Dict[str, dict] = {}
        for i in range(0, len(ids), QUOTE_BATCH):
            chunk = [int(sid) for sid in ids[i : i + QUOTE_BATCH]]
            payload = self._post("/marketfeed/ohlc", {"NSE_EQ": chunk})
            data = payload.get("data", payload)
            nse = data.get("NSE_EQ", {}) if isinstance(data, dict) else {}
            for sid, quote in nse.items():
                ohlc = quote.get("ohlc") or {}
                out[str(sid)] = {
                    "ltp": quote.get("last_price"),
                    "open": ohlc.get("open"),
                    "high": ohlc.get("high"),
                    "low": ohlc.get("low"),
                    "close": ohlc.get("close"),
                }
            if i + QUOTE_BATCH < len(ids):
                time.sleep(1.05)
        return out
