#!/usr/bin/env python3
"""
factor_test.py — Uji tiap faktor MENTAH satu per satu (bukan sebagai komposit).

Protokol & hipotesis dipra-registrasi di HIPOTESIS_FAKTOR.md (di-commit sebelum
run ini). Skor komposit `scoring.py` sudah terbukti gagal — di sini tidak disetel
dan tidak diubah. Ini murni pengukuran.

Alur:
  1. Konteks: return buy-and-hold rata-rata/median per koin (dilaporkan DULU).
  2. Walk-forward tanpa lookahead: setiap sinyal non-veto dari `evaluate()` dicatat
     bersama 9 nilai faktor mentah pada bar sinyal (df.iloc[:t+1]) dan pnl_r dari
     mekanik eksekusi yang sama dengan backtest.py.
  3. Untuk tiap faktor: bagi SELURUH sinyal jadi 5 kuintil berdasarkan nilai
     faktor saja (skor total diabaikan), bandingkan ekspektasi antar kuintil.
  4. Kriteria lulus (HIPOTESIS_FAKTOR.md): monoton atau |Q5-Q1|>0.15R, Spearman
     |>=0.10|, n>=500/kuintil, arah stabil 2021-23 vs 2024-26.

    python factor_test.py
    python factor_test.py --symbols BTCUSDT,ETHUSDT --threads

Ekspor: factor_test_signals.csv
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
import scoring                   # noqa: E402
import screener as scr           # noqa: E402
import backtest as bt            # noqa: E402  (mekanik eksekusi + loader, dipakai apa adanya)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()

MIN_PER_QUINTILE = 500        # HIPOTESIS_FAKTOR.md kriteria 3
MIN_SPEARMAN = 0.10           # kriteria 2
MIN_Q5_Q1 = 0.15             # kriteria 1 (R)
SPLIT_YEAR = 2024            # 2021-2023 vs 2024-2026 (kriteria 4)

FACTORS = [
    ("vol_ratio",           "volume[-1] / rata2 volume 20 bar sebelumnya"),
    ("obv_slope",           "kemiringan % OBV atas 20 bar (slope_pct)"),
    ("stochrsi_k",          "Stoch RSI %K harian (0-100)"),
    ("stochrsi_htf",        "Stoch RSI %K mingguan (0-100)"),
    ("fib_retr",            "retracement thd impuls naik terakhir (0-1+)"),
    ("dist_to_res_pct",     "(resistance terdekat - harga)/harga * 100"),
    ("dist_ema50_pct",      "(harga - EMA50)/EMA50 * 100"),
    ("atr_pct",             "ATR(14)/harga * 100"),
    ("rs_btc_30d",          "return koin 30 bar - return BTC 30 bar (poin %)"),
    ("dd_from_1y_high_pct", "(harga - high 252 bar)/high * 100  (<= 0)"),
]


# ─────────────────────────────────────────────────────────────
# Konteks: buy-and-hold per koin
# ─────────────────────────────────────────────────────────────

def buy_and_hold(universe: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for sym, df in universe.items():
        if len(df) <= bt.WARMUP_BARS + 5:
            continue
        p0 = float(df["close"].iloc[bt.WARMUP_BARS])
        p1 = float(df["close"].iloc[-1])
        if p0 <= 0:
            continue
        rows.append({
            "symbol": sym,
            "bars": len(df) - bt.WARMUP_BARS,
            "start": df.index[bt.WARMUP_BARS].date(),
            "ret_pct": (p1 / p0 - 1) * 100,
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# Faktor mentah pada bar sinyal (df sudah dipotong :t+1)
# ─────────────────────────────────────────────────────────────

def raw_factors(df: pd.DataFrame, ev: dict, btc_close: pd.Series) -> dict:
    price = float(df["close"].iloc[-1])
    close = df["close"]

    # dari evaluate() — sama persis dengan yang dipakai komposit
    f = {
        "vol_ratio":       _num(ev.get("vol_ratio")),
        "stochrsi_k":      _num(ev.get("stochrsi_k")),
        "stochrsi_htf":    _num(ev.get("stochrsi_htf")),
        "fib_retr":        _num(ev.get("fib_retr")),
        "dist_to_res_pct": _num(ev.get("dist_to_res_pct")),
    }

    # dihitung fresh
    obv = ta.obv(close, df["volume"])
    f["obv_slope"] = ta.slope_pct(obv, 20)

    ema50 = ta.ema(close, 50)
    e = float(ema50.iloc[-1])
    f["dist_ema50_pct"] = (price / e - 1) * 100 if e > 0 else np.nan

    atr = float(ta.atr(df).iloc[-1])
    f["atr_pct"] = atr / price * 100 if price > 0 else np.nan

    hi252 = float(df["high"].iloc[-252:].max())
    f["dd_from_1y_high_pct"] = (price / hi252 - 1) * 100 if hi252 > 0 else np.nan

    # RS vs BTC 30 bar — pakai harga BTC di tanggal yang sama (asof, tanpa lookahead)
    if len(close) > 31:
        d_now, d_prev = df.index[-1], df.index[-31]
        b_now, b_prev = btc_close.asof(d_now), btc_close.asof(d_prev)
        coin_ret = price / float(close.iloc[-31]) - 1
        if b_prev and b_prev > 0 and not pd.isna(b_now):
            f["rs_btc_30d"] = (coin_ret - (b_now / b_prev - 1)) * 100
        else:
            f["rs_btc_30d"] = np.nan
    else:
        f["rs_btc_30d"] = np.nan
    return f


def _num(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except (TypeError, ValueError):
        return np.nan


# ─────────────────────────────────────────────────────────────
# Walk-forward per simbol — satu baris per sinyal non-veto terisi
# ─────────────────────────────────────────────────────────────

_G = {}


def _pool_init(btc_index, btc_regimes, btc_close, cfg):
    _G["idx"], _G["reg"], _G["btc_close"], _G["cfg"] = btc_index, btc_regimes, btc_close, cfg


def _one(item):
    sym, df = item
    return sym, factor_rows(sym, df, _G["idx"], _G["reg"], _G["btc_close"], _G["cfg"])


def factor_rows(symbol, df, btc_index, btc_regimes, btc_close, cfg) -> list[dict]:
    rows = []
    n = len(df)
    t = bt.WARMUP_BARS
    while t <= n - 1 - bt.MIN_FORWARD:
        bias = df.iloc[:t + 1]
        date_t = df.index[t]
        regime = bt.regime_at(btc_index, btc_regimes, date_t)
        if regime is None:
            t += 1
            continue
        try:
            htf = ta.resample_ohlcv(bias, "W-MON")
            ev = scoring.evaluate(symbol, bias, htf, regime, cfg)
        except Exception:
            t += 1
            continue
        if ev is None or ev["vetoed"]:
            t += 1
            continue

        plan = ev["plan"]
        fill_idx = bt.try_fill(df, t, plan["entry"])
        if fill_idx is None:
            t = min(t + bt.ENTRY_WINDOW, n - 1) + 1
            continue
        legs, exit_idx, outcome = bt.simulate_exit(df, fill_idx, plan["entry"],
                                                   plan["sl"], plan["tp1"], plan["tp2"])
        pnl_r = bt.cost_adjusted_pnl_r(plan["entry"], plan["sl"], legs)

        try:
            fac = raw_factors(bias, ev, btc_close)
        except Exception:
            t = exit_idx + 1
            continue

        row = {"symbol": symbol, "signal_date": date_t, "year": date_t.year,
               "regime": regime["status"], "total_score": ev["total"],
               "exit_type": outcome, "bars_held": exit_idx - fill_idx,
               "pnl_r": pnl_r, "win": pnl_r > 0}
        row.update(fac)
        rows.append(row)
        t = exit_idx + 1
    return rows


# ─────────────────────────────────────────────────────────────
# Analisa per faktor
# ─────────────────────────────────────────────────────────────

def _spearman(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if m.sum() < 20 or a[m].nunique() < 3:
        return np.nan
    return float(a[m].rank().corr(b[m].rank()))


def quintile_table(sig: pd.DataFrame, col: str, ycol: str = "pnl_r") -> pd.DataFrame | None:
    d = sig[[col, ycol, "year"]].dropna(subset=[col, ycol])
    if len(d) < 5 * MIN_PER_QUINTILE // 2:
        return None
    try:
        d = d.assign(q=pd.qcut(d[col].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]))
    except ValueError:
        return None
    out = []
    for q in [1, 2, 3, 4, 5]:
        s = d[d["q"] == q]
        out.append({
            "Q": q, "n": len(s),
            "faktor_lo": float(s[col].min()), "faktor_hi": float(s[col].max()),
            "win": float((s[ycol] > 0).mean()),
            "E_R": float(s[ycol].mean()),
            "median_R": float(s[ycol].median()),
        })
    return pd.DataFrame(out)


def stability(sig: pd.DataFrame, col: str, ycol: str = "pnl_r") -> tuple[float, float]:
    """(Q5-Q1) di 2021-2023, (Q5-Q1) di 2024-2026 — pada kolom hasil ycol."""
    res = []
    for lo, hi in [(0, SPLIT_YEAR), (SPLIT_YEAR, 9999)]:
        d = sig[(sig["year"] >= lo) & (sig["year"] < hi)][[col, ycol]].dropna(subset=[col, ycol])
        if len(d) < 200:
            res.append(np.nan)
            continue
        try:
            d = d.assign(q=pd.qcut(d[col].rank(method="first"), 5, labels=False))
        except ValueError:
            res.append(np.nan)
            continue
        e = d.groupby("q")[ycol].mean()
        res.append(float(e.get(4, np.nan) - e.get(0, np.nan)))
    return res[0], res[1]


def verdict(qt: pd.DataFrame, spr: float, s1: float, s2: float) -> tuple[bool, str]:
    if qt is None:
        return False, "data tak cukup untuk kuintil"
    n_ok = bool((qt["n"] >= MIN_PER_QUINTILE).all())
    e = qt["E_R"].to_numpy()
    mono_up = all(e[i] <= e[i + 1] for i in range(4))
    mono_dn = all(e[i] >= e[i + 1] for i in range(4))
    gap = e[4] - e[0]
    gap_ok = abs(gap) > MIN_Q5_Q1
    spr_ok = (not np.isnan(spr)) and abs(spr) >= MIN_SPEARMAN
    stab_ok = (not np.isnan(s1) and not np.isnan(s2) and np.sign(s1) == np.sign(s2)
               and s1 != 0 and s2 != 0)

    reasons = [
        ("n>=500/kuintil", n_ok),
        ("monoton atau |Q5-Q1|>0.15R", (mono_up or mono_dn or gap_ok)),
        (f"|Spearman|>=0.10 (={spr:+.3f})", spr_ok),
        (f"arah stabil ({s1:+.2f} / {s2:+.2f})", stab_ok),
    ]
    passed = all(ok for _, ok in reasons)
    direction = ("Q5>Q1" if gap > 0 else "Q1>Q5") if abs(gap) > 1e-9 else "datar"
    detail = "; ".join(f"{'v' if ok else 'x'} {name}" for name, ok in reasons)
    return passed, f"[{'LOLOS' if passed else 'gagal'}] arah {direction}, gap {gap:+.3f} R | {detail}"


def anova_symbol_eta_sq(sig: pd.DataFrame, ycol: str = "pnl_r") -> tuple[float, float, int]:
    """Fraksi varians pnl_r yang dijelaskan identitas koin saja (eta-squared,
    ANOVA satu arah). Return (eta_sq, F, k_grup)."""
    d = sig[["symbol", ycol]].dropna()
    grand = d[ycol].mean()
    ss_total = float(((d[ycol] - grand) ** 2).sum())
    g = d.groupby("symbol")[ycol]
    ss_between = float((g.count() * (g.mean() - grand) ** 2).sum())
    ss_within = ss_total - ss_between
    k = d["symbol"].nunique()
    nobs = len(d)
    eta_sq = ss_between / ss_total if ss_total > 0 else np.nan
    f = ((ss_between / (k - 1)) / (ss_within / (nobs - k))) if (k > 1 and ss_within > 0) else np.nan
    return eta_sq, f, k


def interpret(raw_spr: float, dm_spr: float) -> str:
    r, m = abs(raw_spr) if not np.isnan(raw_spr) else 0.0, abs(dm_spr) if not np.isnan(dm_spr) else 0.0
    if r >= 0.10 and m < 0.05:
        return "PROKSI KUALITAS KOIN — bukan timing. Tidak berguna untuk screener."
    if r < 0.05 and m >= 0.10:
        return "SINYAL TIMING TERSEMBUNYI (tertutup derau antar-koin). Temuan berharga."
    if r >= 0.10 and m >= 0.10:
        return "punya komponen timing (mungkin bercampur kualitas koin)."
    if m >= 0.05 or r >= 0.05:
        return "lemah/ambigu — di bawah ambang."
    return "tidak ada sinyal (mentah maupun demeaned)."


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Uji faktor tunggal per kuintil (tanpa lookahead)")
    ap.add_argument("--symbols", default=None, help="Batasi simbol (dipisah koma)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--threads", action="store_true", help="Thread, bukan proses (debug)")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    sym_filter = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    print("-> Memuat universe .cache_history/ ...", flush=True)
    uni = bt.load_universe(sym_filter, use_history=True)
    if "BTCUSDT" not in uni:
        raise SystemExit("BTCUSDT tidak ada di .cache_history — jalankan fetch_history.py dulu.")
    print(f"   {len(uni)} simbol")

    # ── KONTEKS: buy-and-hold ──
    print("\n" + "=" * 96)
    print("  KONTEKS — BUY & HOLD per koin (dari bar warm-up ke-250 sampai akhir data)")
    print("=" * 96)
    bh = buy_and_hold(uni)
    pos = float((bh["ret_pct"] > 0).mean())
    print(f"  n koin            : {len(bh)}")
    print(f"  Return rata-rata  : {bh['ret_pct'].mean():+.1f}%")
    print(f"  Return median     : {bh['ret_pct'].median():+.1f}%")
    print(f"  Koin yang naik    : {pos*100:.0f}%")
    print(f"  Persentil 25 / 75 : {bh['ret_pct'].quantile(.25):+.1f}% / {bh['ret_pct'].quantile(.75):+.1f}%")
    print(f"  Terbaik / terburuk: {bh['ret_pct'].max():+.0f}% / {bh['ret_pct'].min():+.0f}%")
    if bh["ret_pct"].median() < 0:
        print("\n  >> Median koin RUGI kalau di-hold. Lahan long-only di universe ini buruk —\n"
              "     setiap hasil faktor di bawah harus dibaca dengan latar ini.")
    else:
        print("\n  >> Median koin naik kalau di-hold. Lahan tidak sepenuhnya buruk.")

    print("\n-> Membangun regime BTC walk-forward ...", flush=True)
    btc_index, btc_regimes = bt.build_btc_regime_lookup(uni["BTCUSDT"])
    _rdist = pd.Series([r["status"] for r in btc_regimes if r]).value_counts()
    print(f"   Sebaran regime: {_rdist.to_dict()}")
    btc_close = uni["BTCUSDT"]["close"]
    cfg = dict(scr.DEFAULT_CFG)

    kind = "thread" if args.threads else "proses"
    print(f"\n-> Mengumpulkan sinyal + faktor ({args.workers} {kind}) ...", flush=True)
    t0 = time.time()
    items = list(uni.items())
    all_rows = []
    if args.threads:
        _pool_init(btc_index, btc_regimes, btc_close, cfg)
        pool = cf.ThreadPoolExecutor(max_workers=args.workers)
    else:
        pool = cf.ProcessPoolExecutor(max_workers=args.workers, initializer=_pool_init,
                                      initargs=(btc_index, btc_regimes, btc_close, cfg))
    with pool as ex:
        for i, (sym, rows) in enumerate(ex.map(_one, items), 1):
            all_rows.extend(rows)
            if i % 20 == 0 or i == len(items):
                print(f"   {bt._eta(i, len(items), t0)}  ({len(all_rows)} sinyal)", flush=True)

    sig = pd.DataFrame(all_rows)
    if sig.empty:
        raise SystemExit("Tidak ada sinyal.")
    csv_path = os.path.join(args.outdir, "factor_test_signals.csv")
    try:
        sig.to_csv(csv_path, index=False)
    except PermissionError:
        csv_path = os.path.join(args.outdir,
                                f"factor_test_signals_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv")
        sig.to_csv(csv_path, index=False)

    # demeaned per koin — buang efek "koin ini bagus/jelek", sisakan pemilihan MOMEN
    sig["pnl_r_dm"] = sig["pnl_r"] - sig.groupby("symbol")["pnl_r"].transform("mean")

    span = f"{pd.to_datetime(sig['signal_date']).min().date()}..{pd.to_datetime(sig['signal_date']).max().date()}"
    print("\n" + "=" * 96)
    print(f"  UJI FAKTOR TUNGGAL (v2: mentah + demeaned per koin) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print(f"  {len(sig)} sinyal | {sig['symbol'].nunique()} koin | {span} | "
          f"E[R] semua sinyal {sig['pnl_r'].mean():+.3f} | win {(sig['pnl_r']>0).mean()*100:.1f}%")
    print("=" * 96)

    eta, fstat, k = anova_symbol_eta_sq(sig)
    print("\n  ANOVA satu arah — berapa varians pnl_r dijelaskan IDENTITAS KOIN saja?")
    print(f"    eta-squared = {eta*100:.1f}%   (F={fstat:.1f}, {k} koin, n={len(sig)})")
    if eta >= 0.05:
        print(f"    -> Identitas koin menjelaskan {eta*100:.0f}% varians. Pemilihan KOIN "
              "mengalahkan pemilihan WAKTU;\n"
              "       uji demeaned di bawah adalah yang sebenarnya relevan untuk screener timing.")
    else:
        print(f"    -> Identitas koin hanya menjelaskan {eta*100:.1f}% varians — hasil mentah & "
              "demeaned akan mirip.")

    print("\n  Per faktor: Spearman MENTAH vs DEMEANED (kemampuan pilih momen) + selisih.")
    print(f"  Verdict 4-kriteria dinilai pada versi DEMEANED. Ambang: |Spearman|>={MIN_SPEARMAN}, "
          f"|Q5-Q1|>{MIN_Q5_Q1}R, n>={MIN_PER_QUINTILE}/kuintil, arah stabil.")
    print("=" * 96)

    summary = []
    for col, desc in FACTORS:
        print(f"\n{'-'*96}\n  {col}  —  {desc}")
        if sig[col].notna().sum() < 5 * MIN_PER_QUINTILE // 2:
            print("    data tak cukup untuk analisa kuintil")
            summary.append((col, False, np.nan, np.nan, "data tak cukup"))
            continue
        raw_spr = _spearman(sig[col], sig["pnl_r"])
        dm_spr = _spearman(sig[col], sig["pnl_r_dm"])
        qt_raw = quintile_table(sig, col, "pnl_r")
        qt_dm = quintile_table(sig, col, "pnl_r_dm")

        print(f"    {'Q':<3}{'n':>7}{'rentang faktor':>24}{'E[R] mentah':>13}{'E[R] demeaned':>15}")
        for (_, rr), (_, rd) in zip(qt_raw.iterrows(), qt_dm.iterrows()):
            rng = f"[{rr['faktor_lo']:.3g}, {rr['faktor_hi']:.3g}]"
            print(f"    {int(rr['Q']):<3}{int(rr['n']):>7}{rng:>24}{rr['E_R']:>+13.3f}{rd['E_R']:>+15.3f}")

        diff = (dm_spr - raw_spr) if not (np.isnan(dm_spr) or np.isnan(raw_spr)) else np.nan
        print(f"    Spearman  mentah {raw_spr:+.3f} | demeaned {dm_spr:+.3f} | selisih {diff:+.3f}")
        s1, s2 = stability(sig, col, "pnl_r_dm")
        ok, msg = verdict(qt_dm, dm_spr, s1, s2)
        print(f"    {msg}")
        print(f"    -> {interpret(raw_spr, dm_spr)}")
        summary.append((col, ok, raw_spr, dm_spr, interpret(raw_spr, dm_spr)))

    print("\n" + "=" * 96)
    print("  RINGKASAN — Spearman mentah / demeaned / verdict(demeaned)")
    print("=" * 96)
    print(f"  {'faktor':<22}{'mentah':>9}{'demeaned':>11}{'verdict':>9}   tafsiran")
    for c, ok, rs, ds, tafsir in summary:
        rss = f"{rs:+.3f}" if not (isinstance(rs, float) and np.isnan(rs)) else "  -  "
        dss = f"{ds:+.3f}" if not (isinstance(ds, float) and np.isnan(ds)) else "  -  "
        print(f"  {c:<22}{rss:>9}{dss:>11}{'LOLOS' if ok else 'gagal':>9}   {tafsir}")
    print("=" * 96)

    lolos = [c for c, ok, *_ in summary if ok]
    if not lolos:
        print("  TIDAK ADA faktor yang lolos verdict demeaned 4-kriteria.")
        print(f"  Kesimpulan: sembilan faktor mentah, diuji sendiri-sendiri di {len(sig)} sinyal,\n"
              "  tidak ada yang memisahkan MOMEN yang lebih baik dari yang lebih buruk di dalam\n"
              "  koin yang sama, secara stabil. Screener ini memilih WAKTU — dan tidak ada faktor\n"
              "  tunggal yang membantunya melakukan itu.")
    else:
        print(f"  Faktor lolos (verdict demeaned): {', '.join(lolos)}")
        print("  'Lolos' = ada sinyal timing terukur & stabil, BUKAN otomatis layak jadi strategi.")
    print(f"\n  Diekspor: {csv_path}")
    print("  Ini alat ukur. Hasil nol adalah hasil yang sah.")
    print("=" * 96 + "\n")


if __name__ == "__main__":
    main()
