#!/usr/bin/env python3
"""
calibrate_threshold.py — Distribusi skor & kapasitas kandidat pada 15 koin
universe_frozen.json (BUKAN universe volume harian).

Latar: diagnose.py menjalankan sebaran volume pada seluruh universe kandidat
(ratusan pair). Untuk memilih ambang skor yang PRAKTIS bagi eksperimen 50 trade,
yang relevan hanya 15 koin beku — screener harian (daily_run.py) tidak pernah
keluar dari daftar itu. Skrip ini mengukur, secara walk-forward tanpa lookahead:

  a. Distribusi skor evaluate() di 15 koin selama N hari terakhir
  b. Untuk tiap ambang (40..70): rata-rata kandidat per minggu SETELAH veto
  c. Sebaran alasan veto (per alasan utama)
  d. Berapa hari yang menghasilkan 0 kandidat

Ini ALAT UKUR. Tidak menyetel scoring.py/indicators.py — hanya membaca lewat
scoring.evaluate(). Mekanik evaluasi (slicing tanpa lookahead, lookup regime BTC,
warm-up) dipinjam apa adanya dari backtest.py — tidak ditulis ulang.

    python calibrate_threshold.py                 # .cache/ harian (paling segar), 180 hari
    python calibrate_threshold.py --history       # arsip .cache_history/
    python calibrate_threshold.py --days 365
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
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
import backtest as bt            # noqa: E402  (pinjam mekanik: load_universe, regime, warmup)

bt._force_utf8()

FROZEN_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe_frozen.json")
THRESHOLDS = [40, 45, 50, 55, 60, 65, 70]


def load_frozen_symbols() -> list[str]:
    with open(FROZEN_JSON, encoding="utf-8") as f:
        return list(json.load(f)["symbols"])


# ─────────────────────────────────────────────────────────────
# Klasifikasi alasan veto — string dari scoring.evaluate()
# ─────────────────────────────────────────────────────────────

def classify_veto(reason: str) -> str:
    r = reason
    if r.startswith("BTC status MERAH"):
        return "Regime BTC MERAH"
    if r.startswith("R:R ke TP1 hanya"):
        return "R:R ke TP1 di bawah minimum (1:2.0)"
    if r.startswith("R:R 1:") and "tidak masuk akal" in r:
        return "R:R ke TP1 di atas max_plausible (1:15) - geometri rusak"
    if r.startswith("StochRSI:"):
        if "overbought" in r:
            return "StochRSI daily overbought (>80) - telat"
        return "StochRSI: lainnya (data kurang)"
    if r.startswith("Fibonacci:"):
        if "0.786" in r or "0,786" in r:
            return "Fibonacci: harga tembus di bawah 0.786 - struktur batal"
        return "Fibonacci: struktur swing tidak jelas / retr tak terhitung"
    if r.startswith("Volume:"):
        return "Volume: divergensi negatif / breakout volume tipis"
    if r.startswith("S/R:"):
        return "S/R: baru breakdown zona support"
    if r.startswith("Pattern:"):
        return "Pattern: pola bearish terdeteksi"
    return f"Lain-lain: {r[:50]}"


# ─────────────────────────────────────────────────────────────
# Worker walk-forward per simbol (langkah 1 bar, SEMUA hari dievaluasi)
# ─────────────────────────────────────────────────────────────

_G = {}


def _init(btc_index, btc_regimes, cfg, cutoff_ts):
    _G["idx"] = btc_index
    _G["reg"] = btc_regimes
    _G["cfg"] = cfg
    _G["cutoff"] = cutoff_ts


def _one(item: tuple[str, pd.DataFrame]) -> tuple[str, list[dict], list[str]]:
    sym, df = item
    idx, regimes, cfg, cutoff = _G["idx"], _G["reg"], _G["cfg"], _G["cutoff"]
    rows: list[dict] = []
    errs: list[str] = []
    n = len(df)
    # Mulai dari bar pertama yang tanggalnya >= cutoff, tapi hormati warm-up.
    start = max(bt.WARMUP_BARS, int(df.index.searchsorted(cutoff, side="left")))
    for t in range(start, n):                     # sampai bar terakhir yang tersedia
        bias = df.iloc[:t + 1]                    # potongan asli tanpa lookahead
        date_t = df.index[t]
        regime = bt.regime_at(idx, regimes, date_t)
        if regime is None:
            continue
        try:
            htf = ta.resample_ohlcv(bias, "W-MON")
            res = scoring.evaluate(sym, bias, htf, regime, cfg)
        except Exception as e:                    # noqa: BLE001
            errs.append(f"{sym}@{date_t.date()}: {e}")
            continue
        if res is None:
            continue
        rows.append({
            "symbol": sym,
            "date": date_t.normalize(),
            "regime": regime["status"],
            "total": int(res["total"]),
            "grade": res["grade"],
            "vetoed": bool(res["vetoed"]),
            "veto_reasons": list(res["veto_reasons"]),
            "rr1": res["plan"]["rr1"],
            "s_volume": res["s_volume"], "s_stochrsi": res["s_stochrsi"],
            "s_fib": res["s_fib"], "s_sr": res["s_sr"], "s_pattern": res["s_pattern"],
        })
    return sym, rows, errs


# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Kalibrasi ambang skor pada 15 koin beku")
    ap.add_argument("--history", action="store_true",
                    help="Pakai arsip panjang .cache_history/ (default: .cache/ harian, lebih segar)")
    ap.add_argument("--days", type=int, default=180, help="Jendela kalender ke belakang (default 180)")
    ap.add_argument("--workers", type=int, default=max(1, min(15, (os.cpu_count() or 4))))
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    symbols = load_frozen_symbols()
    src = ".cache_history/" if args.history else ".cache/"
    print(f"-> Memuat {len(symbols)} koin beku dari {src} (tanpa download) ...", flush=True)
    universe = bt.load_universe(symbols, use_history=args.history)
    missing = [s for s in symbols if s not in universe]
    if "BTCUSDT" not in universe:
        raise SystemExit(f"BTCUSDT tidak ada di {src} - dibutuhkan untuk regime. "
                         + ("Jalankan fetch_history.py." if args.history
                            else "Jalankan screener.py --mode daily."))
    if missing:
        print(f"   PERINGATAN: {len(missing)} simbol tak ada di cache dan dilewati: {missing}")
    print(f"   {len(universe)} simbol dimuat.")

    # Rentang tanggal aktual (cache bisa berakhir beberapa hari lalu).
    last_date = max(df.index.max() for df in universe.values())
    cutoff = (last_date - pd.Timedelta(days=args.days)).normalize()
    print(f"-> Jendela: {cutoff.date()} .. {last_date.date()} "
          f"({(last_date - cutoff).days} hari kalender)")

    print("-> Membangun regime BTC walk-forward (sekali) ...", flush=True)
    btc_index, btc_regimes = bt.build_btc_regime_lookup(universe["BTCUSDT"])
    _rdist = pd.Series([r["status"] for r in btc_regimes if r]).value_counts().to_dict()
    print(f"   Sebaran regime BTC sepanjang sejarah: {_rdist}")

    cfg = dict(scr.DEFAULT_CFG)                   # TIDAK diubah

    items = list(universe.items())
    t0 = time.time()
    all_rows: list[dict] = []
    all_errs: list[str] = []
    with cf.ProcessPoolExecutor(max_workers=args.workers, initializer=_init,
                                initargs=(btc_index, btc_regimes, cfg, cutoff)) as ex:
        for i, (sym, rows, errs) in enumerate(ex.map(_one, items), 1):
            all_rows.extend(rows)
            all_errs.extend(errs)
            print(f"   [{i:2d}/{len(items)}] {sym:12s} {len(rows):4d} hari dievaluasi", flush=True)
    print(f"   selesai dalam {time.time() - t0:.0f}s")

    df = pd.DataFrame(all_rows)
    if df.empty:
        raise SystemExit("Tidak ada evaluasi terkumpul - periksa rentang tanggal / warm-up.")
    if all_errs:
        print(f"\n  Peringatan: {len(all_errs)} bar gagal dievaluasi (dilewati). Contoh:")
        for e in all_errs[:5]:
            print(f"    - {e}")

    df.to_csv(os.path.join(args.outdir, "calibrate_threshold_evals.csv"), index=False)

    # Kalender minggu efektif dari tanggal yang benar-benar dievaluasi.
    all_dates = pd.to_datetime(sorted(df["date"].unique()))
    n_days_span = (all_dates.max() - all_dates.min()).days + 1
    n_weeks = n_days_span / 7.0
    trading_days = df["date"].nunique()

    line = "=" * 92
    print("\n" + line)
    print(f"  KALIBRASI AMBANG SKOR — 15 KOIN universe_frozen.json — "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Sumber {src} | jendela {all_dates.min().date()}..{all_dates.max().date()} "
          f"({n_days_span} hari, {n_weeks:.1f} minggu, {trading_days} hari-bar unik)")
    print(f"  Total evaluasi (simbol x hari): {len(df)}")
    print(line)

    # ── (a) Distribusi skor ──
    print("\n  (a) DISTRIBUSI SKOR evaluate() — semua evaluasi (termasuk yang kena veto)")
    s = df["total"]
    qs = {"min": s.min(), "p25": s.quantile(.25), "median": s.median(),
          "p75": s.quantile(.75), "p90": s.quantile(.90), "p95": s.quantile(.95),
          "maks": s.max()}
    print("     " + "  ".join(f"{k}={v:.0f}" for k, v in qs.items()))
    sv = df.loc[~df["vetoed"], "total"]
    if len(sv):
        print(f"     (hanya {len(sv)} evaluasi LOLOS veto: min={sv.min():.0f} "
              f"median={sv.median():.0f} p90={sv.quantile(.9):.0f} maks={sv.max():.0f})")
    print(f"     grade: " + ", ".join(f"{g}={int((df['grade'] == g).sum())}"
                                      for g in ["A+", "A", "B", "C"]))
    top = df.nlargest(5, "total")[["date", "symbol", "total", "grade", "vetoed"]]
    print("     5 skor tertinggi di jendela ini:")
    for _, r in top.iterrows():
        print(f"       {r['date'].date()}  {r['symbol']:10s}  {r['total']:3d} {r['grade']:2s}"
              f"  {'(kena veto)' if r['vetoed'] else '(lolos veto)'}")

    # ── (b) Kandidat per minggu per ambang ──
    print("\n  (b) KANDIDAT PER MINGGU per ambang (skor >= ambang DAN lolos semua veto)")
    print(f"      {'AMBANG':>7}{'kand/mgg':>11}{'episode/mgg':>13}{'total kand':>12}{'% dari eval':>13}")
    passed = df[~df["vetoed"]].copy()
    per_thr = {}
    for thr in THRESHOLDS:
        cand = passed[passed["total"] >= thr].copy()
        n_cand = len(cand)
        # "episode" = simbol yang BARU muncul (bukan kandidat di hari-bar sebelumnya
        #  untuk simbol itu) — perkiraan jumlah chart baru yang perlu ditinjau.
        episodes = 0
        for sym, g in cand.groupby("symbol"):
            d = np.sort(g["date"].values)
            episodes += 1 + int((np.diff(d).astype("timedelta64[D]").astype(int) > 3).sum())
        per_thr[thr] = {
            "n_cand": n_cand,
            "per_week": n_cand / n_weeks,
            "episodes_per_week": episodes / n_weeks,
        }
        print(f"      {thr:>7}{n_cand / n_weeks:>11.2f}{episodes / n_weeks:>13.2f}"
              f"{n_cand:>12}{n_cand / len(df) * 100:>12.1f}%")
    print("      episode/mgg = perkiraan chart BARU per minggu (simbol yang absen >3 hari "
          "lalu muncul lagi dihitung episode baru).")

    # ── (c) Sebaran alasan veto ──
    print("\n  (c) SEBARAN ALASAN VETO")
    vt = df[df["vetoed"]]
    n_vt = len(vt)
    print(f"      {n_vt} dari {len(df)} evaluasi kena veto ({n_vt / len(df) * 100:.1f}%).")
    # Alasan UTAMA = alasan pertama yang tercatat evaluate().
    primary = vt["veto_reasons"].apply(lambda xs: classify_veto(xs[0]) if xs else "?")
    pc = primary.value_counts()
    print("      Berdasarkan alasan UTAMA (alasan pertama; % dari evaluasi ter-veto):")
    for reason, cnt in pc.items():
        print(f"        {cnt:5d}  {cnt / n_vt * 100:5.1f}%  {reason}")
    # Kemunculan di posisi mana pun (satu evaluasi bisa punya >1 alasan).
    any_cat: dict[str, int] = {}
    for xs in vt["veto_reasons"]:
        for cat in {classify_veto(x) for x in xs}:
            any_cat[cat] = any_cat.get(cat, 0) + 1
    print("      Kemunculan di posisi MANA PUN (jumlahnya bisa > 100%):")
    for reason, cnt in sorted(any_cat.items(), key=lambda kv: -kv[1]):
        print(f"        {cnt:5d}  {cnt / n_vt * 100:5.1f}%  {reason}")

    # ── (d) Hari tanpa kandidat ──
    print("\n  (d) HARI TANPA KANDIDAT (dari hari-bar unik di jendela)")
    print(f"      {'AMBANG':>7}{'hari 0-kand':>14}{'/ total':>10}{'% hari':>9}"
          f"{'streak terpanjang':>20}")
    date_index = pd.to_datetime(sorted(df["date"].unique()))
    for thr in THRESHOLDS:
        cand = passed[passed["total"] >= thr]
        cand_days = set(pd.to_datetime(cand["date"].unique()))
        zero_days = [d for d in date_index if d not in cand_days]
        # streak terpanjang hari beruntun tanpa kandidat (pakai hari-bar unik)
        streak = best = 0
        for d in date_index:
            if d in cand_days:
                streak = 0
            else:
                streak += 1
                best = max(best, streak)
        per_thr[thr]["zero_days"] = len(zero_days)
        per_thr[thr]["zero_pct"] = len(zero_days) / len(date_index) * 100
        per_thr[thr]["max_streak"] = best
        print(f"      {thr:>7}{len(zero_days):>14}{len(date_index):>10}"
              f"{len(zero_days) / len(date_index) * 100:>8.0f}%{best:>17d} hari")

    # ── Rekomendasi (dasar PRAKTIS, bukan prediktif) ──
    print("\n" + line)
    print("  REKOMENDASI AMBANG — dasar PRAKTIS (kapasitas tinjau chart), bukan prediktif")
    print(line)
    target_lo, target_hi = 2.0, 4.0
    in_band = [t for t in THRESHOLDS
               if target_lo <= per_thr[t]["per_week"] <= target_hi]
    print(f"  Target: {target_lo:.0f}-{target_hi:.0f} kandidat/minggu (bisa ditinjau manual, "
          "memungkinkan 50 trade dalam 6-12 bulan).")
    if in_band:
        # ambang tertinggi di dalam pita = paling selektif tanpa terlalu jarang
        pick = max(in_band)
        p = per_thr[pick]
        print(f"\n  -> AMBANG {pick}: {p['per_week']:.2f} kandidat/minggu "
              f"({p['episodes_per_week']:.2f} episode/minggu), "
              f"{p['zero_days']}/{len(date_index)} hari tanpa kandidat "
              f"({p['zero_pct']:.0f}%), streak nol terpanjang {p['max_streak']} hari.")
        if len(in_band) > 1:
            print(f"     (ambang lain yang juga masuk pita 2-4/mgg: "
                  f"{[t for t in in_band if t != pick]} — tidak ada yang lebih "
                  "benar atas dasar bukti, skor tidak prediktif; dipilih yang "
                  "paling selektif.)")
    else:
        lowest = THRESHOLDS[0]
        pw_low = per_thr[lowest]["per_week"]
        if pw_low < target_lo:
            print(f"\n  -> TIDAK ADA ambang yang menghasilkan {target_lo:.0f}-{target_hi:.0f}/minggu.")
            print(f"     Bahkan ambang TERENDAH ({lowest}) hanya {pw_low:.2f} kandidat/minggu.")
            print("     Artinya: universe 15 koin TERLALU SEMPIT untuk laju 50 trade "
                  "dalam 6-12 bulan pada metodologi ini — MASALAHNYA BUKAN DI AMBANG.")
            print("     Opsi (keputusan user, di luar ruang lingkup skrip ini): perpanjang "
                  "horizon eksperimen, atau tinjau ulang lebar universe SETELAH trade ke-50.")
        else:
            print(f"\n  -> Semua ambang > {target_hi:.0f}/minggu (ambang tertinggi "
                  f"{THRESHOLDS[-1]} = {per_thr[THRESHOLDS[-1]]['per_week']:.2f}/mgg). "
                  "Pakai ambang tertinggi atau lebih tinggi lagi.")

    print("\n  Diekspor: calibrate_threshold_evals.csv (semua evaluasi mentah).")
    print("  Ingat: ambang di sini dipilih atas dasar KAPASITAS, bukan kinerja. "
          "Backtest sudah membuktikan skor tidak prediktif (Spearman -0.13).")
    print(line + "\n")


if __name__ == "__main__":
    main()
