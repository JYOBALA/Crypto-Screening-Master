#!/usr/bin/env python3
"""
defi_test.py — Uji nilai prediktif DATA FUNDAMENTAL PROTOKOL (DefiLlama),
cross-sectional (BUKAN simulasi trade). Lihat HIPOTESIS_DEFI.md — pra-registrasi
di-commit SEBELUM script ini dibuat. Ambang & metodologi TIDAK diubah setelah
melihat angka apa pun.

Kategori data KEENAM proyek ini (5 sebelumnya turunan harga/volume — lihat
RINGKASAN_AKHIR.md). Metodologi identik dengan orderflow_test.py.

5 fitur x 3 horizon (5/10/20 hari) = 15 uji. Untuk tiap kombinasi: IC (Spearman)
harian antara nilai fitur pada t (di-lag 2 hari, lihat HIPOTESIS_DEFI.md jebakan
#1) dan return ke depan h hari ter-demean cross-sectional pada t.

Kriteria lulus (HIPOTESIS_DEFI.md, Langkah 3), pada set DISCOVERY (70% simbol):
  1. |rata-rata IC| >= 0.02
  2. |t-stat IC| >= 3.5
  3. Arah IC sama di 2021-2023 vs 2024-2026
  4. Spread kuintil (Q5-Q1) searah dengan tanda IC
  5. Bertahan di holdout 30% simbol (seed 20260906, dipakai SEKALI):
     tanda IC sama dengan discovery DAN |IC holdout| >= 0.02
Tambahan: versi within-symbol demeaned harus SEARAH dengan cross-sectional,
kalau tidak -> tidak lolos (sinyal timing sejati muncul di kedua kerangka).

    python defi_test.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr  # noqa: E402  (_force_utf8 lewat efek samping import)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFI_DIR = os.path.join(HERE, ".cache_defi")
RAW_DIR = os.path.join(DEFI_DIR, "raw")
CHAIN_DIR = os.path.join(DEFI_DIR, "_chains")
PRICE_DIR = os.path.join(DEFI_DIR, "_prices")
HIST_DIR = os.path.join(HERE, ".cache_history")

HORIZONS = (5, 10, 20)
FEATURES = ("D1_tvl_growth_real_30d", "D2_mcap_to_fees_pct", "D3_fees_growth_30d",
            "D4_tvl_share_of_chain_slope30", "D5_stablecoin_inflow_chain_14d")
FEATURE_DIR = {  # dugaan arah IC (HIPOTESIS_DEFI.md) -- untuk pelaporan, bukan gerbang
    "D1_tvl_growth_real_30d": +1, "D2_mcap_to_fees_pct": -1,
    "D3_fees_growth_30d": +1, "D4_tvl_share_of_chain_slope30": +1,
    "D5_stablecoin_inflow_chain_14d": +1,
}

FEATURE_LAG = 2                  # hari, aproksimasi jeda pelaporan DefiLlama (jebakan #1)
MIN_COINS_PER_DAY = 15
MIN_IC = 0.02
MIN_TSTAT = 3.5
MIN_HISTORY_DAYS = 180
SUBPERIOD_SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
HOLDOUT_SEED = 20260906         # BARU -- 20260904/20260905 tidak dipakai ulang
HOLDOUT_FRAC = 0.30


# ─────────────────────────────────────────────────────────────
# Util deret
# ─────────────────────────────────────────────────────────────

def _pairs_to_daily(pairs: list, name: str) -> pd.Series:
    """[[ts_sec, val], ...] -> Series index tanggal UTC (harian), observasi
    terakhir per hari, ffill maksimum 3 hari."""
    if not pairs:
        return pd.Series(dtype=float, name=name)
    df = pd.DataFrame(pairs, columns=["ts", "v"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.floor("D")
    s = df.groupby("date")["v"].last().sort_index()
    full = pd.date_range(s.index[0], s.index[-1], freq="D", tz="UTC")
    return s.reindex(full).ffill(limit=3).rename(name)


def _tokmap_to_frame(tokmap: dict) -> pd.DataFrame:
    """{ts_str: {TOKEN: val}} -> DataFrame(index=tanggal harian, kolom=token)."""
    if not tokmap:
        return pd.DataFrame()
    rows = {}
    for ts_str, d in tokmap.items():
        try:
            day = pd.Timestamp(int(float(ts_str)), unit="s", tz="UTC").floor("D")
        except (TypeError, ValueError):
            continue
        rows[day] = d
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.apply(pd.to_numeric, errors="coerce")
    if len(df) < 2:
        return pd.DataFrame()
    full = pd.date_range(df.index[0], df.index[-1], freq="D", tz="UTC")
    return df.reindex(full).ffill(limit=3)


def _rolling_pct_rank(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    def f(x):
        return (x <= x[-1]).mean()
    return s.rolling(window, min_periods=min_periods).apply(f, raw=True)


def _rolling_slope(s: pd.Series, window: int) -> pd.Series:
    xs = np.arange(window, dtype=float)
    xs_c = xs - xs.mean()
    denom = (xs_c ** 2).sum()

    def f(y):
        if np.isnan(y).any():
            return np.nan
        return float((xs_c * (y - y.mean())).sum() / denom)
    return s.rolling(window, min_periods=window).apply(f, raw=True)


def _load_price(sym: str) -> pd.Series | None:
    for base in (HIST_DIR, PRICE_DIR):
        p = os.path.join(base, f"{sym}USDT_1d.parquet")
        if os.path.exists(p):
            try:
                d = pd.read_parquet(p)
                s = d["close"].copy()
                s.index = pd.to_datetime(s.index, utc=True).floor("D")
                return s[~s.index.duplicated(keep="last")].sort_index()
            except Exception:
                return None
    return None


# ─────────────────────────────────────────────────────────────
# Data level-chain (dipakai bersama semua protokol di chain itu)
# ─────────────────────────────────────────────────────────────

_CHAIN_CACHE: dict[str, dict] = {}


def _chain_series(chain: str) -> tuple[pd.Series, pd.Series]:
    if chain not in _CHAIN_CACHE:
        p = os.path.join(CHAIN_DIR, f"{chain.replace('/', '_')}.json")
        tvl = stbl = pd.Series(dtype=float)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            tvl = _pairs_to_daily(d.get("chain_tvl_usd", []), f"{chain}_tvl")
            stbl = _pairs_to_daily(d.get("stablecoin_usd", []), f"{chain}_stbl")
        _CHAIN_CACHE[chain] = {"tvl": tvl, "stbl": stbl}
    c = _CHAIN_CACHE[chain]
    return c["tvl"], c["stbl"]


# ─────────────────────────────────────────────────────────────
# Panel fitur per protokol
# ─────────────────────────────────────────────────────────────

def compute_symbol_panel(rec: dict) -> pd.DataFrame | None:
    sym = rec["symbol"]
    raw_path = os.path.join(RAW_DIR, f"{sym}.json")
    if not os.path.exists(raw_path):
        return None
    with open(raw_path, "r", encoding="utf-8") as f:
        R = json.load(f)

    price = _load_price(sym)
    if price is None or len(price) < 120:
        return None

    tvl_usd = _pairs_to_daily(R.get("tvl_usd", []), "tvl_usd")
    fees = _pairs_to_daily(R.get("daily_fees", []), "fees")

    has_tvl = len(tvl_usd) >= MIN_HISTORY_DAYS
    has_fee = len(fees) >= MIN_HISTORY_DAYS
    if not (has_tvl or has_fee):
        return None

    idx = price.index
    out = pd.DataFrame(index=idx)
    out["close"] = price
    ret30 = price / price.shift(30) - 1.0

    # ---- D1: pertumbuhan TVL harga-netral (Laspeyres) 30 hari, dikondisikan harga datar
    qty = _tokmap_to_frame(R.get("tokens_native", {}))
    usd = _tokmap_to_frame(R.get("tokens_usd", {}))
    d1 = pd.Series(np.nan, index=idx)
    if not qty.empty and not usd.empty:
        common_cols = qty.columns.intersection(usd.columns)
        qty, usd = qty[common_cols], usd[common_cols]
        price_tok = (usd / qty).replace([np.inf, -np.inf], np.nan)
        qty = qty.reindex(idx).ffill(limit=3)
        price_tok = price_tok.reindex(idx).ffill(limit=3)
        p_lag = price_tok.shift(30)
        q_lag = qty.shift(30)
        valid = qty.notna() & q_lag.notna() & p_lag.notna() & (q_lag > 0) & (qty > 0)
        num = (qty.where(valid) * p_lag.where(valid)).sum(axis=1, min_count=1)
        den = (q_lag.where(valid) * p_lag.where(valid)).sum(axis=1, min_count=1)
        d1_raw = (num / den - 1.0).where(den > 0)
        d1 = d1_raw.where(ret30.abs() < 0.10)
    out["D1_tvl_growth_real_30d"] = d1

    # ---- D2: persentil trailing-365d dari price / fee_tahunan
    fee_daily = fees.reindex(idx)
    roll = fee_daily.rolling(365, min_periods=MIN_HISTORY_DAYS)
    fsum = roll.sum()
    fcnt = roll.count()
    fees_annual = np.where(fcnt >= 365, fsum, np.where(fcnt >= MIN_HISTORY_DAYS,
                                                       fsum / fcnt.replace(0, np.nan) * 365, np.nan))
    fees_annual = pd.Series(fees_annual, index=idx)
    ratio = (price / fees_annual).replace([np.inf, -np.inf], np.nan).where(fees_annual > 0)
    out["D2_mcap_to_fees_pct"] = _rolling_pct_rank(ratio, 365, MIN_HISTORY_DAYS)

    # ---- D3: fees_growth_30d
    f30 = fee_daily.rolling(30, min_periods=25).sum()
    out["D3_fees_growth_30d"] = (f30 / f30.shift(30) - 1.0).replace([np.inf, -np.inf], np.nan)

    # ---- D4: slope 30-hari dari (TVL protokol / TVL chain gabungan)
    chains = rec.get("chains") or []
    chain_tvl_sum = None
    for ch in chains:
        ctvl, _ = _chain_series(ch)
        if len(ctvl):
            c = ctvl.reindex(idx).ffill(limit=3)
            chain_tvl_sum = c if chain_tvl_sum is None else chain_tvl_sum.add(c, fill_value=0.0)
    if chain_tvl_sum is not None and has_tvl:
        share = (tvl_usd.reindex(idx).ffill(limit=3) / chain_tvl_sum).replace([np.inf, -np.inf], np.nan)
        out["D4_tvl_share_of_chain_slope30"] = _rolling_slope(share, 30)
    else:
        out["D4_tvl_share_of_chain_slope30"] = np.nan

    # ---- D5: stablecoin inflow ke chain utama (chains[0]) 14 hari
    d5 = pd.Series(np.nan, index=idx)
    if chains:
        _, stbl = _chain_series(chains[0])
        if len(stbl):
            sc = stbl.reindex(idx).ffill(limit=3)
            d5 = (sc / sc.shift(14) - 1.0).replace([np.inf, -np.inf], np.nan)
    out["D5_stablecoin_inflow_chain_14d"] = d5

    # ---- lag semua fitur 2 hari (jebakan #1)
    for feat in FEATURES:
        out[feat] = out[feat].shift(FEATURE_LAG)

    # ---- return ke depan
    for h in HORIZONS:
        out[f"fwd_ret_{h}"] = price.shift(-h) / price - 1.0

    keep = [c for c in out.columns if out[c].notna().any()]
    return out[keep] if len(keep) > 3 else None


def build_panel(recs: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    frames, used = {}, []
    for rec in recs:
        p = compute_symbol_panel(rec)
        if p is not None:
            frames[rec["symbol"]] = p
            used.append(rec["symbol"])
    if not frames:
        return pd.DataFrame(), []
    panel = pd.concat(frames, names=["symbol", "date"]).swaplevel().sort_index()
    return panel, used


# ─────────────────────────────────────────────────────────────
# IC harian cross-sectional  (identik orderflow_test.py)
# ─────────────────────────────────────────────────────────────

def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    if ra.nunique() < 3:
        return np.nan
    return float(ra.corr(rb))


def daily_ic_series(panel: pd.DataFrame, symbols_subset: set[str],
                    feature: str, horizon: int) -> pd.DataFrame:
    ycol = f"fwd_ret_{horizon}"
    if feature not in panel.columns:
        return pd.DataFrame(columns=["ic", "spread_q5q1", "n"])
    sub = panel[panel.index.get_level_values("symbol").isin(symbols_subset)]
    sub = sub[[feature, ycol]].dropna()
    rows = []
    for date, grp in sub.groupby(level="date"):
        n = len(grp)
        if n < MIN_COINS_PER_DAY:
            continue
        x = grp[feature].to_numpy()
        y = grp[ycol].to_numpy()
        y_dm = y - y.mean()
        ic = _spearman(x, y_dm)
        try:
            q = pd.qcut(grp[feature], 5, labels=False, duplicates="drop")
        except ValueError:
            q = None
        if q is not None and np.nanmax(q.to_numpy()) == 4:
            qa = q.to_numpy()
            spread = y_dm[qa == 4].mean() - y_dm[qa == 0].mean()
        else:
            spread = np.nan
        rows.append((date, ic, spread, n))
    if not rows:
        return pd.DataFrame(columns=["ic", "spread_q5q1", "n"])
    return pd.DataFrame(rows, columns=["date", "ic", "spread_q5q1", "n"]).set_index("date")


def within_symbol_ic(panel: pd.DataFrame, symbols_subset: set[str],
                     feature: str, horizon: int) -> float:
    """Spearman pooled setelah fitur & fwd_ret masing-masing di-demean per simbol."""
    ycol = f"fwd_ret_{horizon}"
    if feature not in panel.columns:
        return np.nan
    sub = panel[panel.index.get_level_values("symbol").isin(symbols_subset)]
    sub = sub[[feature, ycol]].dropna().copy()
    if len(sub) < 200:
        return np.nan
    g = sub.groupby(level="symbol")
    fx = sub[feature] - g[feature].transform("mean")
    fy = sub[ycol] - g[ycol].transform("mean")
    m = fx.notna() & fy.notna()
    if m.sum() < 200:
        return np.nan
    return _spearman(fx[m].to_numpy(), fy[m].to_numpy())


def summarize(ic_df: pd.DataFrame) -> dict:
    ic = ic_df["ic"].dropna()
    if len(ic) < 30:
        return {"n_days": len(ic), "mean_ic": np.nan, "tstat": np.nan, "pct_pos": np.nan,
                "mean_spread": np.nan, "ic_pre": np.nan, "ic_post": np.nan, "dir_stable": False}
    mean_ic = ic.mean()
    tstat = mean_ic / (ic.std(ddof=1) / np.sqrt(len(ic)))
    pre = ic[ic.index < SUBPERIOD_SPLIT]
    post = ic[ic.index >= SUBPERIOD_SPLIT]
    ic_pre = pre.mean() if len(pre) >= 30 else np.nan
    ic_post = post.mean() if len(post) >= 30 else np.nan
    dir_stable = (np.sign(ic_pre) == np.sign(ic_post)) if (pd.notna(ic_pre) and pd.notna(ic_post)) else False
    return {"n_days": len(ic), "mean_ic": mean_ic, "tstat": tstat,
            "pct_pos": (ic > 0).mean() * 100, "mean_spread": ic_df["spread_q5q1"].dropna().mean(),
            "ic_pre": ic_pre, "ic_post": ic_post, "dir_stable": dir_stable}


def passes_1_4(s: dict, within_ic: float) -> bool:
    if pd.isna(s["mean_ic"]) or pd.isna(s["tstat"]):
        return False
    if abs(s["mean_ic"]) < MIN_IC or abs(s["tstat"]) < MIN_TSTAT:
        return False
    if not s["dir_stable"]:
        return False
    if pd.isna(s["mean_spread"]) or np.sign(s["mean_spread"]) != np.sign(s["mean_ic"]):
        return False
    if pd.isna(within_ic) or np.sign(within_ic) != np.sign(s["mean_ic"]):
        return False
    return True


# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("UJI NILAI PREDIKTIF DATA FUNDAMENTAL PROTOKOL (DefiLlama) -- HIPOTESIS_DEFI.md")
    print("kategori data ke-6; metodologi = orderflow_test.py")
    print("=" * 80)

    with open(os.path.join(DEFI_DIR, "universe_defi.json"), "r", encoding="utf-8") as f:
        meta = json.load(f)
    recs = meta["mapped"]
    print(f"Universe dipetakan: {len(recs)} protokol")

    panel, used = build_panel(recs)
    if panel.empty:
        print("PANEL KOSONG -- data belum diunduh? jalankan fetch_defi.py dulu.")
        return
    print(f"Protokol dengan data cukup (>=180 hari TVL/fee + harga): {len(used)}")
    print(f"Panel: {len(panel):,} baris (protokol x hari), "
          f"{panel.index.get_level_values('date').min().date()} .. "
          f"{panel.index.get_level_values('date').max().date()}")

    rng = np.random.RandomState(HOLDOUT_SEED)
    perm = rng.permutation(sorted(used))
    n_hold = int(round(len(perm) * HOLDOUT_FRAC))
    holdout = set(perm[:n_hold])
    discovery = set(perm[n_hold:])
    print(f"Holdout seed={HOLDOUT_SEED}: {len(discovery)} discovery / {len(holdout)} holdout\n")

    all_syms = set(used)
    results = []
    for feature in FEATURES:
        for h in HORIZONS:
            s_full = summarize(daily_ic_series(panel, all_syms, feature, h))
            ic_disc = daily_ic_series(panel, discovery, feature, h)
            s_disc = summarize(ic_disc)
            w_disc = within_symbol_ic(panel, discovery, feature, h)

            verdict, hold_note = "GAGAL", "-"
            if passes_1_4(s_disc, w_disc):
                s_hold = summarize(daily_ic_series(panel, holdout, feature, h))
                hi = s_hold["mean_ic"]
                if pd.notna(hi) and abs(hi) >= MIN_IC and np.sign(hi) == np.sign(s_disc["mean_ic"]):
                    verdict = "LULUS"
                    hold_note = f"IC={hi:+.3f} (arah sama, |IC|>=0.02)"
                else:
                    verdict = "GAGAL (holdout)"
                    hold_note = f"IC={hi:+.3f}" if pd.notna(hi) else "n<30 hari valid"

            results.append({
                "feature": feature, "horizon": h, "dugaan_arah": FEATURE_DIR[feature],
                "n_days_full": s_full["n_days"], "mean_ic_full": s_full["mean_ic"],
                "tstat_full": s_full["tstat"],
                "mean_ic_disc": s_disc["mean_ic"], "tstat_disc": s_disc["tstat"],
                "within_ic_disc": w_disc,
                "ic_2021_2023": s_disc["ic_pre"], "ic_2024_2026": s_disc["ic_post"],
                "dir_stable": s_disc["dir_stable"], "mean_spread_disc": s_disc["mean_spread"],
                "pct_pos_disc": s_disc["pct_pos"], "verdict": verdict, "holdout": hold_note,
            })
            print(f"  {feature:32s} h={h:>2d}  IC(full)={s_full['mean_ic']:+.4f} "
                  f"t={s_full['tstat']:+.2f} (n={s_full['n_days']:>4d}d)  "
                  f"IC(disc)={s_disc['mean_ic']:+.4f} t={s_disc['tstat']:+.2f} "
                  f"within={w_disc:+.3f}  -> {verdict}", flush=True)

    res = pd.DataFrame(results)
    out_csv = os.path.join(HERE, "defi_ic_results.csv")
    res.to_csv(out_csv, index=False)
    n_pass = (res["verdict"] == "LULUS").sum()
    print("\n" + "=" * 80)
    print(f"SELESAI: {len(res)} uji, {n_pass} LULUS SEMUA kriteria (termasuk holdout).")
    print(f"Detail -> {out_csv}")
    if n_pass == 0:
        print("Tidak ada fitur fundamental dengan sinyal yang bertahan. Laporkan & berhenti.")
    print("=" * 80)


if __name__ == "__main__":
    main()
