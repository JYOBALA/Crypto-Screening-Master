#!/usr/bin/env python3
"""
fetch_orderflow.py — Unduh sejarah harian TERMASUK kolom aliran order Binance
yang selama ini dibuang oleh fetch_klines()/fetch_history.py:

  index 7  = quote asset volume        (qav)
  index 8  = number of trades          (trades)
  index 9  = taker buy base asset volume  (tbbav)
  index 10 = taker buy quote asset volume (tbqav)

Dipakai HANYA untuk uji nilai prediktif aliran order (orderflow_test.py) —
proyek terpisah dari screener harian, metodologi baru, tidak menyentuh
`.cache_history/` (dipakai backtest.py) atau cache screener biasa.

    python fetch_orderflow.py                      # semua pair, mulai 2021-01-01
    python fetch_orderflow.py --start 2020-01-01
    python fetch_orderflow.py --max-pairs 150 --workers 6
    python fetch_orderflow.py --symbols BTCUSDT,ETHUSDT --refresh

Read-only terhadap akun: tidak ada API key, tidak ada order. Hanya GET publik.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr              # noqa: E402  (_get, dst.)
import fetch_history as fh          # noqa: E402  (list_usdt_pairs — universe sama persis)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()

OF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_orderflow")
MS_DAY = 86_400_000
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "qav", "trades", "tbbav", "tbqav", "ignore"]
KEEP_COLS = ["open", "high", "low", "close", "volume", "qav", "trades", "tbbav", "tbqav"]


# ─────────────────────────────────────────────────────────────
# Paginasi klines lewat startTime — sama seperti fetch_history.py, TAPI
# menyimpan qav/trades/tbbav/tbqav alih-alih membuangnya
# ─────────────────────────────────────────────────────────────

def fetch_full_history_of(symbol: str, start_ms: int, interval: str = "1d") -> pd.DataFrame | None:
    now_ms = int(time.time() * 1000)
    all_rows: list[list] = []
    cur = start_ms
    guard = 0
    while cur < now_ms and guard < 200:
        guard += 1
        try:
            raw = scr._get("/api/v3/klines", {
                "symbol": symbol, "interval": interval,
                "startTime": cur, "limit": 1000,
            })
        except Exception:
            return None
        if not raw:
            break
        all_rows.extend(raw)
        last_open = raw[-1][0]
        if len(raw) < 1000:
            break
        nxt = last_open + MS_DAY
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.15)

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows, columns=KLINE_COLS)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume", "qav", "tbbav", "tbqav"):
        df[c] = df[c].astype(float)
    df["trades"] = df["trades"].astype("int64")
    df = (df.set_index("time")[KEEP_COLS]
            .loc[~df.set_index("time").index.duplicated(keep="first")]
            .sort_index())
    df = df.iloc[:-1]                       # buang candle berjalan (aturan inti SOP)
    return df if len(df) else None


def process(symbol: str, start_ms: int, refresh: bool) -> tuple[str, int, str]:
    path = os.path.join(OF_DIR, f"{symbol}_1d.parquet")
    if os.path.exists(path) and not refresh:
        try:
            n = len(pd.read_parquet(path))
            return symbol, n, "cache"
        except Exception:
            pass
    df = fetch_full_history_of(symbol, start_ms)
    if df is None or len(df) < 60:
        return symbol, 0, "kosong/pendek"
    try:
        df.to_parquet(path)
    except Exception as e:                  # noqa: BLE001
        return symbol, len(df), f"gagal simpan: {e}"
    span = f"{df.index[0].date()}..{df.index[-1].date()}"
    return symbol, len(df), span


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Unduh sejarah harian + kolom aliran order")
    ap.add_argument("--start", default="2021-01-01",
                    help="Tanggal mulai (YYYY-MM-DD). Default sama dengan fetch_history.py.")
    ap.add_argument("--max-pairs", type=int, default=0,
                    help="Batasi jumlah pair (0 = semua pair USDT TRADING, sama seperti fetch_history.py).")
    ap.add_argument("--symbols", default=None, help="Paksa daftar simbol tertentu (dipisah koma)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--refresh", action="store_true", help="Unduh ulang walau parquet sudah ada")
    args = ap.parse_args()

    os.makedirs(OF_DIR, exist_ok=True)
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)

    if args.symbols:
        pairs = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        print("-> Mengambil daftar pair USDT (sama seperti fetch_history.py) ...", flush=True)
        pairs = fh.list_usdt_pairs(args.max_pairs or None)
    if "BTCUSDT" not in pairs:
        pairs = ["BTCUSDT"] + pairs
    print(f"   {len(pairs)} pair akan diunduh sejak {args.start} -> {OF_DIR}")

    done = 0
    total_bars = 0
    t0 = time.time()
    N = len(pairs)
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, s, start_ms, args.refresh): s for s in pairs}
        for fut in cf.as_completed(futs):
            sym, n, info = fut.result()
            done += 1
            total_bars += n
            eta = ""
            if done >= 5:
                rem = (time.time() - t0) / done * (N - done)
                m, s = divmod(int(rem), 60)
                eta = f"  | ETA {m:02d}:{s:02d}"
            print(f"   [{done}/{N}] {done/N*100:3.0f}%  {sym:<14} {n:>5} bar  {info}{eta}", flush=True)

    # ringkasan kelengkapan kolom order flow (bukan cuma jumlah bar)
    n_files = 0
    n_zero_trades = 0
    n_zero_qav = 0
    total_bars_chk = 0
    for f in os.listdir(OF_DIR):
        if not f.endswith("_1d.parquet"):
            continue
        n_files += 1
        try:
            d = pd.read_parquet(os.path.join(OF_DIR, f))
        except Exception:
            continue
        total_bars_chk += len(d)
        if len(d) and (d["trades"] == 0).mean() > 0.5:
            n_zero_trades += 1
        if len(d) and (d["qav"] == 0).mean() > 0.5:
            n_zero_qav += 1

    dt = time.time() - t0
    print("\n" + "=" * 70)
    print(f"  SELESAI dalam {dt/60:.1f} menit")
    print(f"  Pair tersimpan            : {n_files}")
    print(f"  Total bar                 : {total_bars_chk:,}")
    print(f"  Pair 'trades' mayoritas 0 : {n_zero_trades}  (kemungkinan data order flow tidak terisi)")
    print(f"  Pair 'qav' mayoritas 0    : {n_zero_qav}")
    print(f"  Lokasi                    : {OF_DIR}")
    print("=" * 70)
    print("  Lanjut: python orderflow_test.py")


if __name__ == "__main__":
    main()
