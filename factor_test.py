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


def quintile_table(sig: pd.DataFrame, col: str) -> pd.DataFrame | None:
    d = sig[[col, "pnl_r", "year"]].dropna(subset=[col])
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
            "win": float((s["pnl_r"] > 0).mean()),
            "E_R": float(s["pnl_r"].mean()),
            "median_R": float(s["pnl_r"].median()),
        })
    return pd.DataFrame(out)


def stability(sig: pd.DataFrame, col: str) -> tuple[float, float]:
    """(Q5-Q1) di 2021-2023, (Q5-Q1) di 2024-2026."""
    res = []
    for lo, hi in [(0, SPLIT_YEAR), (SPLIT_YEAR, 9999)]:
        d = sig[(sig["year"] >= lo) & (sig["year"] < hi)][[col, "pnl_r"]].dropna(subset=[col])
        if len(d) < 200:
            res.append(np.nan)
            continue
        try:
            d = d.assign(q=pd.qcut(d[col].rank(method="first"), 5, labels=False))
        except ValueError:
            res.append(np.nan)
            continue
        e = d.groupby("q")["pnl_r"].mean()
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

    reasons = []
    reasons.append(("n>=500/kuintil", n_ok))
    reasons.append(("monoton atau |Q5-Q1|>0.15R", (mono_up or mono_dn or gap_ok)))
    reasons.append((f"|Spearman|>=0.10 (={spr:+.3f})", spr_ok))
    reasons.append((f"arah stabil (2021-23 {s1:+.2f} / 2024-26 {s2:+.2f})", stab_ok))
    passed = all(ok for _, ok in reasons)
    direction = ("Q5>Q1" if gap > 0 else "Q1>Q5") if abs(gap) > 1e-9 else "datar"
    tag = "LOLOS" if passed else "gagal"
    detail = "; ".join(f"{'v' if ok else 'x'} {name}" for name, ok in reasons)
    return passed, f"[{tag}] arah {direction}, gap {gap:+.3f} R | {detail}"


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

    span = f"{pd.to_datetime(sig['signal_date']).min().date()}..{pd.to_datetime(sig['signal_date']).max().date()}"
    print("\n" + "=" * 96)
    print(f"  UJI FAKTOR TUNGGAL — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print(f"  {len(sig)} sinyal | {sig['symbol'].nunique()} koin | {span} | "
          f"E[R] semua sinyal {sig['pnl_r'].mean():+.3f} | win {(sig['pnl_r']>0).mean()*100:.1f}%")
    print(f"  Kriteria lulus: monoton/|Q5-Q1|>{MIN_Q5_Q1}R + |Spearman|>={MIN_SPEARMAN} + "
          f"n>={MIN_PER_QUINTILE}/kuintil + arah stabil {SPLIT_YEAR-3}-{SPLIT_YEAR-1}/{SPLIT_YEAR}-2026")
    print("=" * 96)

    summary = []
    for col, desc in FACTORS:
        print(f"\n{'-'*96}\n  {col}  —  {desc}")
        qt = quintile_table(sig, col)
        spr = _spearman(sig[col], sig["pnl_r"])
        s1, s2 = stability(sig, col)
        if qt is None:
            print("    data tak cukup untuk analisa kuintil")
            summary.append((col, False, "data tak cukup"))
            continue
        print(f"    {'Q':<3}{'n':>7}{'rentang faktor':>26}{'win':>9}{'E[R]':>10}{'median R':>11}")
        for _, r in qt.iterrows():
            rng = f"[{r['faktor_lo']:.3g}, {r['faktor_hi']:.3g}]"
            print(f"    {int(r['Q']):<3}{int(r['n']):>7}{rng:>26}{r['win']*100:>8.1f}%"
                  f"{r['E_R']:>+10.3f}{r['median_R']:>+11.3f}")
        print(f"    Spearman(faktor, pnl_r) = {spr:+.3f}")
        ok, msg = verdict(qt, spr, s1, s2)
        print(f"    {msg}")
        summary.append((col, ok, msg))

    print("\n" + "=" * 96)
    print("  RINGKASAN")
    print("=" * 96)
    lolos = [c for c, ok, _ in summary if ok]
    for c, ok, msg in summary:
        print(f"  {'LOLOS ' if ok else 'gagal '} {c:<22} {msg.split('|')[0].strip()}")
    print("=" * 96)
    if not lolos:
        print("  TIDAK ADA faktor yang lolos keempat kriteria.")
        print("  Kesimpulan: sembilan faktor mentah, diuji sendiri-sendiri di "
              f"{len(sig)} sinyal, tidak ada yang memisahkan menang dari kalah "
              "dengan cara yang stabil.")
    else:
        print(f"  Faktor lolos: {', '.join(lolos)}")
        print("  CATATAN: 'lolos' = ada sinyal terukur, BUKAN otomatis layak jadi strategi. "
              "Verifikasi manual + out-of-sample sebelum dipakai.")
    print(f"\n  Diekspor: {csv_path}")
    print("  Ini alat ukur. Hasil nol adalah hasil yang sah.")
    print("=" * 96 + "\n")


if __name__ == "__main__":
    main()
