#!/usr/bin/env python3
"""
backtest.py — Walk-forward backtest sistem skor 100 poin (mode daily/weekly).

Alat ukur, bukan alat untuk membuat angkanya bagus. Tidak menyetel scoring.py,
indicators.py, atau accumulation.py — hanya membaca lewat evaluate().

Semua sinyal non-veto direkam (termasuk grade C < 60 yang tak pernah ditampilkan
screener) supaya pertanyaan "apakah skor lebih tinggi = ekspektasi lebih tinggi"
bisa dijawab lintas seluruh rentang skor. Metrik agregat dilaporkan dua kali:
seluruh sinyal, dan hanya yang skornya >= min_score (yang benar-benar user ambil).

    python backtest.py
    python backtest.py --symbols BTCUSDT,ETHUSDT --workers 4

Aturan tanpa-lookahead (lihat CLAUDE.md):
  1. evaluate() hanya dipanggil dengan df.iloc[:t+1] — potongan asli, bukan
     seluruh dataframe yang hasilnya dipotong belakangan.
  2. Regime BTC dihitung dari data BTC sampai bar t saja (lookup per tanggal).
  3. Entry paling cepat di bar t+1.
  4. SL & TP tersentuh di bar yang sama -> asumsikan SL duluan (konservatif).
  5. Fee 0.1% + slippage 0.05% per sisi, diterapkan di tiap eksekusi (entry
     dan tiap leg exit).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as ta          # noqa: E402
import scoring                   # noqa: E402
import screener as scr           # noqa: E402


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()


def _eta(done: int, total: int, t_start: float) -> str:
    """String '[done/total] xx% | ETA mm:ss' untuk proses panjang."""
    if done <= 0:
        return f"[0/{total}]"
    elapsed = time.time() - t_start
    rem = elapsed / done * (total - done)
    m, s = divmod(int(rem), 60)
    return f"[{done}/{total}] {done/total*100:3.0f}% | ETA {m:02d}:{s:02d}"


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
HIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_history")

# ── Parameter tetap sesuai spesifikasi (bukan parameter yang "dioprek" agar
#    hasil terlihat bagus — ini definisi mekanik backtest itu sendiri) ──
MIN_BAND_SAMPLE = 30    # di bawah ini per pita skor: hasil tidak boleh diklaim prediktif
WARMUP_BARS = 250       # lewati N bar pertama (butuh warm-up MA/pivot)
ENTRY_WINDOW = 10       # batalkan setup kalau entry tak tersentuh dalam N bar
MAX_HOLD = 30           # timeout posisi (bar dihitung sejak fill, bukan sejak sinyal)
MIN_FORWARD = ENTRY_WINDOW + MAX_HOLD   # bar minimum yang harus tersisa agar sinyal disimulasikan penuh
FEE = 0.001             # 0.1% per sisi
SLIP = 0.0005           # 0.05% per sisi
BTC_REGIME_WARM = 60    # warm-up minimum sebelum regime BTC dianggap valid


# ─────────────────────────────────────────────────────────────
# Universe dari cache lokal (tidak download ulang)
# ─────────────────────────────────────────────────────────────

def load_universe(symbols_filter: list[str] | None = None,
                  use_history: bool = False) -> dict[str, pd.DataFrame]:
    """Muat satu dataframe harian per simbol.

    - default: `.cache/<SYM>_1d_<stamp>.parquet` (stamp terbaru) — cache screener harian.
    - use_history: `.cache_history/<SYM>_1d.parquet` — arsip panjang dari fetch_history.py.
    """
    if use_history:
        files = glob.glob(os.path.join(HIST_DIR, "*_1d.parquet"))
        latest = {os.path.basename(f).split("_1d.parquet")[0]: f for f in files}
    else:
        files = glob.glob(os.path.join(CACHE_DIR, "*_1d_*.parquet"))
        latest = {}
        for f in files:
            base = os.path.basename(f)
            sym = base.split("_1d_")[0]
            stamp = base.split("_1d_")[1].replace(".parquet", "")
            if sym not in latest or stamp > latest[sym].split("_1d_")[1].replace(".parquet", ""):
                latest[sym] = f
    out = {}
    for sym, path in sorted(latest.items()):
        if symbols_filter and sym not in symbols_filter:
            continue
        df = pd.read_parquet(path)
        if not df.index.is_monotonic_increasing:
            df = df.sort_index()
        out[sym] = df
    return out


# ─────────────────────────────────────────────────────────────
# Regime BTC per tanggal — dihitung sekali secara walk-forward,
# dipakai lewat lookup supaya tidak dihitung ulang untuk tiap simbol.
# ─────────────────────────────────────────────────────────────

def build_btc_regime_lookup(btc_df: pd.DataFrame):
    # Pakai DatetimeIndex apa adanya untuk pencarian — JANGAN ubah ke int epoch.
    # Bug lama: `.asi8` mengembalikan int dalam satuan resolusi index (parquet
    # menyimpan datetime64[ms], jadi milidetik), sedangkan `pd.Timestamp.value`
    # selalu NANOdetik. searchsorted membandingkan ms vs ns -> nilai ns jauh
    # lebih besar dari semua entri -> selalu mengembalikan bar TERAKHIR (regime
    # masa kini) untuk SETIAP tanggal. Akibatnya seluruh backtest memakai regime
    # BTC 2026 untuk bar 2021-2025 (veto MERAH tak pernah aktif, split bull/bear
    # tak berarti). DatetimeIndex.searchsorted menangani tz + resolusi sendiri.
    idx = btc_df.index
    regimes: list[dict | None] = [None] * len(btc_df)
    for t in range(BTC_REGIME_WARM, len(btc_df)):
        regimes[t] = scoring.btc_regime(btc_df.iloc[:t + 1])
    return idx, regimes


def regime_at(btc_index, btc_regimes, date) -> dict | None:
    ts = pd.Timestamp(date)
    if ts.tz is None and btc_index.tz is not None:
        ts = ts.tz_localize(btc_index.tz)
    pos = int(btc_index.searchsorted(ts, side="right")) - 1
    if pos < BTC_REGIME_WARM or pos >= len(btc_regimes):
        return None
    return btc_regimes[pos]


# ─────────────────────────────────────────────────────────────
# Mesin eksekusi: entry limit lalu exit (SL/TP1 parsial/TP2/timeout)
# ─────────────────────────────────────────────────────────────

def try_fill(df: pd.DataFrame, signal_idx: int, entry: float) -> int | None:
    """Cari bar pertama sejak signal_idx+1 (bar t+1) yang menyentuh level entry
    dalam jendela ENTRY_WINDOW. None kalau tak pernah tersentuh -> setup batal."""
    end = min(signal_idx + ENTRY_WINDOW, len(df) - 1)
    for j in range(signal_idx + 1, end + 1):
        lo = float(df["low"].iloc[j])
        hi = float(df["high"].iloc[j])
        if lo <= entry <= hi:
            return j
    return None


def simulate_exit(df: pd.DataFrame, fill_idx: int, entry: float, sl: float,
                  tp1: float, tp2: float):
    """State machine exit. Return (legs, exit_idx, outcome_tag).
    legs = list of (bar_idx, price, weight, tag)."""
    n = len(df)
    exit_end = min(fill_idx + MAX_HOLD, n - 1)
    cur_sl = sl
    tp1_hit = False
    legs = []

    for j in range(fill_idx + 1, exit_end + 1):
        lo = float(df["low"].iloc[j])
        hi = float(df["high"].iloc[j])
        if not tp1_hit:
            hit_sl = lo <= cur_sl
            hit_tp1 = hi >= tp1
            if hit_sl:                              # SL duluan kalau dua-duanya kena (aturan 4)
                legs.append((j, cur_sl, 1.0, "SL"))
                return legs, j, "SL"
            if hit_tp1:
                legs.append((j, tp1, 0.5, "TP1"))
                tp1_hit = True
                cur_sl = entry                        # SL naik ke break-even
                continue
        else:
            hit_be = lo <= cur_sl
            hit_tp2 = hi >= tp2
            if hit_be:                                # break-even diperlakukan sama seperti SL
                legs.append((j, cur_sl, 0.5, "BE"))
                return legs, j, "TP1_BE"
            if hit_tp2:
                legs.append((j, tp2, 0.5, "TP2"))
                return legs, j, "TP1_TP2"

    close_price = float(df["close"].iloc[exit_end])
    if tp1_hit:
        legs.append((exit_end, close_price, 0.5, "TIMEOUT"))
        return legs, exit_end, "TP1_TIMEOUT"
    legs.append((exit_end, close_price, 1.0, "TIMEOUT"))
    return legs, exit_end, "TIMEOUT"


def cost_adjusted_pnl_r(entry: float, sl: float, legs: list) -> float:
    """P&L dalam satuan R, sudah memperhitungkan fee + slippage tiap eksekusi.
    R didefinisikan dari risiko rencana awal (entry-sl), bukan risiko setelah biaya —
    ini konvensi standar: biaya mengurangi R yang benar-benar didapat."""
    entry_eff = entry * (1 + SLIP) * (1 + FEE)
    pnl_per_unit = 0.0
    for _, price, weight, _ in legs:
        exit_eff = price * (1 - SLIP) * (1 - FEE)
        pnl_per_unit += weight * (exit_eff - entry_eff)
    risk_per_unit = entry - sl
    return pnl_per_unit / risk_per_unit if risk_per_unit > 0 else 0.0


# ─────────────────────────────────────────────────────────────
# Walk-forward per simbol (sistem skor asli)
#
# Pekerjaan ini CPU-bound (evaluate() penuh tiap bar). Dengan ratusan simbol,
# thread tidak menolong (GIL) — pakai ProcessPoolExecutor. Data regime BTC yang
# besar dibagikan sekali lewat initializer, bukan di-pickle ulang tiap task.
# ─────────────────────────────────────────────────────────────

_G_BTC_INDEX = None
_G_BTC_REGIMES = None
_G_CFG = None


def _pool_init(btc_index, btc_regimes, cfg):
    global _G_BTC_INDEX, _G_BTC_REGIMES, _G_CFG
    _G_BTC_INDEX, _G_BTC_REGIMES, _G_CFG = btc_index, btc_regimes, cfg


def _bt_one(item: tuple[str, pd.DataFrame]) -> tuple[str, list[dict], list[str]]:
    sym, df = item
    rows, errors = backtest_symbol(sym, df, _G_BTC_INDEX, _G_BTC_REGIMES, _G_CFG)
    return sym, rows, errors


def backtest_symbol(symbol: str, df: pd.DataFrame, btc_index, btc_regimes,
                    cfg: dict) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    n = len(df)
    t = WARMUP_BARS
    while t <= n - 1 - MIN_FORWARD:
        bias = df.iloc[:t + 1]                      # ATURAN 1: potongan asli sampai bar t
        date_t = df.index[t]
        regime = regime_at(btc_index, btc_regimes, date_t)   # ATURAN 2
        if regime is None:
            t += 1
            continue
        try:
            htf = ta.resample_ohlcv(bias, "W-MON")
            result = scoring.evaluate(symbol, bias, htf, regime, cfg)
        except Exception as e:                       # noqa: BLE001
            errors.append(f"{symbol}@{date_t}: {e}")
            t += 1
            continue

        if result is None:
            t += 1
            continue

        # Counterfactual veto BTC MERAH: kalau SATU-SATUNYA alasan veto adalah
        # "BTC status MERAH", simulasikan tetap — untuk mengukur apakah veto itu
        # menyelamatkan modal atau justru membuang periode yang menguntungkan.
        # (Veto lain akan tetap membatalkan setup walau MERAH dimatikan, jadi
        #  hanya kasus MERAH-sendiri yang bisa diisolasi.)
        merah_only = (result["vetoed"]
                      and result.get("veto_reasons") == ["BTC status MERAH"])
        if result["vetoed"] and not merah_only:
            t += 1
            continue

        plan = result["plan"]
        base_row = {
            "symbol": symbol, "signal_date": date_t,
            "regime_status": regime["status"],
            "total_score": result["total"], "grade": result["grade"],
            "s_volume": result["s_volume"], "s_stochrsi": result["s_stochrsi"],
            "s_fib": result["s_fib"], "s_sr": result["s_sr"],
            "s_pattern": result["s_pattern"],
            "plan_entry": plan["entry"], "plan_sl": plan["sl"],
            "plan_tp1": plan["tp1"], "plan_tp2": plan["tp2"],
            "plan_rr1": plan["rr1"],
        }
        filled_status = "filled_merah_cf" if merah_only else "filled"

        fill_idx = try_fill(df, t, plan["entry"])     # ATURAN 3: paling cepat bar t+1
        if fill_idx is None:
            cancel_status = "cancelled_merah_cf" if merah_only else "cancelled"
            rows.append({**base_row, "status": cancel_status,
                         "fill_date": None, "fill_price": None, "exit_date": None,
                         "exit_type": None, "bars_held": None, "pnl_r": None, "win": None})
            t = min(t + ENTRY_WINDOW, n - 1) + 1
            continue

        legs, exit_idx, outcome = simulate_exit(df, fill_idx, plan["entry"],
                                                plan["sl"], plan["tp1"], plan["tp2"])
        pnl_r = cost_adjusted_pnl_r(plan["entry"], plan["sl"], legs)
        rows.append({**base_row, "status": filled_status,
                     "fill_date": df.index[fill_idx], "fill_price": plan["entry"],
                     "exit_date": df.index[exit_idx], "exit_type": outcome,
                     "bars_held": exit_idx - fill_idx,
                     "pnl_r": pnl_r, "win": pnl_r > 0})
        # Counterfactual MERAH tidak "menghabiskan" slot posisi nyata — di dunia
        # nyata setup ini tak diambil, jadi jangan majukan t ke exit-nya.
        t = (min(t + ENTRY_WINDOW, n - 1) + 1) if merah_only else exit_idx + 1
    return rows, errors


# ─────────────────────────────────────────────────────────────
# Baseline: beli acak dengan mekanik SL/TP/exit yang sama
# ─────────────────────────────────────────────────────────────

def build_valid_pool(universe: dict[str, pd.DataFrame]) -> list[tuple[str, int]]:
    pool = []
    for sym, df in universe.items():
        n = len(df)
        for t in range(WARMUP_BARS, n - MIN_FORWARD):
            pool.append((sym, t))
    return pool


def baseline_trade(df: pd.DataFrame, t: int, cfg: dict) -> dict:
    entry = float(df["close"].iloc[t])
    a = float(ta.atr(df.iloc[:t + 1]).iloc[-1])
    sl = entry - 2.0 * a
    risk = entry - sl
    tp1 = entry + cfg["min_rr"] * risk
    tp2 = entry + (cfg["min_rr"] + 1.0) * risk
    fill_idx = t                      # beli seketika di close bar t (bukan limit)
    legs, exit_idx, outcome = simulate_exit(df, fill_idx, entry, sl, tp1, tp2)
    pnl_r = cost_adjusted_pnl_r(entry, sl, legs)
    return {
        "symbol": None, "signal_date": df.index[t], "status": "filled",
        "total_score": None, "grade": None,
        "s_volume": None, "s_stochrsi": None, "s_fib": None, "s_sr": None,
        "s_pattern": None,
        "plan_entry": entry, "plan_sl": sl, "plan_tp1": tp1, "plan_tp2": tp2,
        "plan_rr1": cfg["min_rr"],
        "fill_date": df.index[fill_idx], "fill_price": entry,
        "exit_date": df.index[exit_idx], "exit_type": outcome,
        "bars_held": exit_idx - fill_idx,
        "pnl_r": pnl_r, "win": pnl_r > 0,
    }


def run_baseline(universe: dict[str, pd.DataFrame], n_samples: int, seed: int,
                 cfg: dict) -> list[dict]:
    pool = build_valid_pool(universe)
    if not pool:
        return []
    rng = np.random.default_rng(seed)
    n_samples = min(n_samples, len(pool))
    idxs = rng.choice(len(pool), size=n_samples, replace=False)
    rows = []
    for i in idxs:
        sym, t = pool[i]
        row = baseline_trade(universe[sym], t, cfg)
        row["symbol"] = sym
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────
# Metrik
# ─────────────────────────────────────────────────────────────

def compute_metrics(filled: pd.DataFrame) -> dict:
    if filled.empty:
        return {"n": 0}
    wins = filled[filled["pnl_r"] > 0]["pnl_r"]
    losses = filled[filled["pnl_r"] <= 0]["pnl_r"]
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    ordered = filled.sort_values("exit_date")
    equity = ordered["pnl_r"].cumsum()
    running_max = equity.cummax()
    dd = running_max - equity
    max_dd = float(dd.max()) if len(dd) else 0.0

    return {
        "n": len(filled),
        "win_rate": float((filled["pnl_r"] > 0).mean()),
        "profit_factor": pf,
        "expectancy_r": float(filled["pnl_r"].mean()),
        "max_drawdown_r": max_dd,
        "avg_bars_held": float(filled["bars_held"].mean()),
    }


def score_band_breakdown(filled: pd.DataFrame) -> pd.DataFrame:
    bands = [(-1, 60, "<60"), (60, 70, "60-69"), (70, 80, "70-79"),
             (80, 90, "80-89"), (90, 101, "90+")]
    rows = []
    for lo, hi, label in bands:
        sub = filled[(filled["total_score"] >= lo) & (filled["total_score"] < hi)]
        if sub.empty:
            rows.append({"pita": label, "n": 0, "win_rate": None, "expectancy_r": None})
            continue
        rows.append({
            "pita": label, "n": len(sub),
            "win_rate": float((sub["pnl_r"] > 0).mean()),
            "expectancy_r": float(sub["pnl_r"].mean()),
        })
    return pd.DataFrame(rows)


def rr_band_breakdown(filled: pd.DataFrame) -> pd.DataFrame:
    """Ekspektasi per pita R:R RENCANA (bukan skor). Menguji ulang temuan
    backtest v1: R:R lebar berkinerja lebih buruk?"""
    bands = [(0, 3, "0-3"), (3, 5, "3-5"), (5, 8, "5-8"), (8, 999, ">8")]
    rows = []
    for lo, hi, label in bands:
        sub = filled[(filled["plan_rr1"] >= lo) & (filled["plan_rr1"] < hi)]
        if sub.empty:
            rows.append({"pita_rr": label, "n": 0, "win_rate": None, "expectancy_r": None})
            continue
        rows.append({
            "pita_rr": label, "n": len(sub),
            "win_rate": float((sub["pnl_r"] > 0).mean()),
            "expectancy_r": float(sub["pnl_r"].mean()),
        })
    return pd.DataFrame(rows)


def component_correlation(filled: pd.DataFrame) -> pd.DataFrame:
    comps = ["s_volume", "s_stochrsi", "s_fib", "s_sr", "s_pattern"]
    out = []
    for c in comps:
        if filled[c].nunique(dropna=True) < 2:
            out.append({"komponen": c, "corr_pnl_r": None, "corr_win": None})
            continue
        out.append({
            "komponen": c,
            "corr_pnl_r": float(filled[c].corr(filled["pnl_r"])),
            "corr_win": float(filled[c].corr(filled["win"].astype(float))),
        })
    return pd.DataFrame(out)


# ─────────────────────────────────────────────────────────────
# Laporan
# ─────────────────────────────────────────────────────────────

def print_metrics_block(title: str, m: dict):
    print(f"\n  {title}")
    if m["n"] == 0:
        print("    Tidak ada trade.")
        return
    pf_str = "inf (tidak ada loss)" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    print(f"    Jumlah trade      : {m['n']}")
    print(f"    Win rate          : {m['win_rate']*100:.1f}%")
    print(f"    Profit factor     : {pf_str}")
    print(f"    Ekspektasi/trade  : {m['expectancy_r']:+.3f} R")
    print(f"    Max drawdown      : {m['max_drawdown_r']:.2f} R")
    print(f"    Rata-rata bar/trd : {m['avg_bars_held']:.1f}")


def main():
    ap = argparse.ArgumentParser(description="Backtest walk-forward sistem skor (tanpa lookahead)")
    ap.add_argument("--symbols", default=None,
                    help="Batasi ke simbol tertentu, dipisah koma (mis. BTCUSDT,ETHUSDT)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--threads", action="store_true",
                    help="Pakai thread, bukan proses (lebih lambat untuk universe besar; "
                         "berguna untuk debug/traceback penuh)")
    ap.add_argument("--baseline-seed", type=int, default=42)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--history", action="store_true",
                    help="Pakai arsip panjang .cache_history/ (dari fetch_history.py), "
                         "bukan cache harian .cache/")
    args = ap.parse_args()

    symbols_filter = None
    if args.symbols:
        symbols_filter = [s.strip().upper() for s in args.symbols.split(",")]

    src = ".cache_history/*.parquet" if args.history else ".cache/*.parquet"
    print(f"-> Memuat universe dari {src} (tanpa download ulang) ...", flush=True)
    universe = load_universe(symbols_filter, use_history=args.history)
    if "BTCUSDT" not in universe:
        raise SystemExit(f"BTCUSDT tidak ada di sumber ({src}) — dibutuhkan untuk regime. "
                         + ("Jalankan fetch_history.py dulu." if args.history
                            else "Jalankan screener.py --mode daily dulu untuk mengisi cache."))
    print(f"   {len(universe)} simbol dimuat: {', '.join(sorted(universe))}")

    print("-> Membangun regime BTC walk-forward (sekali, dipakai semua simbol) ...", flush=True)
    btc_index, btc_regimes = build_btc_regime_lookup(universe["BTCUSDT"])
    _rdist = pd.Series([r["status"] for r in btc_regimes if r]).value_counts()
    print(f"   Sebaran regime BTC sepanjang sejarah: {_rdist.to_dict()}")
    if btc_index[-1].year - btc_index[BTC_REGIME_WARM].year >= 2 and len(_rdist) < 2:
        raise SystemExit("Regime BTC degenerate (satu status untuk sejarah bertahun-tahun) "
                         "— lookup tanggal kemungkinan rusak. Batalkan, periksa regime_at().")

    cfg = dict(scr.DEFAULT_CFG)          # TIDAK diubah — ukur apa adanya

    kind = "thread" if args.threads else "proses"
    print(f"-> Menjalankan backtest walk-forward ({args.workers} {kind}) ...", flush=True)
    all_rows: list[dict] = []
    all_errors: list[str] = []
    t_run = time.time()
    items = list(universe.items())
    if args.threads:
        pool = cf.ThreadPoolExecutor(max_workers=args.workers)
        _pool_init(btc_index, btc_regimes, cfg)
    else:
        pool = cf.ProcessPoolExecutor(max_workers=args.workers, initializer=_pool_init,
                                      initargs=(btc_index, btc_regimes, cfg))
    with pool as ex:
        for i, (sym, rows, errors) in enumerate(ex.map(_bt_one, items), 1):
            all_rows.extend(rows)
            all_errors.extend(errors)
            if i % 10 == 0 or i == len(items):
                print(f"   {_eta(i, len(items), t_run)}  ({sym}: {len(rows)} sinyal)", flush=True)

    trades = pd.DataFrame(all_rows)
    if all_errors:
        print(f"\n  Peringatan: {len(all_errors)} bar gagal dievaluasi (dilewati). Contoh:")
        for e in all_errors[:5]:
            print(f"    - {e}")

    filled = trades[trades["status"] == "filled"].copy() if not trades.empty else trades
    cancelled_n = int((trades["status"] == "cancelled").sum()) if not trades.empty else 0
    cf_n = int(trades["status"].str.endswith("_merah_cf").sum()) if not trades.empty else 0
    real_n = len(trades) - cf_n if not trades.empty else 0

    print("-> Menjalankan baseline (beli acak, mekanik exit sama) ...", flush=True)
    n_real_filled = len(filled)
    baseline_rows = run_baseline(universe, n_real_filled, args.baseline_seed, cfg)
    baseline_df = pd.DataFrame(baseline_rows)

    # ── Ekspor CSV ──
    trades_out = trades.copy()
    trades_out["source"] = "system"
    baseline_out = baseline_df.copy()
    if not baseline_out.empty:
        baseline_out["source"] = "baseline_random"
    combined = pd.concat([trades_out, baseline_out], ignore_index=True, sort=False)
    csv_path = os.path.join(args.outdir, "backtest_trades.csv")
    try:
        combined.to_csv(csv_path, index=False)
    except PermissionError:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(args.outdir, f"backtest_trades_{stamp}.csv")
        combined.to_csv(csv_path, index=False)
        print(f"\n  (backtest_trades.csv terkunci — mungkin terbuka di Excel. "
              f"Ditulis ke {os.path.basename(csv_path)} sebagai gantinya.)")

    # ── Laporan ──
    line = "=" * 96
    print("\n" + line)
    print(f"  HASIL BACKTEST WALK-FORWARD — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    src_label = ".cache_history (arsip panjang)" if args.history else ".cache (harian)"
    span_txt = ""
    if not filled.empty:
        sd = pd.to_datetime(filled["signal_date"])
        span_txt = f" | sinyal {sd.min().date()}..{sd.max().date()}"
    print(f"  Universe: {len(universe)} simbol dari {src_label}{span_txt} | warmup {WARMUP_BARS} bar | "
          f"entry window {ENTRY_WINDOW} bar | timeout {MAX_HOLD} bar | "
          f"fee {FEE*100:.2f}% + slip {SLIP*100:.2f}% per sisi")
    print(line)
    print(f"\n  Sinyal non-veto total : {real_n}")
    print(f"  Terisi (filled)       : {len(filled)}")
    print(f"  Batal (entry tak kena): {cancelled_n}")
    if cf_n:
        print(f"  (+ {cf_n} sinyal counterfactual MERAH — dianalisa terpisah di bawah)")

    min_score = cfg["min_score"]
    tradeable = filled[filled["total_score"] >= min_score].copy() if not filled.empty else filled

    m_sys = compute_metrics(filled) if not filled.empty else {"n": 0}
    m_trade = compute_metrics(tradeable) if not tradeable.empty else {"n": 0}
    m_base = compute_metrics(baseline_df) if not baseline_df.empty else {"n": 0}
    print_metrics_block("SEMUA SINYAL NON-VETO (evaluate, termasuk grade C < 60)", m_sys)
    print_metrics_block(f"HANYA YANG DIREKOMENDASIKAN (skor >= min_score {min_score})", m_trade)
    print_metrics_block("BASELINE ACAK (SL/TP & exit sama)", m_base)
    print("\n  Catatan: screener.py hanya menampilkan skor >= min_score. Blok 'SEMUA SINYAL'\n"
          "  di atas mencakup setup yang TIDAK akan pernah user ambil — dipakai hanya untuk\n"
          "  menjawab pertanyaan monotonisitas pita skor di bawah.")

    if m_trade["n"] and m_base["n"]:
        beats = m_trade["expectancy_r"] > m_base["expectancy_r"]
        print(f"\n  >> Setup yang direkomendasikan {'MENGALAHKAN' if beats else 'TIDAK mengalahkan'} "
              f"baseline acak ({m_trade['expectancy_r']:+.3f} R vs {m_base['expectancy_r']:+.3f} R).")
        if not beats:
            print("     Ini temuan penting — skor tidak menambah edge dibanding entry acak "
                  "dengan risk management yang sama.")
        if m_trade["n"] < MIN_BAND_SAMPLE:
            print(f"     (Hanya {m_trade['n']} trade direkomendasikan — di bawah {MIN_BAND_SAMPLE}, "
                  "belum bisa disebut konklusif.)")

    # ── Ambang skor 70 & skala skor (Tugas 3a/3b) ──
    print("\n" + line)
    print("  AMBANG SKOR & SKALA — apakah min_score 70 bisa divalidasi?")
    print(line)
    if not filled.empty:
        n_ge70 = int((filled["total_score"] >= 70).sum())
        n_ge80 = int((filled["total_score"] >= 80).sum())
        max_score = int(filled["total_score"].max())
        p95 = float(filled["total_score"].quantile(0.95))
        print(f"  Sinyal terisi berskor >= 70 : {n_ge70}  (>= 80: {n_ge80})")
        print(f"  Skor maksimum tercapai      : {max_score}   (persentil-95: {p95:.0f})")
        if n_ge70 < MIN_BAND_SAMPLE:
            print(f"\n  -> AMBANG 70 TIDAK BISA DIVALIDASI. Hanya {n_ge70} sinyal (< {MIN_BAND_SAMPLE}) "
                  "yang pernah lolos.\n"
                  "     Sistem skor jarang mencapai 70; sampel di grade tradeable terlalu kecil\n"
                  "     untuk mengukur win rate/ekspektasi dengan keyakinan apa pun.")
        else:
            print(f"\n  -> {n_ge70} sinyal berskor >= 70 — cukup untuk dinilai (lihat pita 70-79 & 80+).")
        if max_score < 80:
            print(f"\n  -> MASALAH DESAIN SKALA: skor tertinggi yang PERNAH dicapai hanya {max_score}.\n"
                  "     Grade A+ (>=80) secara praktis tidak terjangkau — 15 poin teratas skala\n"
                  "     (mis. Pattern 15 + sebagian Volume) hampir tak pernah menyala bersamaan.\n"
                  "     Bobot/threshold yang mengacu ke pita 80+ mengatur wilayah yang kosong.")

    print("\n" + line)
    print("  PECAHAN PER PITA SKOR (pertanyaan utama: apakah monoton naik?)")
    print(line)
    if not filled.empty:
        band = score_band_breakdown(filled)
        print(f"  {'PITA':<8}{'N':>6}{'WIN RATE':>12}{'EKSPEKTASI':>14}")
        for _, r in band.iterrows():
            wr = "-" if r["win_rate"] is None else f"{r['win_rate']*100:.1f}%"
            ex = "-" if r["expectancy_r"] is None else f"{r['expectancy_r']:+.3f} R"
            print(f"  {r['pita']:<8}{r['n']:>6}{wr:>12}{ex:>14}")

        main_bands = band[band["pita"].isin(["60-69", "70-79", "80-89", "90+"])]
        pairs = [(r["pita"], r["expectancy_r"], r["n"]) for _, r in main_bands.iterrows()
                 if r["expectancy_r"] is not None]
        thin = [p[0] for p in pairs if p[2] < MIN_BAND_SAMPLE]
        exps = [p[1] for p in pairs]
        monotonic = all(exps[i] <= exps[i + 1] for i in range(len(exps) - 1)) if len(exps) >= 2 else None

        if thin:
            print(f"\n  PERINGATAN SAMPEL: pita {', '.join(thin)} punya < {MIN_BAND_SAMPLE} trade. "
                  "Angka win rate/ekspektasi di pita itu TIDAK cukup untuk diklaim prediktif.")
        if monotonic is None:
            print("  Data tidak cukup di seluruh pita untuk menilai monotonisitas.")
        elif monotonic:
            print("  Ekspektasi NAIK MONOTON seiring skor lebih tinggi — konsisten dengan desain sistem"
                  + (", TAPI lihat peringatan sampel di atas." if thin else "."))
        else:
            print("  TIDAK MONOTON — skor lebih tinggi TIDAK selalu berarti ekspektasi lebih tinggi. "
                  "Lihat tabel di atas.")
    else:
        print("  Tidak ada trade terisi untuk dianalisa.")

    # ── Pita R:R rencana (Tugas 3c) ──
    print("\n" + line)
    print("  PECAHAN PER PITA R:R RENCANA — apakah R:R lebar tetap lebih buruk?")
    print(line)
    if not filled.empty:
        rrb = rr_band_breakdown(filled)
        print(f"  {'PITA R:R':<10}{'N':>6}{'WIN RATE':>12}{'EKSPEKTASI':>14}")
        for _, r in rrb.iterrows():
            wr = "-" if r["win_rate"] is None else f"{r['win_rate']*100:.1f}%"
            ex = "-" if r["expectancy_r"] is None else f"{r['expectancy_r']:+.3f} R"
            print(f"  {r['pita_rr']:<10}{r['n']:>6}{wr:>12}{ex:>14}")
        vals = [(r["pita_rr"], r["expectancy_r"]) for _, r in rrb.iterrows()
                if r["expectancy_r"] is not None]
        if len(vals) >= 3:
            narrow = dict(vals).get("0-3")
            wide = dict(vals).get("5-8")
            if narrow is not None and wide is not None:
                if narrow > wide:
                    print(f"\n  -> Temuan backtest v1 BERTAHAN: pita 0-3 ({narrow:+.3f} R) > pita 5-8 "
                          f"({wide:+.3f} R). R:R lebar tidak menambah edge.")
                else:
                    print(f"\n  -> Temuan v1 TIDAK bertahan di data ini: pita 5-8 ({wide:+.3f} R) "
                          f">= pita 0-3 ({narrow:+.3f} R). Tinjau ulang max_plausible_rr.")
    else:
        print("  Tidak ada trade terisi untuk dianalisa.")

    # ── Bull vs bear & veto BTC MERAH (Tugas 3d) ──
    print("\n" + line)
    print("  PERIODE BULL vs BEAR — apakah veto BTC MERAH menyelamatkan modal?")
    print(line)
    if not trades.empty:
        f2 = filled.copy()
        if not f2.empty:
            f2["year"] = pd.to_datetime(f2["signal_date"]).dt.year
            print("  Trade nyata (HIJAU/KUNING saja — MERAH sudah di-veto) per regime saat sinyal:")
            for st in ("HIJAU", "KUNING"):
                s = f2[f2["regime_status"] == st]
                if len(s):
                    print(f"    {st:<7} n={len(s):>4}  win {(s['pnl_r']>0).mean()*100:>5.1f}%  "
                          f"E[R] {s['pnl_r'].mean():+.3f}")
            print("\n  Per tahun kalender:")
            for y in sorted(f2["year"].unique()):
                s = f2[f2["year"] == y]
                print(f"    {y}   n={len(s):>4}  win {(s['pnl_r']>0).mean()*100:>5.1f}%  "
                      f"E[R] {s['pnl_r'].mean():+.3f}")

        cf_rows = trades[trades["status"] == "filled_merah_cf"]
        print("\n  COUNTERFACTUAL — setup yang HANYA di-veto karena BTC MERAH, disimulasikan tetap:")
        if cf_rows.empty:
            print("    Tidak ada (tidak pernah ada periode MERAH di rentang data, atau setiap\n"
                  "    setup MERAH juga kena veto lain). Veto BTC MERAH tak teruji di data ini.")
        else:
            er = float(cf_rows["pnl_r"].mean())
            tot = float(cf_rows["pnl_r"].sum())
            wr = float((cf_rows["pnl_r"] > 0).mean())
            print(f"    n={len(cf_rows)}  win {wr*100:.1f}%  E[R] {er:+.3f}  total {tot:+.1f} R")
            base_er = m_base["expectancy_r"] if m_base["n"] else 0.0
            if er < 0:
                print(f"    -> Veto BTC MERAH MENYELAMATKAN modal: setup-setup itu rugi rata-rata "
                      f"{er:+.3f} R.")
            elif er > base_er:
                print(f"    -> Veto BTC MERAH JUSTRU MEMBUANG PROFIT: setup-setup itu untung "
                      f"{er:+.3f} R (> baseline {base_er:+.3f} R). Pertimbangkan melonggarkan\n"
                      "       veto jadi size-down, bukan blokir total — TAPI cek jumlah sampel dulu.")
            else:
                print(f"    -> Netral: E[R] {er:+.3f} ~ baseline {base_er:+.3f}. Veto tidak "
                      "menolong maupun merugikan secara berarti.")
            if len(cf_rows) < MIN_BAND_SAMPLE:
                print(f"    (n={len(cf_rows)} < {MIN_BAND_SAMPLE} — indikatif, belum konklusif.)")

    print("\n" + line)
    print("  KORELASI KOMPONEN SKOR TERHADAP HASIL TRADE")
    print(line)
    if not filled.empty:
        corr = component_correlation(filled)
        print(f"  {'KOMPONEN':<14}{'CORR vs R':>12}{'CORR vs WIN':>14}")
        for _, r in corr.iterrows():
            cr = "-" if r["corr_pnl_r"] is None else f"{r['corr_pnl_r']:+.3f}"
            cw = "-" if r["corr_win"] is None else f"{r['corr_win']:+.3f}"
            print(f"  {r['komponen']:<14}{cr:>12}{cw:>14}")
        print(f"\n  (n = {len(filled)} trade. Korelasi |r| < ~0.1 praktis nol; "
              "komponen dengan varian rendah — banyak trade berskor sama — tidak akan "
              "menunjukkan korelasi walau sebenarnya berguna.)")

        print("\n  SKOR TOTAL vs HASIL (jawaban langsung 'skor tinggi = ekspektasi tinggi?'):")
        if filled["total_score"].nunique() >= 2:
            pear = float(filled["total_score"].corr(filled["pnl_r"]))
            spear = float(filled["total_score"].corr(filled["pnl_r"], method="spearman"))
            print(f"    Pearson  skor vs R : {pear:+.3f}")
            print(f"    Spearman skor vs R : {spear:+.3f}  (peringkat — lebih tahan outlier)")
            if abs(pear) < 0.1 and abs(spear) < 0.1:
                print("    -> Praktis NOL. Skor total tidak memisahkan trade menang dari kalah "
                      "di data ini.")
    else:
        print("  Tidak ada trade terisi untuk dianalisa.")

    print("\n" + line)
    print("  KETAHANAN — apakah ekspektasi bergantung pada segelintir trade?")
    print(line)
    if not filled.empty and len(filled) > 10:
        k = 5
        srt = filled["pnl_r"].sort_values(ascending=False)
        top_sum = float(srt.head(k).sum())
        tot_sum = float(filled["pnl_r"].sum())
        rest_mean = (tot_sum - top_sum) / (len(filled) - k)
        share = (top_sum / tot_sum) if tot_sum != 0 else float("nan")
        print(f"  Total P&L                : {tot_sum:+.1f} R dari {len(filled)} trade")
        print(f"  Kontribusi {k} winner teratas: {top_sum:+.1f} R ({share*100:.0f}% dari total)")
        base_note = f"  (baseline acak: {m_base['expectancy_r']:+.3f} R)" if m_base["n"] else ""
        print(f"  Ekspektasi tanpa {k} itu    : {rest_mean:+.3f} R/trade{base_note}")
        if m_base["n"] and abs(rest_mean - m_base["expectancy_r"]) < 0.05:
            print("  -> Tanpa tail itu, sistem = baseline acak. 'Edge' bertumpu pada fat tail, "
                  "bukan pada seleksi skor.")
        n_wide = int((filled["plan_rr1"] > 8).sum())
        n_badtp = int((filled["plan_tp1"] >= filled["plan_tp2"]).sum())
        print(f"\n  R:R rencana > 1:8        : {n_wide}/{len(filled)} "
              + ("(veto max_plausible_rr=8 bekerja)" if n_wide == 0
                 else f"({n_wide/len(filled)*100:.0f}%) — harusnya 0 dgn veto 8; periksa"))
        if n_badtp:
            print(f"  Rencana dgn TP1 >= TP2   : {n_badtp} — REGRESI: bug ini seharusnya sudah "
                  "diperbaiki di build_trade_plan(). Laporkan.")

    print("\n" + line)
    print(f"  Diekspor: {csv_path}")
    print("  Ingat: ini alat ukur. Kalau hasilnya jelek, itu jawaban yang benar — "
          "jangan disetel ulang supaya terlihat bagus.")
    print(line + "\n")


if __name__ == "__main__":
    main()
