#!/usr/bin/env python3
"""
mechanics_test.py — Uji MEKANIK TRADE terpisah dari SELEKSI.

Semua uji sebelumnya menguji seleksi (skor komposit, faktor tunggal). Yang belum
pernah diisolasi: manajemen posisi. 75% sinyal sistem kena SL penuh −1.10R.

Di sini ENTRY dibuat ACAK (bukan sinyal sistem) supaya seleksi dinetralkan
sepenuhnya. Universe & periode sama (.cache_history/, 2021–2026). Enam varian
mekanik, masing-masing dijalankan pada SET ENTRY ACAK YANG SAMA (seed tetap),
minimal 5.000 trade.

  A. Baseline: SL struktur, TP1 2R (keluar 50%) + SL ke break-even, TP2 4R, timeout 30
  B. = A tanpa pemindahan SL ke BE
  C. SL 3x ATR (lebih lebar), sisanya = A
  D. Trailing stop murni (chandelier 3x ATR), tanpa TP tetap, timeout 30
  E. = A, timeout 90 (bukan 30)
  F. Keluar PENUH di TP1 2R (tanpa runner, tanpa BE)

Biaya: fee 0.1% + slippage 0.05% per sisi (dari backtest.py). SL menang kalau
satu bar menyentuh SL dan TP sekaligus (konservatif).

    python mechanics_test.py
    python mechanics_test.py --n 8000 --seed 20260904

TIDAK menguji kombinasi mekanik. TIDAK menyetel parameter. Alat ukur.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as ta          # noqa: E402
import backtest as bt            # noqa: E402  (FEE/SLIP, cost_adjusted_pnl_r, loader, _eta)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()

TIMEOUT_MAX = 90          # varian E; entry butuh >= TIMEOUT_MAX+2 bar ke depan
FWD_MARGIN = TIMEOUT_MAX + 2
DEFAULT_N = 8000         # >= 5000 diminta; margin untuk trade yang ke-skip
ATR_MULT_WIDE = 3.0
TP1_R = 2.0
TP2_R = 4.0

VARIANTS = ["A", "B", "C", "D", "E", "F"]
VARIANT_DESC = {
    "A": "baseline: SL struktur, TP1 2R (50%) + BE, TP2 4R, timeout 30",
    "B": "= A, tanpa SL ke break-even",
    "C": "SL 3x ATR (lebih lebar), sisanya = A",
    "D": "trailing chandelier 3x ATR, tanpa TP tetap, timeout 30",
    "E": "= A, timeout 90",
    "F": "keluar penuh di TP1 2R (tanpa runner, tanpa BE)",
}


# ─────────────────────────────────────────────────────────────
# Stop loss & simulator
# ─────────────────────────────────────────────────────────────

def struct_sl(df: pd.DataFrame, i0: int, entry: float, atr: float) -> float:
    """SL struktur: low 10 bar terakhir * 0.985; kalau tak masuk akal -> 2x ATR."""
    lo10 = float(df["low"].iloc[max(0, i0 - 9):i0 + 1].min())
    sl = lo10 * 0.985
    if sl >= entry * 0.995 or (entry - sl) / entry > 0.15:
        sl = entry - 2.0 * atr
    return sl


def simulate(df: pd.DataFrame, i0: int, entry: float, sl0: float, atr0: float, *,
             tp1: float | None, tp2: float | None, be: bool, single_tp: bool,
             timeout: int, trailing: bool):
    """Return (legs, outcome_tag). legs = [(bar_idx, price, weight, tag)]."""
    n = len(df)
    end = min(i0 + timeout, n - 1)
    cur_sl = sl0
    tp1_hit = False
    hh = entry
    legs: list[tuple] = []

    for j in range(i0 + 1, end + 1):
        lo = float(df["low"].iloc[j])
        hi = float(df["high"].iloc[j])

        if trailing:
            hh = max(hh, hi)
            cur_sl = max(cur_sl, hh - ATR_MULT_WIDE * atr0)
            if lo <= cur_sl:
                legs.append((j, cur_sl, 1.0, "TRAIL_SL"))
                return legs, "TRAIL_SL"
            continue

        if not tp1_hit:
            if lo <= cur_sl:                       # SL duluan kalau dua-duanya kena
                legs.append((j, cur_sl, 1.0, "SL"))
                return legs, "SL"
            if hi >= tp1:
                if single_tp:
                    legs.append((j, tp1, 1.0, "TP1_FULL"))
                    return legs, "TP1_FULL"
                legs.append((j, tp1, 0.5, "TP1"))
                tp1_hit = True
                if be:
                    cur_sl = entry
                continue
        else:
            if lo <= cur_sl:
                legs.append((j, cur_sl, 0.5, "STOP2"))
                return legs, ("TP1_BE" if be else "TP1_SL")
            if hi >= tp2:
                legs.append((j, tp2, 0.5, "TP2"))
                return legs, "TP1_TP2"

    close_p = float(df["close"].iloc[end])
    if trailing or not tp1_hit:
        legs.append((end, close_p, 1.0, "TIMEOUT"))
        return legs, "TIMEOUT"
    legs.append((end, close_p, 0.5, "TIMEOUT"))
    return legs, "TP1_TIMEOUT"


def run_all_variants(df: pd.DataFrame, i0: int) -> dict[str, float] | None:
    entry = float(df["close"].iloc[i0])
    if entry <= 0:
        return None
    atr = float(ta.atr(df.iloc[:i0 + 1]).iloc[-1])
    if not np.isfinite(atr) or atr <= 0:
        return None

    sl_s = struct_sl(df, i0, entry, atr)
    sl_w = entry - ATR_MULT_WIDE * atr
    if sl_s >= entry or sl_w >= entry:
        return None

    r_s = entry - sl_s
    r_w = entry - sl_w
    out: dict[str, float] = {}

    def pnl(sl, legs):
        return bt.cost_adjusted_pnl_r(entry, sl, legs)

    # A
    legs, _ = simulate(df, i0, entry, sl_s, atr, tp1=entry + TP1_R * r_s,
                       tp2=entry + TP2_R * r_s, be=True, single_tp=False,
                       timeout=30, trailing=False)
    out["A"] = pnl(sl_s, legs)
    # B
    legs, _ = simulate(df, i0, entry, sl_s, atr, tp1=entry + TP1_R * r_s,
                       tp2=entry + TP2_R * r_s, be=False, single_tp=False,
                       timeout=30, trailing=False)
    out["B"] = pnl(sl_s, legs)
    # C
    legs, _ = simulate(df, i0, entry, sl_w, atr, tp1=entry + TP1_R * r_w,
                       tp2=entry + TP2_R * r_w, be=True, single_tp=False,
                       timeout=30, trailing=False)
    out["C"] = pnl(sl_w, legs)
    # D
    legs, _ = simulate(df, i0, entry, sl_w, atr, tp1=None, tp2=None, be=False,
                       single_tp=False, timeout=30, trailing=True)
    out["D"] = pnl(sl_w, legs)
    # E
    legs, _ = simulate(df, i0, entry, sl_s, atr, tp1=entry + TP1_R * r_s,
                       tp2=entry + TP2_R * r_s, be=True, single_tp=False,
                       timeout=90, trailing=False)
    out["E"] = pnl(sl_s, legs)
    # F
    legs, _ = simulate(df, i0, entry, sl_s, atr, tp1=entry + TP1_R * r_s,
                       tp2=None, be=False, single_tp=True, timeout=30, trailing=False)
    out["F"] = pnl(sl_s, legs)
    return out


# ─────────────────────────────────────────────────────────────
# Sampling entry acak + eksekusi paralel per simbol
# ─────────────────────────────────────────────────────────────

_G = {}


def _pool_init(entries_by_sym, uni=None):
    # worker proses memuat universe sendiri (hindari pickle ratusan DataFrame);
    # mode thread mengoper uni langsung.
    _G["e"] = entries_by_sym
    _G["u"] = uni if uni is not None else bt.load_universe(use_history=True)


def _one(sym):
    df = _G["u"][sym]
    rows = []
    for i0 in _G["e"][sym]:
        res = run_all_variants(df, i0)
        if res is None:
            continue
        row = {"symbol": sym, "entry_date": df.index[i0], "year": df.index[i0].year}
        row.update(res)
        rows.append(row)
    return rows


def sample_entries(uni: dict[str, pd.DataFrame], n: int, seed: int) -> dict[str, list[int]]:
    pool: list[tuple[str, int]] = []
    for sym, df in uni.items():
        lo, hi = bt.WARMUP_BARS, len(df) - FWD_MARGIN
        if hi <= lo:
            continue
        pool.extend((sym, t) for t in range(lo, hi))
    rng = np.random.default_rng(seed)
    n = min(n, len(pool))
    pick = rng.choice(len(pool), size=n, replace=False)
    by_sym: dict[str, list[int]] = {}
    for k in pick:
        s, t = pool[int(k)]
        by_sym.setdefault(s, []).append(t)
    return by_sym


# ─────────────────────────────────────────────────────────────
# Metrik
# ─────────────────────────────────────────────────────────────

def metrics(pnl: pd.Series) -> dict:
    pnl = pnl.dropna()
    n = len(pnl)
    if n == 0:
        return {"n": 0}
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gw = float(wins.sum())
    gl = float(abs(losses.sum()))
    pf = gw / gl if gl > 0 else float("inf")
    srt = pnl.sort_values(ascending=False).to_numpy()
    no5 = float((srt.sum() - srt[:5].sum()) / (n - 5)) if n > 6 else float("nan")
    return {"n": n, "win": float((pnl > 0).mean()), "E_R": float(pnl.mean()),
            "pf": pf, "E_R_no5": no5, "median": float(pnl.median())}


def fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Uji mekanik trade dengan entry acak")
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="jumlah entry acak (>= 5000)")
    ap.add_argument("--seed", type=int, default=20260904)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--threads", action="store_true")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    print("-> Memuat universe .cache_history/ ...", flush=True)
    uni = bt.load_universe(use_history=True)
    print(f"   {len(uni)} simbol")

    print(f"-> Sampling {args.n} entry acak (seed {args.seed}) ...", flush=True)
    by_sym = sample_entries(uni, args.n, args.seed)
    total = sum(len(v) for v in by_sym.values())
    print(f"   {total} entry di {len(by_sym)} simbol")

    print(f"-> Menjalankan 6 varian mekanik ({args.workers} "
          f"{'thread' if args.threads else 'proses'}) ...", flush=True)
    t0 = time.time()
    syms = sorted(by_sym)
    all_rows = []
    if args.threads:
        _pool_init(by_sym, uni)
        pool = cf.ThreadPoolExecutor(max_workers=args.workers)
    else:
        pool = cf.ProcessPoolExecutor(max_workers=args.workers, initializer=_pool_init,
                                      initargs=(by_sym,))
    with pool as ex:
        for i, rows in enumerate(ex.map(_one, syms), 1):
            all_rows.extend(rows)
            if i % 30 == 0 or i == len(syms):
                print(f"   {bt._eta(i, len(syms), t0)}  ({len(all_rows)} trade)", flush=True)

    tr = pd.DataFrame(all_rows)
    if tr.empty:
        raise SystemExit("Tidak ada trade.")
    csv_path = os.path.join(args.outdir, "mechanics_test_trades.csv")
    try:
        tr.to_csv(csv_path, index=False)
    except PermissionError:
        csv_path = os.path.join(args.outdir,
                                f"mechanics_test_trades_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv")
        tr.to_csv(csv_path, index=False)

    span = f"{pd.to_datetime(tr['entry_date']).min().date()}..{pd.to_datetime(tr['entry_date']).max().date()}"
    line = "=" * 100
    print("\n" + line)
    print(f"  UJI MEKANIK TRADE — ENTRY ACAK — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print(f"  {len(tr)} trade | {tr['symbol'].nunique()} simbol | {span} | "
          f"fee {bt.FEE*100:.2f}% + slip {bt.SLIP*100:.2f}% per sisi | seed {args.seed}")
    print("  Entry = close bar acak; eksekusi mulai bar berikutnya. R = (entry − SL awal varian).")
    print(line)

    print(f"\n  {'VAR':<4}{'n':>7}{'win':>8}{'E[R]':>9}{'PF':>7}{'E[R] tanpa 5 terbaik':>22}{'median':>9}")
    agg = {}
    for v in VARIANTS:
        m = metrics(tr[v])
        agg[v] = m
        print(f"  {v:<4}{m['n']:>7}{m['win']*100:>7.1f}%{m['E_R']:>+9.3f}{fmt_pf(m['pf']):>7}"
              f"{m['E_R_no5']:>+22.3f}{m['median']:>+9.3f}   {VARIANT_DESC[v]}")

    print("\n" + line)
    print("  PECAHAN PER TAHUN — E[R] per varian")
    print(line)
    years = sorted(tr["year"].unique())
    print(f"  {'tahun':<7}{'n':>7}" + "".join(f"{v:>9}" for v in VARIANTS))
    for y in years:
        s = tr[tr["year"] == y]
        cells = "".join(f"{s[v].mean():>+9.3f}" for v in VARIANTS)
        print(f"  {y:<7}{len(s):>7}{cells}")

    print("\n" + line)
    print("  JAWABAN")
    print(line)
    pos_robust = [v for v in VARIANTS
                  if agg[v]["E_R"] > 0 and not np.isnan(agg[v]["E_R_no5"]) and agg[v]["E_R_no5"] > 0]
    pos_only = [v for v in VARIANTS if agg[v]["E_R"] > 0 and v not in pos_robust]
    if not any(agg[v]["E_R"] > 0 for v in VARIANTS):
        print("  TIDAK ADA varian mekanik yang menghasilkan ekspektasi positif dengan entry acak.")
        print("  Masalahnya bukan cuma seleksi — manajemen posisi long-only di universe ini juga\n"
              "  tidak menghasilkan edge. Tidak ada yang bisa diselamatkan dari pendekatan ini.")
    elif not pos_robust:
        print(f"  Varian positif: {', '.join(pos_only)} — TAPI semuanya jatuh ke <= 0 setelah\n"
              "  membuang 5 trade terbaik. Ekspektasi positifnya bertumpu pada ekor, bukan mekanik.\n"
              "  Tidak ada mekanik yang bertahan. Berhenti.")
    else:
        print(f"  Varian dengan E[R] positif DAN bertahan tanpa 5 trade terbaik: {', '.join(pos_robust)}")
        print("  Ini menunjukkan masalahnya (sebagian) manajemen posisi, bukan hanya seleksi.\n"
              "  Verifikasi manual + out-of-sample sebelum kesimpulan apa pun. 'Positif di entry\n"
              "  acak' menarik tapi bisa jadi artefak periode 2023–2024.")

    print(f"\n  Diekspor: {csv_path}")
    print("  Alat ukur. Enam varian, satu-satu, tanpa penyetelan. Hasil nol = hasil yang sah.")
    print(line + "\n")


if __name__ == "__main__":
    main()
