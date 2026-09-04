#!/usr/bin/env python3
"""
regime_test.py — TAHAP 1: bisakah regime dideteksi dengan nilai prediktif?

Protokol & hipotesis: HIPOTESIS_REGIME.md (di-commit sebelum run ini).

Menguji 5 detektor regime (semua walk-forward, hanya data <= bar t):
  R1  BTC close vs SMA200 harian
  R2  BTC close vs EMA50 harian  (aturan sekarang, pembanding)
  R3  Breadth: % koin universe dengan close > SMA200 masing-masing
  R4  Proksi dominasi BTC: SMA50 rasio BTC/altindex, naik/turun
  R5  Return BTC 90 bar ke belakang, positif/negatif

Ukuran hasil: return 30 HARI KE DEPAN tiap koin (bukan hasil trade), dibandingkan
antara titik-evaluasi berlabel BULL vs BEAR. Kadens evaluasi mingguan (tiap 7 bar).

LULUS jika: selisih BULL-BEAR >= 5 poin persen, 95% CI bootstrap tak melewati nol,
DAN arah selisih sama di 2021-2023 & 2024-2026.

    python regime_test.py

Tidak menyetel ambang. scoring.py/indicators.py tidak diubah.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as ta          # noqa: E402
import backtest as bt            # noqa: E402  (load_universe, WARMUP_BARS)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()

FWD = 30            # horizon return ke depan (bar/hari)
EVAL_STRIDE = 7    # kadens evaluasi detektor (mingguan) -> batasi tumpang-tindih
MIN_DIFF_PP = 5.0  # kriteria 1: selisih BULL-BEAR minimal (poin persen)
N_BOOT = 2000
SPLIT_YEAR = 2024


def build_panel(uni: dict[str, pd.DataFrame]):
    """DataFrame close per koin, di-reindex ke kalender harian BTC (NaN di luar
    rentang listing koin)."""
    master = uni["BTCUSDT"].index
    closes = pd.DataFrame({s: d["close"].reindex(master) for s, d in uni.items()})
    return master, closes


def detector_labels(master: pd.DatetimeIndex, closes: pd.DataFrame) -> dict[str, pd.Series]:
    """Return {Rx: Series[bool] (BULL=True) di seluruh master, NaN sebelum warm-up}."""
    btc = closes["BTCUSDT"]
    out = {}

    # R1 — BTC vs SMA200
    sma200_btc = btc.rolling(200, min_periods=200).mean()
    out["R1"] = (btc > sma200_btc)

    # R2 — BTC vs EMA50 (aturan sekarang)
    ema50_btc = ta.ema(btc, 50)
    ema50_btc[:49] = np.nan
    out["R2"] = (btc > ema50_btc)

    # R3 — breadth: % koin dgn close > SMA200 sendiri
    sma200 = closes.rolling(200, min_periods=200).mean()
    above = closes > sma200
    n_have = sma200.notna().sum(axis=1)
    n_above = above.where(sma200.notna()).sum(axis=1)
    breadth = (n_above / n_have.replace(0, np.nan)).where(n_have >= 20)
    out["R3"] = (breadth > 0.50)
    out["_breadth"] = breadth

    # R4 — proksi dominasi BTC: SMA50 rasio BTC/altindex, naik/turun
    first = closes.apply(lambda c: c.loc[c.first_valid_index()] if c.first_valid_index() is not None else np.nan)
    norm = closes / first
    alt = norm.drop(columns=["BTCUSDT"])
    altindex = np.exp(np.log(alt).mean(axis=1, skipna=True))
    dom = norm["BTCUSDT"] / altindex
    dom_sma = dom.rolling(50, min_periods=50).mean()
    dom_slope = dom_sma.diff()
    out["R4"] = (dom_slope < 0)       # dominasi turun = alt season = BULL
    out["_dom"] = dom_sma

    # R5 — return BTC 90 bar
    ret90 = btc / btc.shift(90) - 1.0
    out["R5"] = (ret90 > 0)

    # masking sebelum warm-up
    warm_cut = master[bt.WARMUP_BARS]
    for k in ("R1", "R2", "R3", "R4", "R5"):
        out[k] = out[k].where(master >= warm_cut)
    return out


def collect(master, closes, labels) -> dict[str, list[tuple]]:
    """Per detektor: list of (eval_date, is_bull, np.array fwd_ret_30d semua koin)."""
    fwd = (closes.shift(-FWD) / closes - 1.0) * 100.0     # % 30-hari ke depan
    n = len(master)
    eval_pos = range(bt.WARMUP_BARS, n - FWD, EVAL_STRIDE)
    data = {r: [] for r in ("R1", "R2", "R3", "R4", "R5")}
    for p in eval_pos:
        d = master[p]
        rets = fwd.iloc[p].to_numpy()
        rets = rets[np.isfinite(rets)]
        if len(rets) < 20:
            continue
        for r in data:
            lab = labels[r].iloc[p]
            if pd.isna(lab):
                continue
            data[r].append((d, bool(lab), rets))
    return data


def summarize(entries: list[tuple]):
    """Return dict: mean_bull, mean_bear, diff, n_weeks_bull, n_weeks_bear, obs_*."""
    bull = [a for _, lab, a in entries if lab]
    bear = [a for _, lab, a in entries if not lab]
    mb = np.concatenate(bull).mean() if bull else np.nan
    mr = np.concatenate(bear).mean() if bear else np.nan
    return {"mean_bull": mb, "mean_bear": mr, "diff": mb - mr,
            "wk_bull": len(bull), "wk_bear": len(bear),
            "obs_bull": sum(len(a) for a in bull), "obs_bear": sum(len(a) for a in bear)}


def bootstrap_ci(entries: list[tuple], n_boot=N_BOOT, seed=42):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(entries))
    diffs = []
    for _ in range(n_boot):
        samp = rng.choice(idx, size=len(idx), replace=True)
        b, r = [], []
        for i in samp:
            (b if entries[i][1] else r).append(entries[i][2])
        if b and r:
            diffs.append(np.concatenate(b).mean() - np.concatenate(r).mean())
    if not diffs:
        return np.nan, np.nan
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def subperiod_diff(entries: list[tuple], lo, hi):
    sub = [e for e in entries if lo <= e[0].year < hi]
    if len(sub) < 10:
        return np.nan
    s = summarize(sub)
    return s["diff"]


def main():
    print("-> Memuat universe .cache_history/ ...", flush=True)
    uni = bt.load_universe(use_history=True)
    print(f"   {len(uni)} simbol")
    master, closes = build_panel(uni)
    print(f"   kalender {master[0].date()}..{master[-1].date()} ({len(master)} bar)")

    print("-> Menghitung label detektor (walk-forward) ...", flush=True)
    labels = detector_labels(master, closes)

    print("-> Mengumpulkan return 30-hari-ke-depan per titik evaluasi mingguan ...", flush=True)
    data = collect(master, closes, labels)

    line = "=" * 104
    print("\n" + line)
    print(f"  TAHAP 1 — UJI DETEKTOR REGIME — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print(f"  Ukuran: return {FWD} hari ke depan universe, per koin, per titik evaluasi mingguan.")
    print(f"  LULUS: selisih BULL-BEAR >= {MIN_DIFF_PP}pp + 95% CI tak lewati nol + arah sama "
          f"{SPLIT_YEAR-3}-{SPLIT_YEAR-1}/{SPLIT_YEAR}-2026")
    print(line)

    desc = {
        "R1": "BTC > SMA200 harian",
        "R2": "BTC > EMA50 harian (aturan sekarang)",
        "R3": "breadth: % koin > SMA200 sendiri, ambang 50%",
        "R4": "proksi dominasi BTC (SMA50 rasio BTC/altindex) turun",
        "R5": "return BTC 90 bar > 0",
    }
    results = {}
    for r in ("R1", "R2", "R3", "R4", "R5"):
        ent = data[r]
        s = summarize(ent)
        lo, hi = bootstrap_ci(ent)
        d1 = subperiod_diff(ent, 0, SPLIT_YEAR)
        d2 = subperiod_diff(ent, SPLIT_YEAR, 9999)
        ci_ok = not (np.isnan(lo) or np.isnan(hi)) and (lo > 0 or hi < 0)
        size_ok = abs(s["diff"]) >= MIN_DIFF_PP
        dir_ok = (not np.isnan(d1) and not np.isnan(d2)
                  and np.sign(d1) == np.sign(d2) and d1 != 0 and d2 != 0)
        passed = ci_ok and size_ok and dir_ok
        results[r] = {"s": s, "ci": (lo, hi), "d1": d1, "d2": d2, "pass": passed}

        print(f"\n  {r}  —  {desc[r]}")
        print(f"    minggu BULL / BEAR : {s['wk_bull']} / {s['wk_bear']}  "
              f"(obs koin: {s['obs_bull']:,} / {s['obs_bear']:,})")
        print(f"    return30 BULL      : {s['mean_bull']:+.1f}%")
        print(f"    return30 BEAR      : {s['mean_bear']:+.1f}%")
        print(f"    selisih BULL-BEAR  : {s['diff']:+.1f} pp   95% CI [{lo:+.1f}, {hi:+.1f}]")
        print(f"    sub-periode        : 2021-23 {d1:+.1f}pp | 2024-26 {d2:+.1f}pp")
        checks = (f"{'v' if size_ok else 'x'} >={MIN_DIFF_PP}pp; "
                  f"{'v' if ci_ok else 'x'} CI tak lewati nol; "
                  f"{'v' if dir_ok else 'x'} arah stabil")
        print(f"    [{'LULUS' if passed else 'gagal'}] {checks}")

    print("\n" + line)
    print("  RINGKASAN TAHAP 1")
    print(line)
    print(f"  {'det':<5}{'BULL%':>9}{'BEAR%':>9}{'selisih':>10}{'95% CI':>20}{'stabil?':>10}{'verdict':>9}")
    for r in ("R1", "R2", "R3", "R4", "R5"):
        x = results[r]
        s = x["s"]
        lo, hi = x["ci"]
        stab = "ya" if (not np.isnan(x["d1"]) and not np.isnan(x["d2"])
                        and np.sign(x["d1"]) == np.sign(x["d2"])) else "tidak"
        print(f"  {r:<5}{s['mean_bull']:>+9.1f}{s['mean_bear']:>+9.1f}{s['diff']:>+10.1f}"
              f"{f'[{lo:+.1f},{hi:+.1f}]':>20}{stab:>10}{'LULUS' if x['pass'] else 'gagal':>9}")
    print(line)

    winners = [r for r in ("R1", "R2", "R3", "R4", "R5") if results[r]["pass"]]
    if not winners:
        print("  TIDAK ADA detektor yang lulus ketiga kriteria.")
        print("  Kesimpulan: regime TIDAK bisa dideteksi dengan nilai prediktif yang stabil di\n"
              "  universe & periode ini. TAHAP 2 (long/short) TIDAK dijalankan — tidak ada\n"
              "  fondasi untuk membangunnya.")
    else:
        best = max(winners, key=lambda r: abs(results[r]["s"]["diff"]))
        print(f"  Detektor LULUS: {', '.join(winners)}")
        print(f"  Terbaik (selisih terbesar): {best} — {desc[best]}  "
              f"(selisih {results[best]['s']['diff']:+.1f} pp)")
        print("  -> Lanjut TAHAP 2 dengan detektor ini (lihat HIPOTESIS_REGIME.md).")
    print(line + "\n")


if __name__ == "__main__":
    main()
