#!/usr/bin/env python3
"""
backtest.py — Walk-forward backtest sistem skor 100 poin (mode daily/weekly).

Alat ukur, bukan alat untuk membuat angkanya bagus. Tidak menyetel scoring.py,
indicators.py, atau accumulation.py — hanya membaca lewat evaluate().

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

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

# ── Parameter tetap sesuai spesifikasi (bukan parameter yang "dioprek" agar
#    hasil terlihat bagus — ini definisi mekanik backtest itu sendiri) ──
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

def load_universe(symbols_filter: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Muat file .cache/*_1d_*.parquet, satu dataframe per simbol (stamp terbaru)."""
    files = glob.glob(os.path.join(CACHE_DIR, "*_1d_*.parquet"))
    latest: dict[str, str] = {}
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
    dates = btc_df.index.values
    regimes: list[dict | None] = [None] * len(btc_df)
    for t in range(BTC_REGIME_WARM, len(btc_df)):
        regimes[t] = scoring.btc_regime(btc_df.iloc[:t + 1])
    return dates, regimes


def regime_at(btc_dates, btc_regimes, date) -> dict | None:
    idx = int(np.searchsorted(btc_dates, np.datetime64(date), side="right")) - 1
    if idx < BTC_REGIME_WARM or idx >= len(btc_regimes):
        return None
    return btc_regimes[idx]


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
# ─────────────────────────────────────────────────────────────

def backtest_symbol(symbol: str, df: pd.DataFrame, btc_dates, btc_regimes,
                    cfg: dict) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    n = len(df)
    t = WARMUP_BARS
    while t <= n - 1 - MIN_FORWARD:
        bias = df.iloc[:t + 1]                      # ATURAN 1: potongan asli sampai bar t
        date_t = df.index[t]
        regime = regime_at(btc_dates, btc_regimes, date_t)   # ATURAN 2
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

        if result is None or result["vetoed"]:
            t += 1
            continue

        plan = result["plan"]
        fill_idx = try_fill(df, t, plan["entry"])     # ATURAN 3: paling cepat bar t+1
        if fill_idx is None:
            rows.append({
                "symbol": symbol, "signal_date": date_t, "status": "cancelled",
                "total_score": result["total"], "grade": result["grade"],
                "s_volume": result["s_volume"], "s_stochrsi": result["s_stochrsi"],
                "s_fib": result["s_fib"], "s_sr": result["s_sr"],
                "s_pattern": result["s_pattern"],
                "plan_entry": plan["entry"], "plan_sl": plan["sl"],
                "plan_tp1": plan["tp1"], "plan_tp2": plan["tp2"],
                "plan_rr1": plan["rr1"],
                "fill_date": None, "fill_price": None, "exit_date": None,
                "exit_type": None, "bars_held": None, "pnl_r": None, "win": None,
            })
            t = min(t + ENTRY_WINDOW, n - 1) + 1
            continue

        legs, exit_idx, outcome = simulate_exit(df, fill_idx, plan["entry"],
                                                plan["sl"], plan["tp1"], plan["tp2"])
        pnl_r = cost_adjusted_pnl_r(plan["entry"], plan["sl"], legs)
        rows.append({
            "symbol": symbol, "signal_date": date_t, "status": "filled",
            "total_score": result["total"], "grade": result["grade"],
            "s_volume": result["s_volume"], "s_stochrsi": result["s_stochrsi"],
            "s_fib": result["s_fib"], "s_sr": result["s_sr"],
            "s_pattern": result["s_pattern"],
            "plan_entry": plan["entry"], "plan_sl": plan["sl"],
            "plan_tp1": plan["tp1"], "plan_tp2": plan["tp2"],
            "plan_rr1": plan["rr1"],
            "fill_date": df.index[fill_idx], "fill_price": plan["entry"],
            "exit_date": df.index[exit_idx], "exit_type": outcome,
            "bars_held": exit_idx - fill_idx,
            "pnl_r": pnl_r, "win": pnl_r > 0,
        })
        t = exit_idx + 1               # satu posisi per simbol dalam satu waktu
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
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--baseline-seed", type=int, default=42)
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    symbols_filter = None
    if args.symbols:
        symbols_filter = [s.strip().upper() for s in args.symbols.split(",")]

    print("-> Memuat universe dari .cache/*.parquet (tanpa download ulang) ...", flush=True)
    universe = load_universe(symbols_filter)
    if "BTCUSDT" not in universe:
        raise SystemExit("BTCUSDT tidak ada di .cache — dibutuhkan untuk regime. "
                         "Jalankan screener.py --mode daily dulu untuk mengisi cache.")
    print(f"   {len(universe)} simbol dimuat: {', '.join(sorted(universe))}")

    print("-> Membangun regime BTC walk-forward (sekali, dipakai semua simbol) ...", flush=True)
    btc_dates, btc_regimes = build_btc_regime_lookup(universe["BTCUSDT"])

    cfg = dict(scr.DEFAULT_CFG)          # TIDAK diubah — ukur apa adanya

    print(f"-> Menjalankan backtest walk-forward ({args.workers} worker) ...", flush=True)
    all_rows: list[dict] = []
    all_errors: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(backtest_symbol, sym, df, btc_dates, btc_regimes, cfg): sym
                for sym, df in universe.items()}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            sym = futs[fut]
            rows, errors = fut.result()
            all_rows.extend(rows)
            all_errors.extend(errors)
            print(f"   [{i}/{len(universe)}] {sym}: {len(rows)} sinyal", flush=True)

    trades = pd.DataFrame(all_rows)
    if all_errors:
        print(f"\n  Peringatan: {len(all_errors)} bar gagal dievaluasi (dilewati). Contoh:")
        for e in all_errors[:5]:
            print(f"    - {e}")

    filled = trades[trades["status"] == "filled"].copy() if not trades.empty else trades
    cancelled_n = int((trades["status"] == "cancelled").sum()) if not trades.empty else 0

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
    combined.to_csv(csv_path, index=False)

    # ── Laporan ──
    line = "=" * 96
    print("\n" + line)
    print(f"  HASIL BACKTEST WALK-FORWARD — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Universe: {len(universe)} simbol dari cache lokal | warmup {WARMUP_BARS} bar | "
          f"entry window {ENTRY_WINDOW} bar | timeout {MAX_HOLD} bar | "
          f"fee {FEE*100:.2f}% + slip {SLIP*100:.2f}% per sisi")
    print(line)
    print(f"\n  Sinyal non-veto total : {len(trades)}")
    print(f"  Terisi (filled)       : {len(filled)}")
    print(f"  Batal (entry tak kena): {cancelled_n}")

    m_sys = compute_metrics(filled) if not filled.empty else {"n": 0}
    m_base = compute_metrics(baseline_df) if not baseline_df.empty else {"n": 0}
    print_metrics_block("SISTEM SKOR (evaluate)", m_sys)
    print_metrics_block("BASELINE ACAK (SL/TP & exit sama)", m_base)

    if m_sys["n"] and m_base["n"]:
        beats = m_sys["expectancy_r"] > m_base["expectancy_r"]
        print(f"\n  >> Sistem {'MENGALAHKAN' if beats else 'TIDAK mengalahkan'} baseline acak "
              f"({m_sys['expectancy_r']:+.3f} R vs {m_base['expectancy_r']:+.3f} R).")
        if not beats:
            print("     Ini temuan penting — skor tidak menambah edge dibanding entry acak "
                  "dengan risk management yang sama.")

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
        exps = [e for e in main_bands["expectancy_r"] if e is not None]
        monotonic = all(exps[i] <= exps[i + 1] for i in range(len(exps) - 1)) if len(exps) >= 2 else None
        if monotonic is None:
            print("\n  Data tidak cukup di seluruh pita untuk menilai monotonisitas.")
        elif monotonic:
            print("\n  Ekspektasi NAIK MONOTON seiring skor lebih tinggi — konsisten dengan desain sistem.")
        else:
            print("\n  TIDAK MONOTON — skor lebih tinggi TIDAK selalu berarti ekspektasi lebih tinggi. "
                  "Lihat tabel di atas.")
    else:
        print("  Tidak ada trade terisi untuk dianalisa.")

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
    else:
        print("  Tidak ada trade terisi untuk dianalisa.")

    print("\n" + line)
    print(f"  Diekspor: {csv_path}")
    print("  Ingat: ini alat ukur. Kalau hasilnya jelek, itu jawaban yang benar — "
          "jangan disetel ulang supaya terlihat bagus.")
    print(line + "\n")


if __name__ == "__main__":
    main()
