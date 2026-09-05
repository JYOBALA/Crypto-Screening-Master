#!/usr/bin/env python3
"""
orderflow_test.py — Uji nilai prediktif ALIRAN ORDER, cross-sectional
(BUKAN simulasi trade). Lihat HIPOTESIS_ORDERFLOW.md — pra-registrasi
di-commit SEBELUM hasil ini dilihat. Ambang & metodologi di bawah TIDAK
diubah setelah melihat angka apa pun.

5 fitur x 3 horizon (5/10/20 hari) x 2 universe (U1 likuid, U2 luas) = 30 uji.
Untuk tiap kombinasi: IC (Spearman) harian antara nilai fitur pada t dan
return ke depan h hari ter-demean cross-sectional pada t, lalu dirata-ratakan
sepanjang deret hari (bukan pooled -- demean per hari menghapus arah pasar
SEBELUM korelasi dihitung, walau secara matematis untuk korelasi rank dan
spread Q5-Q1 dalam satu hari, demean per-hari adalah konstanta yang tidak
mengubah urutan/spread -- tetap dihitung eksplisit di sini persis sesuai
protokol, bukan disederhanakan diam-diam).

Kriteria lulus (HIPOTESIS_ORDERFLOW.md, Langkah 3):
  1. |rata-rata IC| >= 0.02
  2. |t-stat IC| >= 3.5   (dinaikkan dari 3.0 krn 30 uji, bukan 15)
  3. Arah IC sama di 2021-2023 vs 2024-2026
  4. Spread kuintil (Q5-Q1) searah dengan tanda IC
  5. Bertahan di holdout 30% simbol (seed BARU, lihat HOLDOUT_SEED di bawah)

Kriteria 1-4 dihitung pada set DISCOVERY (70% simbol). Kriteria 5 baru
diperiksa untuk kombinasi yang lulus 1-4 di discovery, memakai HANYA simbol
holdout yang belum disentuh sama sekali sebelumnya. Operasionalisasi
"bertahan" (tidak dirinci di HIPOTESIS_ORDERFLOW.md) ditetapkan SEKARANG,
sebelum skrip ini dijalankan: sama tanda dengan discovery DAN |IC holdout|
>= 0.02 -- TIDAK mensyaratkan |t|>=3.5 lagi di holdout, karena itu berarti
menuntut signifikansi statistik penuh pada sampel yang secara sengaja
dikecilkan 30%, yang gagal karena ukuran sampel bukan karena tidak adanya
efek.

    python orderflow_test.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr  # noqa: E402  (_force_utf8 lewat efek samping import)

OF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_orderflow")
HORIZONS = (5, 10, 20)
FEATURES = ("F1_taker_buy_ratio", "F2_avg_trade_size_pct", "F3_trade_intensity",
            "F4_flow_divergence", "F5_taker_buy_ratio_extreme")
MIN_COINS_PER_DAY = 15          # lantai kecukupan sampel cross-sectional per hari
MIN_IC = 0.02
MIN_TSTAT = 3.5
SUBPERIOD_SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
HOLDOUT_SEED = 20260905          # BARU -- seed 20260904 (factor_test.py) tidak dipakai ulang
HOLDOUT_FRAC = 0.30


# ─────────────────────────────────────────────────────────────
# Muat universe + hitung fitur per simbol
# ─────────────────────────────────────────────────────────────

def load_universe(key: str) -> list[str]:
    with open(os.path.join(OF_DIR, f"universe_{key}.json"), "r", encoding="utf-8") as f:
        return json.load(f)["symbols"]


def _rolling_pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Persentil nilai TERAKHIR pada tiap jendela trailing (hanya data sampai t)."""
    def f(x):
        return (x <= x[-1]).mean()
    return s.rolling(window, min_periods=window).apply(f, raw=True)


def _rolling_slope(s: pd.Series, window: int) -> pd.Series:
    xs = np.arange(window, dtype=float)
    xs_c = xs - xs.mean()
    denom = (xs_c ** 2).sum()

    def f(y):
        return float((xs_c * (y - y.mean())).sum() / denom)
    return s.rolling(window, min_periods=window).apply(f, raw=True)


def compute_symbol_panel(symbol: str) -> pd.DataFrame | None:
    path = os.path.join(OF_DIR, f"{symbol}_1d.parquet")
    if not os.path.exists(path):
        return None
    d = pd.read_parquet(path)
    if len(d) < 100:                      # < ~1 jendela F2/F5 + sisa -> tidak berguna
        return None

    out = pd.DataFrame(index=d.index)
    out["close"] = d["close"]

    taker_buy_ratio = (d["tbbav"] / d["volume"]).replace([np.inf, -np.inf], np.nan)
    avg_trade_size = (d["qav"] / d["trades"]).replace([np.inf, -np.inf], np.nan)
    ma20_trades = d["trades"].rolling(20, min_periods=20).mean()

    out["F1_taker_buy_ratio"] = taker_buy_ratio
    out["F2_avg_trade_size_pct"] = _rolling_pct_rank(avg_trade_size, 90)
    out["F3_trade_intensity"] = d["trades"] / ma20_trades

    ret10 = d["close"] / d["close"].shift(10) - 1.0
    flow_slope10 = _rolling_slope(taker_buy_ratio, 10)
    out["F4_flow_divergence"] = flow_slope10.where(ret10.abs() < 0.05)

    out["F5_taker_buy_ratio_extreme"] = _rolling_pct_rank(taker_buy_ratio, 90)

    for h in HORIZONS:
        out[f"fwd_ret_{h}"] = d["close"].shift(-h) / d["close"] - 1.0

    return out


def build_panel(symbols: list[str]) -> pd.DataFrame:
    """Panel (date, symbol) -> kolom fitur + fwd_ret_h, untuk SEMUA simbol universe."""
    frames = {}
    for s in symbols:
        p = compute_symbol_panel(s)
        if p is not None:
            frames[s] = p
    panel = pd.concat(frames, names=["symbol", "date"])
    panel = panel.swaplevel().sort_index()
    return panel


# ─────────────────────────────────────────────────────────────
# IC harian cross-sectional
# ─────────────────────────────────────────────────────────────

def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    if ra.nunique() < 3:
        return np.nan
    return float(ra.corr(rb))


def daily_ic_series(panel: pd.DataFrame, symbols_subset: set[str],
                     feature: str, horizon: int) -> pd.DataFrame:
    """Return DataFrame(index=date) kolom ic, spread_q5q1, n."""
    ycol = f"fwd_ret_{horizon}"
    sub = panel[panel.index.get_level_values("symbol").isin(symbols_subset)]
    sub = sub[[feature, ycol]].dropna()

    rows = []
    for date, grp in sub.groupby(level="date"):
        n = len(grp)
        if n < MIN_COINS_PER_DAY:
            continue
        x = grp[feature].to_numpy()
        y = grp[ycol].to_numpy()
        y_dm = y - y.mean()                      # demean cross-sectional (lihat catatan modul)
        ic = _spearman(x, y_dm)
        try:
            q = pd.qcut(grp[feature], 5, labels=False, duplicates="drop")
        except ValueError:
            q = None
        if q is not None and q.max() == 4:
            q5 = y_dm[q.to_numpy() == 4].mean()
            q1 = y_dm[q.to_numpy() == 0].mean()
            spread = q5 - q1
        else:
            spread = np.nan
        rows.append((date, ic, spread, n))
    if not rows:
        return pd.DataFrame(columns=["ic", "spread_q5q1", "n"])
    r = pd.DataFrame(rows, columns=["date", "ic", "spread_q5q1", "n"]).set_index("date")
    return r


def summarize(ic_df: pd.DataFrame) -> dict:
    ic = ic_df["ic"].dropna()
    if len(ic) < 30:
        return {"n_days": len(ic), "mean_ic": np.nan, "tstat": np.nan,
                "pct_pos": np.nan, "mean_spread": np.nan,
                "ic_2021_2023": np.nan, "ic_2024_2026": np.nan, "dir_stable": False}
    mean_ic = ic.mean()
    tstat = mean_ic / (ic.std(ddof=1) / np.sqrt(len(ic)))
    pct_pos = (ic > 0).mean() * 100
    mean_spread = ic_df["spread_q5q1"].dropna().mean()

    pre = ic[ic.index < SUBPERIOD_SPLIT]
    post = ic[ic.index >= SUBPERIOD_SPLIT]
    ic_pre = pre.mean() if len(pre) >= 30 else np.nan
    ic_post = post.mean() if len(post) >= 30 else np.nan
    dir_stable = (np.sign(ic_pre) == np.sign(ic_post)) if (pd.notna(ic_pre) and pd.notna(ic_post)) else False

    return {"n_days": len(ic), "mean_ic": mean_ic, "tstat": tstat,
            "pct_pos": pct_pos, "mean_spread": mean_spread,
            "ic_2021_2023": ic_pre, "ic_2024_2026": ic_post, "dir_stable": dir_stable}


def passes_criteria(s: dict) -> bool:
    if pd.isna(s["mean_ic"]) or pd.isna(s["tstat"]):
        return False
    if abs(s["mean_ic"]) < MIN_IC or abs(s["tstat"]) < MIN_TSTAT:
        return False
    if not s["dir_stable"]:
        return False
    if pd.isna(s["mean_spread"]) or np.sign(s["mean_spread"]) != np.sign(s["mean_ic"]):
        return False
    return True


# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("UJI NILAI PREDIKTIF ALIRAN ORDER -- cross-sectional (HIPOTESIS_ORDERFLOW.md)")
    print("=" * 78)

    all_symbols = sorted(set(load_universe("u1")) | set(load_universe("u2")))
    rng = np.random.RandomState(HOLDOUT_SEED)
    perm = rng.permutation(all_symbols)
    n_holdout = int(round(len(perm) * HOLDOUT_FRAC))
    holdout_syms = set(perm[:n_holdout])
    discovery_syms = set(perm[n_holdout:])
    print(f"Holdout seed={HOLDOUT_SEED}: {len(discovery_syms)} discovery / "
          f"{len(holdout_syms)} holdout (dari {len(all_symbols)} simbol gabungan U1+U2)")

    results = []
    for uni_key in ("u1", "u2"):
        uni_syms = set(load_universe(uni_key))
        print(f"\n-> Membangun panel {uni_key.upper()} ({len(uni_syms)} simbol) ...", flush=True)
        panel = build_panel(sorted(uni_syms))
        print(f"   panel: {len(panel):,} baris (simbol x hari)")

        disc_in_uni = uni_syms & discovery_syms
        hold_in_uni = uni_syms & holdout_syms

        for feature in FEATURES:
            for h in HORIZONS:
                ic_full = daily_ic_series(panel, uni_syms, feature, h)
                ic_disc = daily_ic_series(panel, disc_in_uni, feature, h)
                s_full = summarize(ic_full)
                s_disc = summarize(ic_disc)

                verdict = "GAGAL"
                holdout_note = "-"
                if passes_criteria(s_disc):
                    ic_hold = daily_ic_series(panel, hold_in_uni, feature, h)
                    s_hold = summarize(ic_hold)
                    if (pd.notna(s_hold["mean_ic"]) and abs(s_hold["mean_ic"]) >= MIN_IC
                            and np.sign(s_hold["mean_ic"]) == np.sign(s_disc["mean_ic"])):
                        verdict = "LULUS"
                        holdout_note = f"IC={s_hold['mean_ic']:+.3f} (arah sama, |IC|>=0.02)"
                    else:
                        verdict = "GAGAL (holdout)"
                        hold_ic = s_hold['mean_ic']
                        holdout_note = f"IC={hold_ic:+.3f}" if pd.notna(hold_ic) else "n<30 hari valid"

                row = {"universe": uni_key.upper(), "feature": feature, "horizon": h,
                       "n_days_full": s_full["n_days"], "mean_ic_full": s_full["mean_ic"],
                       "tstat_full": s_full["tstat"], "pct_pos_full": s_full["pct_pos"],
                       "mean_spread_full": s_full["mean_spread"],
                       "mean_ic_disc": s_disc["mean_ic"], "tstat_disc": s_disc["tstat"],
                       "ic_2021_2023_disc": s_disc["ic_2021_2023"], "ic_2024_2026_disc": s_disc["ic_2024_2026"],
                       "dir_stable_disc": s_disc["dir_stable"], "verdict": verdict, "holdout": holdout_note}
                results.append(row)
                print(f"   {uni_key.upper():3s} {feature:28s} h={h:>2d}  "
                      f"IC(full)={s_full['mean_ic']:+.4f} t={s_full['tstat']:+.2f} "
                      f"(n={s_full['n_days']:>4d}d)  IC(disc)={s_disc['mean_ic']:+.4f} "
                      f"t={s_disc['tstat']:+.2f}  -> {verdict}", flush=True)

    res = pd.DataFrame(results)
    out_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orderflow_ic_results.csv")
    res.to_csv(out_csv, index=False)

    n_pass = (res["verdict"] == "LULUS").sum()
    print("\n" + "=" * 78)
    print(f"SELESAI: {len(res)} uji, {n_pass} LULUS SEMUA kriteria (termasuk holdout).")
    print(f"Detail lengkap -> {out_csv}")
    print("=" * 78)


if __name__ == "__main__":
    main()
