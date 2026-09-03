#!/usr/bin/env python3
"""
fetch_history.py — Unduh sejarah harian SEPANJANG MUNGKIN untuk banyak pair USDT.

Tujuan: memberi backtest.py data yang cukup untuk dinilai. Cache harian `.cache/`
hanya berisi ~1000 bar terakhir dari pair yang LOLOS filter volume hari ini —
itu survivorship bias (koin yang pump lalu mati tidak pernah masuk) dan tidak
mencakup bear market 2022 (uji veto BTC MERAH).

Skrip ini:
  - mengambil SEMUA pair spot `*USDT` berstatus TRADING (bukan cuma yang bervolume
    besar) — termasuk yang sekarang sepi;
  - mem-paginasi `/api/v3/klines` lewat `startTime` (maks 1000 bar/panggilan)
    sampai ke masa kini;
  - menyimpan ke `.cache_history/<SYMBOL>_1d.parquet` (dir terpisah, tidak
    mengganggu screener harian).

    python fetch_history.py                      # semua pair, mulai 2021-01-01
    python fetch_history.py --start 2020-01-01
    python fetch_history.py --max-pairs 150 --workers 6
    python fetch_history.py --symbols BTCUSDT,ETHUSDT --refresh

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
import screener as scr             # noqa: E402  (_get, _switch_base, STABLES, dst.)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()

HIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_history")
MS_DAY = 86_400_000
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "qav", "trades", "tbbav", "tbqav", "ignore"]


# ─────────────────────────────────────────────────────────────
# Pemilihan universe — SEMUA pair USDT TRADING, tanpa filter volume
# ─────────────────────────────────────────────────────────────

def list_usdt_pairs(max_pairs: int | None) -> list[str]:
    """Semua simbol spot `*USDT` berstatus TRADING (stablecoin & token leverage
    dibuang). Kalau dibatasi max_pairs, ambil sampel MERATA sepanjang daftar
    terurut-volume — bukan hanya yang tervolume besar — supaya pair sepi ikut."""
    info = scr._get("/api/v3/exchangeInfo")
    quote = "USDT"
    syms = []
    for s in info.get("symbols", []):
        if s.get("quoteAsset") != quote or s.get("status") != "TRADING":
            continue
        if not s.get("isSpotTradingAllowed", False):
            continue
        base = s.get("baseAsset", "")
        if base in scr.STABLES or any(base.endswith(x) for x in scr.EXCLUDE_TOKENS):
            continue
        syms.append(s["symbol"])
    syms = sorted(set(syms))

    # urutkan berdasarkan volume 24h (biar sampel merata punya arti), tapi JANGAN
    # buang yang kecil
    try:
        tick = {t["symbol"]: float(t.get("quoteVolume", 0))
                for t in scr._get("/api/v3/ticker/24hr")}
        syms.sort(key=lambda x: -tick.get(x, 0.0))
    except Exception:
        pass

    if not max_pairs or max_pairs >= len(syms):
        return syms

    # sampel merata: selalu sertakan BTC/ETH di depan, lalu ambil tiap k-langkah
    keep = {"BTCUSDT", "ETHUSDT"}
    step = len(syms) / max_pairs
    picked = [syms[int(i * step)] for i in range(max_pairs)]
    out = list(dict.fromkeys([s for s in syms if s in keep] + picked))
    return out[:max_pairs]


# ─────────────────────────────────────────────────────────────
# Paginasi klines lewat startTime
# ─────────────────────────────────────────────────────────────

def fetch_full_history(symbol: str, start_ms: int, interval: str = "1d") -> pd.DataFrame | None:
    """Tarik klines dari start_ms sampai sekarang, 1000 bar per panggilan."""
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
        if nxt <= cur:                      # tidak maju -> hentikan (jaga-jaga)
            break
        cur = nxt
        time.sleep(0.15)                    # sopan ke rate limit

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows, columns=KLINE_COLS)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df = (df.set_index("time")[["open", "high", "low", "close", "volume"]]
            .loc[~df.set_index("time").index.duplicated(keep="first")]
            .sort_index())
    df = df.iloc[:-1]                       # buang candle berjalan (aturan inti SOP)
    return df if len(df) else None


def process(symbol: str, start_ms: int, refresh: bool) -> tuple[str, int, str]:
    path = os.path.join(HIST_DIR, f"{symbol}_1d.parquet")
    if os.path.exists(path) and not refresh:
        try:
            n = len(pd.read_parquet(path))
            return symbol, n, "cache"
        except Exception:
            pass
    df = fetch_full_history(symbol, start_ms)
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
    ap = argparse.ArgumentParser(description="Unduh sejarah harian panjang untuk backtest")
    ap.add_argument("--start", default="2021-01-01",
                    help="Tanggal mulai (YYYY-MM-DD). Default 2021-01-01 supaya mencakup bear 2022.")
    ap.add_argument("--max-pairs", type=int, default=0,
                    help="Batasi jumlah pair (0 = semua pair USDT TRADING). Sampel merata bila dibatasi.")
    ap.add_argument("--symbols", default=None, help="Paksa daftar simbol tertentu (dipisah koma)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--refresh", action="store_true", help="Unduh ulang walau parquet sudah ada")
    args = ap.parse_args()

    os.makedirs(HIST_DIR, exist_ok=True)
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)

    if args.symbols:
        pairs = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        print("-> Mengambil daftar pair USDT (semua status TRADING, tanpa filter volume) ...", flush=True)
        pairs = list_usdt_pairs(args.max_pairs or None)
    if "BTCUSDT" not in pairs:
        pairs = ["BTCUSDT"] + pairs          # wajib ada untuk regime backtest
    print(f"   {len(pairs)} pair akan diunduh sejak {args.start} -> {HIST_DIR}")

    done = 0
    total_bars = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, s, start_ms, args.refresh): s for s in pairs}
        for fut in cf.as_completed(futs):
            sym, n, info = fut.result()
            done += 1
            total_bars += n
            print(f"   [{done}/{len(pairs)}] {sym:<14} {n:>5} bar  {info}", flush=True)

    covers_2022 = 0
    short = 0
    for f in os.listdir(HIST_DIR):
        if not f.endswith("_1d.parquet"):
            continue
        try:
            d = pd.read_parquet(os.path.join(HIST_DIR, f))
        except Exception:
            continue
        if len(d) and d.index[0].year <= 2022 and d.index.max().year >= 2022:
            covers_2022 += 1
        if len(d) < 300:
            short += 1

    dt = time.time() - t0
    print("\n" + "=" * 70)
    print(f"  SELESAI dalam {dt/60:.1f} menit")
    print(f"  Pair tersimpan       : {len([f for f in os.listdir(HIST_DIR) if f.endswith('_1d.parquet')])}")
    print(f"  Total bar            : {total_bars:,}")
    print(f"  Pair mencakup 2022   : {covers_2022}  (dibutuhkan untuk uji veto BTC MERAH)")
    print(f"  Pair < 300 bar       : {short}  (akan otomatis dilewati backtest saat warm-up)")
    print(f"  Lokasi               : {HIST_DIR}")
    print("=" * 70)
    print("  Lanjut: python backtest.py --history")


if __name__ == "__main__":
    main()
