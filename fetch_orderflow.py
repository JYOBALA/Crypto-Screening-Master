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

Memakai infrastruktur `screener.py` apa adanya (BASE_CANDIDATES + _switch_base
+ _force_utf8 lewat efek samping `import screener`) — JANGAN menulis ulang
endpoint/fallback/encoding di sini. Pola itu sudah 3x menimbulkan bug
tersendiri (lihat CLAUDE.md): cp1252, fallback endpoint, exception TLS.

    python fetch_orderflow.py --universe u2          # U2: vol24h >= $1jt (~201 pair)
    python fetch_orderflow.py --universe u1          # U1: vol24h >= $5jt (~78 pair)
    python fetch_orderflow.py --symbols BTCUSDT,ETHUSDT --refresh
    python fetch_orderflow.py --universe u2 --start 2020-01-01

Universe U1/U2 DIBEKUKAN pada panggilan pertama: daftar simbol + snapshot
volume disimpan ke .cache_orderflow/universe_<U>.json. Panggilan berikutnya
memakai file itu apa adanya (tidak menghitung ulang) — lihat HIPOTESIS_ORDERFLOW.md
("universe dibekukan sekali, tidak disaring ulang per tanggal").

Read-only terhadap akun: tidak ada API key, tidak ada order. Hanya GET publik.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr              # noqa: E402  (_get/_switch_base/BASE_CANDIDATES/_force_utf8 via import)
import fetch_history as fh          # noqa: E402  (list_usdt_pairs — pola exclude stablecoin/leverage sama persis)

OF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_orderflow")
MS_DAY = 86_400_000
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "qav", "trades", "tbbav", "tbqav", "ignore"]
KEEP_COLS = ["open", "high", "low", "close", "volume", "qav", "trades", "tbbav", "tbqav"]

UNIVERSE_MIN_USD = {"u1": 5_000_000.0, "u2": 1_000_000.0}   # HIPOTESIS_ORDERFLOW.md

WEIGHT_THROTTLE_FRAC = 0.70     # jeda kalau used-weight lewat 70% batas
WEIGHT_THROTTLE_SLEEP = 5.0
_weight_limit_1m: int | None = None


# ─────────────────────────────────────────────────────────────
# Universe U1/U2 — dibangun sekali dari snapshot volume 24h, lalu dibekukan
# ─────────────────────────────────────────────────────────────

def build_universe(key: str) -> tuple[list[str], dict]:
    """Bangun (atau muat, kalau sudah pernah dibangun) universe U1/U2. Dibekukan
    ke .cache_orderflow/universe_<key>.json supaya TIDAK dihitung ulang di
    tanggal berbeda — menghitung ulang berarti memakai volume hari-ke-hari
    sebagai filter seleksi, yang membocorkan info masa depan ke universe."""
    os.makedirs(OF_DIR, exist_ok=True)
    frozen_path = os.path.join(OF_DIR, f"universe_{key}.json")
    if os.path.exists(frozen_path):
        with open(frozen_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta["symbols"], meta

    min_usd = UNIVERSE_MIN_USD[key]
    info = scr._get("/api/v3/exchangeInfo")
    tick = {t["symbol"]: float(t.get("quoteVolume", 0)) for t in scr._get("/api/v3/ticker/24hr")}
    syms = []
    for s in info.get("symbols", []):
        if s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
            continue
        if not s.get("isSpotTradingAllowed", False):
            continue
        base = s.get("baseAsset", "")
        if base in scr.STABLES or any(base.endswith(x) for x in scr.EXCLUDE_TOKENS):
            continue
        sym = s["symbol"]
        if tick.get(sym, 0.0) >= min_usd:
            syms.append(sym)
    syms = sorted(set(syms))

    meta = {
        "universe": key,
        "min_usd_volume_24h": min_usd,
        "snapshot_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_symbols": len(syms),
        "symbols": syms,
    }
    with open(frozen_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return syms, meta


# ─────────────────────────────────────────────────────────────
# Pemantauan rate-limit weight (X-MBX-USED-WEIGHT-1M)
# ─────────────────────────────────────────────────────────────

def _init_weight_limit() -> None:
    global _weight_limit_1m
    try:
        info = scr._get("/api/v3/exchangeInfo")
        for rl in info.get("rateLimits", []):
            if rl.get("rateLimitType") == "REQUEST_WEIGHT" and rl.get("interval") == "MINUTE":
                _weight_limit_1m = int(rl.get("limit"))
                break
    except Exception:
        pass
    if not _weight_limit_1m:
        _weight_limit_1m = 6000     # default aman Binance kalau exchangeInfo gagal ditarik


def _throttle_if_near_limit() -> None:
    """Header X-MBX-USED-WEIGHT-1M spesifik Binance; endpoint cadangan yang tidak
    mengirimkannya (mis. data-api.binance.vision) diam-diam dilewati -- tidak ada
    info untuk dipantau, bukan error."""
    try:
        used = int(scr.LAST_HEADERS.get("X-MBX-USED-WEIGHT-1M", 0))
    except Exception:
        return
    if used and _weight_limit_1m and used / _weight_limit_1m > WEIGHT_THROTTLE_FRAC:
        print(f"   (weight {used}/{_weight_limit_1m} > {WEIGHT_THROTTLE_FRAC:.0%} -> jeda {WEIGHT_THROTTLE_SLEEP}s)",
              flush=True)
        time.sleep(WEIGHT_THROTTLE_SLEEP)


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
        _throttle_if_near_limit()
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
        time.sleep(0.2)                     # jeda antar request, per SOP order-flow

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
    """Progres per simbol disimpan segera setelah unduh (parquet per simbol) —
    kalau proses terputus, jalankan ulang perintah yang sama: simbol yang sudah
    ada file-nya dilewati (kecuali --refresh), lanjut dari simbol berikutnya."""
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
    ap.add_argument("--universe", choices=["u1", "u2"], default=None,
                    help="u1 = vol24h >= $5jt (~78 pair), u2 = vol24h >= $1jt (~201 pair). "
                         "Dibekukan sekali ke .cache_orderflow/universe_<u>.json.")
    ap.add_argument("--max-pairs", type=int, default=0,
                    help="Batasi jumlah pair kalau TIDAK pakai --universe (0 = semua pair USDT TRADING).")
    ap.add_argument("--symbols", default=None, help="Paksa daftar simbol tertentu (dipisah koma)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--refresh", action="store_true", help="Unduh ulang walau parquet sudah ada")
    args = ap.parse_args()

    os.makedirs(OF_DIR, exist_ok=True)
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)

    _init_weight_limit()
    print(f"-> Endpoint aktif: {scr.BASE}  |  batas weight/menit: {_weight_limit_1m}", flush=True)

    if args.symbols:
        pairs = [s.strip().upper() for s in args.symbols.split(",")]
    elif args.universe:
        print(f"-> Membangun/memuat universe {args.universe.upper()} "
              f"(vol24h >= ${UNIVERSE_MIN_USD[args.universe]:,.0f}) ...", flush=True)
        pairs, meta = build_universe(args.universe)
        print(f"   {meta['n_symbols']} pair (snapshot {meta['snapshot_utc']}) -> "
              f".cache_orderflow/universe_{args.universe}.json", flush=True)
    else:
        print("-> Mengambil daftar pair USDT (sama seperti fetch_history.py, TANPA filter volume) ...",
              flush=True)
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
