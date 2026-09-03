"""
Apache Parquet columnar storage and DuckDB analytical query layer for CPR data.

Provides:
- Partitioned Parquet storage: cpr_output/parquet/year=YYYY/month=MM/cpr_full_YYYYMMDD.parquet
- Zero-server fast analytical queries via embedded DuckDB
- Backward-compatible lookback computation and one-time CSV-to-Parquet backfill
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Union

try:
    import duckdb
except ImportError:
    duckdb = None
import pandas as pd

DEFAULT_OUTPUT_DIR = Path("cpr_output")
PARQUET_SUBDIR = "parquet"


def parquet_partition_dir(date: str, output_dir: Optional[Union[str, Path]] = None) -> Path:
    """Return cpr_output/parquet/year=YYYY/month=MM for a YYYYMMDD date."""
    root = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    if len(date) != 8 or not date.isdigit():
        raise ValueError(f"Date must be YYYYMMDD, got {date!r}")
    year, month = date[:4], date[4:6]
    return root / PARQUET_SUBDIR / f"year={year}" / f"month={month}"


def parquet_session_path(date: str, output_dir: Optional[Union[str, Path]] = None) -> Path:
    """Canonical path for a session Parquet file."""
    return parquet_partition_dir(date, output_dir) / f"cpr_full_{date}.parquet"


def save_session_parquet(
    df: pd.DataFrame,
    date: str,
    output_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Save a full CPR scan dataframe as a compressed Parquet partition."""
    if df is None or df.empty:
        raise ValueError("Cannot save empty dataframe to Parquet")
    path = parquet_session_path(date, output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    out_df = df.copy()
    if "Date" not in out_df.columns:
        out_df["Date"] = date

    # Ensure clean string representation for symbol and industry
    for col in ("SYMBOL", "NAME", "Industry", "Segment", "CPR_Class", "Bias", "Overlay", "Setup", "Price_Position"):
        if col in out_df.columns:
            out_df[col] = out_df[col].astype(str).fillna("")

    # Convert numeric columns where applicable
    for col in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE", "Pivot", "BC", "TC", "CPR_Width_Pct", "Width_Rank_Pct", "Confluence_Score", "Signal_Score", "Value_60d"):
        if col in out_df.columns:
            out_df[col] = pd.to_numeric(out_df[col], errors="coerce")

    # Use PyArrow engine with snappy compression
    out_df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
    return path


def load_session_parquet(
    date: str,
    output_dir: Optional[Union[str, Path]] = None,
) -> Optional[pd.DataFrame]:
    """Load a session dataframe from Parquet if present."""
    path = parquet_session_path(date, output_dir)
    if not path.exists():
        return None
    return pd.read_parquet(path, engine="pyarrow")


def get_parquet_glob(output_dir: Optional[Union[str, Path]] = None) -> str:
    """Return the glob pattern for all Parquet files in the lakehouse."""
    root = Path(output_dir).resolve() if output_dir is not None else DEFAULT_OUTPUT_DIR.resolve()
    parquet_root = root / PARQUET_SUBDIR
    return str(parquet_root / "**" / "*.parquet").replace("'", "''")


def query_duckdb(
    sql: str,
    output_dir: Optional[Union[str, Path]] = None,
    params: Optional[dict] = None,
) -> pd.DataFrame:
    """Execute a SQL query against the Parquet Lakehouse using DuckDB."""
    if duckdb is None:
        raise RuntimeError("duckdb is not installed")
    glob_path = get_parquet_glob(output_dir)
    sql_formatted = sql.replace("__PARQUET__", f"read_parquet('{glob_path}', hive_partitioning=1)")
    con = duckdb.connect(":memory:")
    try:
        if params:
            return con.execute(sql_formatted, params).df()
        return con.execute(sql_formatted).df()
    finally:
        con.close()


def compute_own_narrow_duckdb(
    current_df: pd.DataFrame,
    as_of_date: str,
    lookback_sessions: int = 60,
    output_dir: Optional[Union[str, Path]] = None,
) -> Optional[pd.DataFrame]:
    """
    Compute 60-day historical narrow rank and Own_Narrow flag using DuckDB.
    Returns None if insufficient Parquet history is available to trigger fallback.
    """
    if duckdb is None:
        return None
    root = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    parquet_root = root / PARQUET_SUBDIR
    if not parquet_root.exists():
        return None

    glob_path = get_parquet_glob(output_dir)
    con = duckdb.connect(":memory:")
    try:
        # Check available dates strictly before as_of_date
        distinct_dates_df = con.execute(
            f"""
            SELECT DISTINCT Date 
            FROM read_parquet('{glob_path}', hive_partitioning=1) 
            WHERE Date < ? 
            ORDER BY Date DESC 
            LIMIT ?
            """,
            [as_of_date, lookback_sessions],
        ).df()

        if distinct_dates_df.empty:
            return None

        recent_dates = list(distinct_dates_df["Date"])
        if len(recent_dates) < 5:  # Minimum threshold to compute meaningful percentile
            return None

        # Calculate 25th percentile threshold and history length per symbol
        stats_df = con.execute(
            f"""
            WITH historical AS (
                SELECT SYMBOL, CPR_Width_Pct, Date
                FROM read_parquet('{glob_path}', hive_partitioning=1)
                WHERE Date IN (SELECT * FROM distinct_dates_df)
                  AND CPR_Width_Pct IS NOT NULL
                  AND isfinite(CPR_Width_Pct)
            )
            SELECT 
                SYMBOL,
                COUNT(*) as History_Days,
                QUANTILE_CONT(CPR_Width_Pct, 0.25) as Threshold_P25,
                AVG(CPR_Width_Pct) as Avg_Width_Pct
            FROM historical
            GROUP BY SYMBOL
            """
        ).df()

        if stats_df.empty:
            return None

        # Merge with current_df
        merged = current_df.merge(stats_df, on="SYMBOL", how="left")
        
        # Calculate rank percentage and NR4/NR7 multi-day compression using DuckDB
        rank_df = con.execute(
            f"""
            WITH hist AS (
                SELECT 
                    SYMBOL, 
                    CPR_Width_Pct,
                    ROW_NUMBER() OVER (PARTITION BY SYMBOL ORDER BY Date DESC) as recency_rank
                FROM read_parquet('{glob_path}', hive_partitioning=1)
                WHERE Date IN (SELECT * FROM distinct_dates_df)
                  AND CPR_Width_Pct IS NOT NULL
            ),
            curr AS (
                SELECT SYMBOL, CPR_Width_Pct as curr_width
                FROM current_df
            )
            SELECT 
                curr.SYMBOL,
                COUNT(hist.CPR_Width_Pct) as total_hist,
                SUM(CASE WHEN hist.CPR_Width_Pct <= curr.curr_width THEN 1 ELSE 0 END) as below_count,
                COALESCE(curr.curr_width <= MIN(CASE WHEN hist.recency_rank <= 3 THEN hist.CPR_Width_Pct END), false) as NR4,
                COALESCE(curr.curr_width <= MIN(CASE WHEN hist.recency_rank <= 6 THEN hist.CPR_Width_Pct END), false) as NR7
            FROM curr
            JOIN hist ON curr.SYMBOL = hist.SYMBOL
            GROUP BY curr.SYMBOL, curr.curr_width
            """
        ).df()

        if not rank_df.empty:
            rank_df["Width_Rank_Pct"] = (rank_df["below_count"] / rank_df["total_hist"] * 100.0).round(2)
            merged = merged.merge(rank_df[["SYMBOL", "Width_Rank_Pct", "NR4", "NR7"]], on="SYMBOL", how="left")
        else:
            merged["Width_Rank_Pct"] = None
            merged["NR4"] = False
            merged["NR7"] = False

        merged["NR4"] = merged["NR4"].fillna(False).astype(bool)
        merged["NR7"] = merged["NR7"].fillna(False).astype(bool)
        merged["Own_Narrow"] = (merged["History_Days"] >= min(30, len(recent_dates))) & (
            merged["CPR_Width_Pct"] <= merged["Threshold_P25"]
        )
        return merged
    except Exception:
        return None
    finally:
        con.close()


def cached_parquet_dates(
    end_date: str,
    lookback: int = 252,
    output_dir: Optional[Union[str, Path]] = None,
    include_end_date: bool = True,
) -> List[str]:
    """Return available distinct session dates in the Parquet lakehouse <= end_date, ordered newest-to-oldest."""
    if duckdb is None:
        return []
    root = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    parquet_root = root / PARQUET_SUBDIR
    if not parquet_root.exists():
        return []
    glob_path = get_parquet_glob(output_dir)
    op = "<=" if include_end_date else "<"
    sql = f"""
    SELECT DISTINCT Date 
    FROM read_parquet('{glob_path}', hive_partitioning=1) 
    WHERE Date {op} ?
    ORDER BY Date DESC 
    LIMIT ?
    """
    con = duckdb.connect(":memory:")
    try:
        df = con.execute(sql, [end_date, lookback]).df()
        if df.empty or "Date" not in df.columns:
            return []
        return list(df["Date"])
    except Exception:
        return []
    finally:
        con.close()


STRING_PANEL_COLS = ("NAME", "Industry", "Segment", "CPR_Class", "Bias", "Price_Position")
NUM_PANEL_COLS = (
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "VOLUME",
    "VALUE",
    "Pivot",
    "BC",
    "TC",
    "CPR_Top",
    "CPR_Bottom",
    "CPR_Width",
    "CPR_Width_Pct",
)


def load_history_panel_parquet(
    dates: Optional[List[str]] = None,
    end_date: Optional[str] = None,
    lookback: int = 252,
    output_dir: Optional[Union[str, Path]] = None,
    include_end_date: bool = True,
) -> pd.DataFrame:
    """
    Load historical CPR panel from Parquet lakehouse for specified dates or ending at end_date.
    Returns a DataFrame conforming to load_history_panel contract with 'session' column and standard numeric types.
    """
    if duckdb is None:
        return pd.DataFrame()
    root = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    parquet_root = root / PARQUET_SUBDIR
    if not parquet_root.exists():
        return pd.DataFrame()

    glob_path = get_parquet_glob(output_dir)
    con = duckdb.connect(":memory:")
    try:
        cols_df = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{glob_path}', hive_partitioning=1, union_by_name=1) LIMIT 0"
        ).df()
        existing_cols = set(cols_df["column_name"].tolist())

        select_exprs = ["Date AS session", "CAST(SYMBOL AS VARCHAR) AS SYMBOL"]
        for col in STRING_PANEL_COLS:
            if col in existing_cols:
                select_exprs.append(f'CAST(COALESCE("{col}", \'\') AS VARCHAR) AS "{col}"')
            else:
                select_exprs.append(f'\'\' AS "{col}"')
        for col in NUM_PANEL_COLS:
            if col in existing_cols:
                select_exprs.append(f'CAST("{col}" AS DOUBLE) AS "{col}"')
            else:
                select_exprs.append(f'CAST(NULL AS DOUBLE) AS "{col}"')

        cols_sql = ",\n                ".join(select_exprs)

        if dates is not None:
            if not dates:
                return pd.DataFrame()
            dates_list = list(dates)
            placeholders = ", ".join(["?"] * len(dates_list))
            sql = f"""
            SELECT 
                {cols_sql}
            FROM read_parquet('{glob_path}', hive_partitioning=1, union_by_name=1)
            WHERE Date IN ({placeholders})
            ORDER BY Date ASC, SYMBOL ASC
            """
            df = con.execute(sql, dates_list).df()
        elif end_date is not None:
            op = "<=" if include_end_date else "<"
            sql = f"""
            WITH recent_dates AS (
                SELECT DISTINCT Date 
                FROM read_parquet('{glob_path}', hive_partitioning=1) 
                WHERE Date {op} ?
                ORDER BY Date DESC 
                LIMIT ?
            )
            SELECT 
                {cols_sql}
            FROM read_parquet('{glob_path}', hive_partitioning=1, union_by_name=1)
            WHERE Date IN (SELECT Date FROM recent_dates)
            ORDER BY Date ASC, SYMBOL ASC
            """
            df = con.execute(sql, [end_date, lookback]).df()
        else:
            return pd.DataFrame()

        if df.empty:
            return pd.DataFrame()

        # Enforce standard pandas types
        for num_col in NUM_PANEL_COLS:
            if num_col in df.columns:
                df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

        df["session"] = df["session"].astype(str)
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()


def backfill_csv_to_parquet(
    output_dir: Optional[Union[str, Path]] = None,
    overwrite: bool = False,
    verbose: bool = True,
) -> int:
    """
    Scan cpr_output/ for all historical cpr_full_*.csv files and convert to Parquet.
    Returns the count of sessions converted.
    """
    root = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    csv_files = list(root.glob("**/cpr_full_*.csv")) + list(root.glob("cpr_full_*.csv"))
    
    # Deduplicate paths
    unique_csvs = {}
    for p in csv_files:
        match = re.search(r"cpr_full_(\d{8})\.csv$", p.name)
        if match:
            date = match.group(1)
            # Prefer nested YYYY/MM path over flat root path
            if date not in unique_csvs or len(p.parts) > len(unique_csvs[date].parts):
                unique_csvs[date] = p

    converted = 0
    for date in sorted(unique_csvs.keys()):
        csv_path = unique_csvs[date]
        parquet_path = parquet_session_path(date, root)
        if overwrite or not parquet_path.exists():
            try:
                df = pd.read_csv(csv_path, low_memory=False)
                if not df.empty and "SYMBOL" in df.columns:
                    save_session_parquet(df, date, root)
                    converted += 1
            except Exception as exc:
                if verbose:
                    print(f"Error converting {csv_path} to parquet: {exc}")

    if verbose:
        print(f"✓ Converted {converted} session(s) to Parquet lakehouse in {root / PARQUET_SUBDIR}")
    return converted


if __name__ == "__main__":
    backfill_csv_to_parquet()

