#!/usr/bin/env python3
"""
journal.py — Jurnal trade sebagai INSTRUMEN RISET, bukan sekadar catatan.

Tujuan: mengukur apakah penilaian diskresioner user menambah nilai di atas
skor screener yang sudah terbukti tidak prediktif (lihat RINGKASAN_AKHIR.md).
Eksperimen berjalan, disiplin sama seperti backtest.py/factor_test.py/dst:
tidak ada saran ambil/lewati di sini — alat ini MENCATAT dan MENGUKUR.

Penyimpanan: journal.jsonl, append-only. Setiap baris = satu record (event
"entry" dari `new`, event "exit" dari `close`), dengan timestamp UTC + hash
SHA256 dari isi record. load_records() memverifikasi ulang hash tiap baca —
kalau ada baris yang diedit setelah ditulis, hash tidak akan cocok lagi dan
itu dilaporkan sebagai peringatan integritas. TIDAK ADA perintah untuk
mengedit/menghapus baris lama; kalau perlu koreksi, buat record `new` baru
dengan --supersedes ID lama.

    python journal.py new SYMBOL [--mode daily|weekly] [--supersedes ID]
    python journal.py close ID --exit-price X --exit-reason SL|TP1|TP2|manual|timeout
    python journal.py review

Read-only terhadap akun: tidak ada API key, tidak ada order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr   # noqa: E402  (_force_utf8 via import, fetch_for_mode, DEFAULT_CFG)
import scoring            # noqa: E402
import backtest as bt     # noqa: E402  (try_fill/simulate_exit/cost_adjusted_pnl_r -- mekanik
                          #   baseline PERSIS sama dgn backtest.py, dipakai utk simulasi hasil
                          #   hipotetis trade yang DILEWATI. Bukan reimplementasi baru.)

JOURNAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal.jsonl")
FEE = 0.001            # sama dgn backtest.py: 0.1% per sisi
SLIP = 0.0005          # sama dgn backtest.py: 0.05% per sisi
VALID_EXIT_REASONS = ("SL", "TP1", "TP2", "manual", "timeout")
MIN_TRADES_FOR_CONCLUSION = 50


# ─────────────────────────────────────────────────────────────
# Penyimpanan append-only + integritas hash
# ─────────────────────────────────────────────────────────────

def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def _hash_record(d: dict) -> str:
    return hashlib.sha256(_canonical(d).encode("utf-8")).hexdigest()


def load_records() -> list[dict]:
    """Baca seluruh record & verifikasi hash tiap baris (deteksi edit setelah ditulis)."""
    if not os.path.exists(JOURNAL_PATH):
        return []
    records = []
    with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            stored_hash = rec.pop("content_hash", None)
            recomputed = _hash_record(rec)
            rec["content_hash"] = stored_hash
            rec["_tampered"] = stored_hash != recomputed
            if rec["_tampered"]:
                print(f"  !! PERINGATAN INTEGRITAS: journal.jsonl baris {lineno} tidak cocok "
                      f"dengan hash-nya -- kemungkinan diedit manual setelah ditulis. "
                      f"Record DIKELUARKAN dari statistik review.", file=sys.stderr)
            records.append(rec)
    return records


def append_record(rec: dict) -> None:
    rec = dict(rec)
    rec["content_hash"] = _hash_record(rec)
    with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(_canonical(rec) + "\n")


def next_trade_id(records: list[dict]) -> int:
    ids = [r["trade_id"] for r in records if r["event"] == "entry"]
    return (max(ids) + 1) if ids else 1


# ─────────────────────────────────────────────────────────────
# `new` — tangkap kondisi objektif + tesis/keyakinan/keputusan user
# ─────────────────────────────────────────────────────────────

def cmd_new(args: argparse.Namespace) -> None:
    records = load_records()
    trade_id = next_trade_id(records)
    sym = args.symbol.upper()
    mode = args.mode

    cfg = dict(scr.DEFAULT_CFG)
    if mode == "weekly":
        cfg["min_bars_analysis"] = 80

    print(f"-> Mengambil data {sym} ({mode}) ...", flush=True)
    btc_bias, _ = scr.fetch_for_mode("BTCUSDT", mode, True)
    if btc_bias is None:
        raise SystemExit("Gagal mengambil data BTCUSDT untuk regime.")
    regime = scoring.btc_regime(btc_bias)

    bias, htf = scr.fetch_for_mode(sym, mode, True)
    if bias is None:
        raise SystemExit(f"Gagal mengambil data {sym}. Cek nama pair-nya.")

    r = scoring.evaluate(sym, bias, htf, regime, cfg)
    if r is None:
        raise SystemExit(f"{sym}: data historis kurang ({len(bias)} bar).")

    p = r["plan"]
    line = "=" * 74
    print("\n" + line)
    print(f"  {sym}  --  mode {mode.upper()}  --  harga {r['price']}")
    print(line)
    print(f"  BTC regime       : {regime['status']} -- {regime['message']}")
    print(f"  SKOR TOTAL       : {r['total']}/100  grade {r['grade']}"
          + ("  [VETO]" if r["vetoed"] else "  [lolos veto]"))
    if r["vetoed"]:
        for v in r["veto_reasons"]:
            print(f"     - {v}")
    print(f"  Komponen         : volume={r['s_volume']} stochrsi={r['s_stochrsi']} "
          f"fib={r['s_fib']} sr={r['s_sr']} pattern={r['s_pattern']}")
    print(f"  vol_ratio        : {r['vol_ratio']}x")
    print(f"  StochRSI (k/htf) : {r['stochrsi_k']} / {r['stochrsi_htf']}")
    print(f"  Fib retracement  : {r['fib_retr']}")
    print(f"  Trend / Pattern  : {r['trend']} / {r['pattern'] or '-'}")
    print(f"  Jarak ke resist. : {r['dist_to_res_pct']}%")
    print(f"  Rencana (dlm R)  : entry {p['entry']}  SL {p['sl']} ({p['sl_pct']}%)  "
          f"TP1 {p['tp1']} (R:R 1:{p['rr1']})  TP2 {p['tp2']} (R:R 1:{p['rr2']})")
    print(line)

    thesis = input("Tesis (bebas, boleh kosong): ").strip()

    confidence = None
    while confidence is None:
        raw = input("Keyakinan (1-5): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= 5:
            confidence = int(raw)
        else:
            print("  masukkan angka 1-5.")

    decision = None
    while decision is None:
        raw = input("Diambil atau dilewati? [ambil/lewati]: ").strip().lower()
        if raw in ("ambil", "a"):
            decision = "AMBIL"
        elif raw in ("lewati", "lewat", "l"):
            decision = "LEWAT"
        else:
            print("  jawab 'ambil' atau 'lewati'.")

    rec = {
        "event": "entry",
        "trade_id": trade_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "supersedes": args.supersedes,
        "symbol": sym, "mode": mode,
        "signal_bar_time": bias.index[-1].isoformat(),
        "price": r["price"], "total_score": r["total"], "grade": r["grade"],
        "vetoed": r["vetoed"], "veto_reasons": r["veto_reasons"],
        "s_volume": r["s_volume"], "s_stochrsi": r["s_stochrsi"], "s_fib": r["s_fib"],
        "s_sr": r["s_sr"], "s_pattern": r["s_pattern"],
        "vol_ratio": r["vol_ratio"], "stochrsi_k": r["stochrsi_k"],
        "stochrsi_htf": r["stochrsi_htf"], "fib_retr": r["fib_retr"],
        "dist_to_res_pct": r["dist_to_res_pct"], "trend": r["trend"], "pattern": r["pattern"],
        "regime": regime,
        "plan": p,
        "thesis": thesis, "confidence": confidence, "decision": decision,
    }
    append_record(rec)
    print(f"\nDisimpan sebagai trade #{trade_id} ({decision}). Append-only -- tidak bisa diedit.")


# ─────────────────────────────────────────────────────────────
# `close` — catat exit AKTUAL utk trade yang diambil, hitung pnl_r
# ─────────────────────────────────────────────────────────────

def cmd_close(args: argparse.Namespace) -> None:
    records = load_clean_records()
    entry = next((r for r in records if r["event"] == "entry" and r["trade_id"] == args.id), None)
    if entry is None:
        raise SystemExit(f"Trade #{args.id} tidak ditemukan di journal.jsonl (atau recordnya rusak -- lihat peringatan integritas di atas).")
    if any(r["event"] == "exit" and r["trade_id"] == args.id for r in records):
        raise SystemExit(f"Trade #{args.id} sudah ditutup sebelumnya -- append-only, "
                          f"tidak bisa ditutup dua kali atau diedit.")
    if entry["decision"] != "AMBIL":
        raise SystemExit(f"Trade #{args.id} berstatus LEWAT -- tidak ada posisi utk ditutup. "
                          f"Hasil hipotetisnya dihitung otomatis di 'journal.py review'.")

    plan = entry["plan"]
    entry_px, sl = float(plan["entry"]), float(plan["sl"])
    exit_px = args.exit_price
    entry_eff = entry_px * (1 + SLIP) * (1 + FEE)
    exit_eff = exit_px * (1 - SLIP) * (1 - FEE)
    risk = entry_px - sl
    pnl_r = (exit_eff - entry_eff) / risk if risk > 0 else 0.0

    rec = {
        "event": "exit",
        "trade_id": args.id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exit_price": exit_px, "exit_reason": args.exit_reason,
        "pnl_r": round(pnl_r, 4),
    }
    append_record(rec)
    print(f"Trade #{args.id} ({entry['symbol']}) ditutup: {args.exit_reason} @ {exit_px} "
          f"-> pnl_r = {pnl_r:+.3f}R (sudah termasuk fee {FEE*100:.2f}% + slip {SLIP*100:.2f}% per sisi)")


# ─────────────────────────────────────────────────────────────
# `review` — statistik + kalibrasi + trade yang dilewati (BUKAN rekomendasi)
# ─────────────────────────────────────────────────────────────

def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) < 5:
        return float("nan")
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    if ra.nunique() < 3 or rb.nunique() < 3:
        return float("nan")
    return float(ra.corr(rb))


def hypothetical_pnl(entry_rec: dict) -> dict:
    """Simulasikan APA YANG AKAN TERJADI kalau trade yang di-LEWAT tetap diambil,
    memakai data harga AKTUAL sesudahnya (bukan simulasi acak) + mekanik baseline
    backtest.py (try_fill/simulate_exit) supaya konsisten dgn seluruh proyek."""
    plan = entry_rec["plan"]
    entry, sl, tp1, tp2 = float(plan["entry"]), float(plan["sl"]), float(plan["tp1"]), float(plan["tp2"])
    sym, mode = entry_rec["symbol"], entry_rec.get("mode", "daily")
    try:
        df, _ = scr.fetch_for_mode(sym, mode, True)
    except Exception:
        return {"status": "gagal_ambil_data"}
    if df is None or not len(df):
        return {"status": "gagal_ambil_data"}

    sig_time = pd.Timestamp(entry_rec["signal_bar_time"])
    if sig_time in df.index:
        pos = df.index.get_loc(sig_time)
    else:
        pos = int(df.index.searchsorted(sig_time, side="right")) - 1
        if pos < 0:
            return {"status": "bar_sinyal_tak_ditemukan_di_cache"}

    if pos >= len(df) - 1:
        return {"status": "belum_cukup_waktu_berlalu"}

    fill_idx = bt.try_fill(df, pos, entry)
    if fill_idx is None:
        return {"status": "entry_tak_pernah_tersentuh"}

    legs, exit_idx, tag = bt.simulate_exit(df, fill_idx, entry, sl, tp1, tp2)
    pnl_r = bt.cost_adjusted_pnl_r(entry, sl, legs)
    still_running = exit_idx >= len(df) - 1 and tag in ("TIMEOUT", "TP1_TIMEOUT")
    return {"status": "selesai", "pnl_r": pnl_r, "outcome": tag, "estimasi_masih_berjalan": still_running}


def load_clean_records() -> list[dict]:
    """load_records() minus record yang gagal verifikasi hash."""
    return [r for r in load_records() if not r["_tampered"]]


def open_positions(records: list[dict] | None = None) -> list[dict]:
    """Daftar trade AMBIL yang belum ada event exit-nya. Dipakai `journal.py review`
    DAN daily_run.py (hitung posisi terbuka utk gerbang batas 3 posisi)."""
    records = records if records is not None else load_clean_records()
    entries = {r["trade_id"]: r for r in records if r["event"] == "entry"}
    exits = {r["trade_id"]: r for r in records if r["event"] == "exit"}
    return [e for tid, e in entries.items() if e["decision"] == "AMBIL" and tid not in exits]


def compute_review(records: list[dict] | None = None) -> dict:
    """Hitung seluruh statistik review sebagai dict (tanpa print) supaya
    `journal.py review` (cetak ke terminal) tidak perlu menghitung ulang
    logika yang sama di tempat lain kalau suatu saat dibutuhkan lagi (mis.
    dashboard.html dulu memakai ini sebelum dicabut 2026-09-05)."""
    records = records if records is not None else load_clean_records()
    entries = {r["trade_id"]: r for r in records if r["event"] == "entry"}
    exits = {r["trade_id"]: r for r in records if r["event"] == "exit"}

    closed_taken = [(e, exits[tid]) for tid, e in entries.items()
                     if e["decision"] == "AMBIL" and tid in exits]
    open_taken = open_positions(records)
    skipped = [e for e in entries.values() if e["decision"] == "LEWAT"]
    n = len(closed_taken)

    out = {
        "n_entries": len(entries),
        "n_ambil": sum(1 for e in entries.values() if e["decision"] == "AMBIL"),
        "n_lewat": len(skipped),
        "n_closed": n,
        "n_open": len(open_taken),
        "min_trades_for_conclusion": MIN_TRADES_FOR_CONCLUSION,
        "meets_min_trades": n >= MIN_TRADES_FOR_CONCLUSION,
        "performance": None,
        "calibration": None,
        "calibration_monotone": None,
        "spearman_score_pnl": None,
        "spearman_conf_pnl": None,
        "skipped": {"n": len(skipped), "status_counts": {}, "hypothetical_mean_pnl": None,
                    "n_hypothetical": 0, "skip_better_than_taken": None},
    }
    if n == 0:
        return out

    pnl = [x["pnl_r"] for _, x in closed_taken]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    expectancy = float(np.mean(pnl))
    win_rate = len(wins) / n * 100
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else None   # None = tak terhingga (tanpa rugi)

    eq = np.cumsum(pnl)
    running_max = np.maximum.accumulate(eq)
    max_dd = float((eq - running_max).min())

    exp_no_top3 = None
    if n > 3:
        rest = sorted(pnl, reverse=True)[3:]
        exp_no_top3 = float(np.mean(rest)) if rest else None

    out["performance"] = {
        "expectancy": expectancy, "win_rate": win_rate,
        "profit_factor": profit_factor, "max_drawdown": max_dd,
        "expectancy_no_top3": exp_no_top3,
    }

    conf_groups: dict[int, list[float]] = {}
    for e, x in closed_taken:
        conf_groups.setdefault(e["confidence"], []).append(x["pnl_r"])
    calibration = {}
    means_in_order = []
    for c in range(1, 6):
        if c in conf_groups:
            m = float(np.mean(conf_groups[c]))
            calibration[str(c)] = {"n": len(conf_groups[c]), "mean_pnl": m}
            means_in_order.append(m)
        else:
            calibration[str(c)] = None
    out["calibration"] = calibration
    if len(means_in_order) >= 2:
        out["calibration_monotone"] = all(
            means_in_order[i] <= means_in_order[i + 1] for i in range(len(means_in_order) - 1))

    scores = [e["total_score"] for e, _ in closed_taken]
    confs = [e["confidence"] for e, _ in closed_taken]
    sp_score = _spearman(scores, pnl)
    sp_conf = _spearman(confs, pnl)
    out["spearman_score_pnl"] = None if pd.isna(sp_score) else sp_score
    out["spearman_conf_pnl"] = None if pd.isna(sp_conf) else sp_conf

    statuses: dict[str, int] = {}
    hyp_pnl = []
    for e in skipped:
        h = hypothetical_pnl(e)
        statuses[h["status"]] = statuses.get(h["status"], 0) + 1
        if h["status"] == "selesai" and not h.get("estimasi_masih_berjalan"):
            hyp_pnl.append(h["pnl_r"])
    mean_skip = float(np.mean(hyp_pnl)) if hyp_pnl else None
    out["skipped"] = {
        "n": len(skipped), "status_counts": statuses,
        "hypothetical_mean_pnl": mean_skip, "n_hypothetical": len(hyp_pnl),
        "skip_better_than_taken": (mean_skip > expectancy) if mean_skip is not None else None,
    }
    return out


def cmd_review(_args: argparse.Namespace) -> None:
    rv = compute_review()
    line = "=" * 74
    print("\n" + line)
    print("  JOURNAL REVIEW -- statistik & kalibrasi (bukan rekomendasi)")
    print(line)
    print(f"  Total record entry : {rv['n_entries']}  (AMBIL={rv['n_ambil']}, LEWAT={rv['n_lewat']})")
    print(f"  AMBIL, sudah ditutup: {rv['n_closed']}   AMBIL, masih terbuka: {rv['n_open']}")

    n = rv["n_closed"]
    if n < rv["min_trades_for_conclusion"]:
        print("\n  " + "!" * 70)
        print(f"  PERINGATAN BESAR: baru {n} trade yang ditutup (< {rv['min_trades_for_conclusion']}).")
        print("  BELUM ADA KESIMPULAN YANG BISA DITARIK dari statistik di bawah ini.")
        print("  Angka tetap ditampilkan untuk transparansi, bukan sebagai bukti apa pun.")
        print("  " + "!" * 70)

    if n == 0:
        print("\n  Belum ada trade yang ditutup -- tidak ada statistik untuk dihitung.")
        print(line + "\n")
        return

    perf = rv["performance"]
    print(f"\n  -- Performa ({n} trade, satuan R) " + "-" * 30)
    print(f"  Ekspektasi (E[R])   : {perf['expectancy']:+.3f}")
    print(f"  Win rate            : {perf['win_rate']:.1f}%")
    pf = perf["profit_factor"]
    print(f"  Profit factor       : {'inf (tanpa rugi)' if pf is None else f'{pf:.2f}'}")
    print(f"  Max drawdown        : {perf['max_drawdown']:.3f}R")
    if perf["expectancy_no_top3"] is not None:
        print(f"  E[R] tanpa 3 terbaik: {perf['expectancy_no_top3']:+.3f}  (uji ketahanan ekor)")

    print(f"\n  -- Kalibrasi keyakinan (1-5) " + "-" * 30)
    for c in range(1, 6):
        cal = rv["calibration"][str(c)]
        if cal:
            print(f"  keyakinan {c}: n={cal['n']:>3d}  E[R]={cal['mean_pnl']:+.3f}")
        else:
            print(f"  keyakinan {c}: (belum ada data)")
    if rv["calibration_monotone"] is not None:
        mono = rv["calibration_monotone"]
        print(f"  Monoton naik? {'YA' if mono else 'TIDAK'}"
              + ("" if mono else "  -- keyakinan user TIDAK berbanding lurus dgn hasil di data ini"))

    print(f"\n  -- Diskresi vs skor " + "-" * 30)
    sp_score, sp_conf = rv["spearman_score_pnl"], rv["spearman_conf_pnl"]
    print(f"  Spearman skor screener vs pnl_r   : "
          f"{'n/a' if sp_score is None else f'{sp_score:+.3f}'}  (temuan backtest: seharusnya ~0)")
    print(f"  Spearman keyakinan user vs pnl_r  : {'n/a' if sp_conf is None else f'{sp_conf:+.3f}'}")
    if sp_score is not None and sp_conf is not None:
        if sp_conf - abs(sp_score) > 0.15:
            print("  -> Keyakinan user jauh lebih berkorelasi dgn hasil drpd skor -- "
                  "bukti AWAL penilaian diskresioner menambah nilai (n masih kecil, lihat peringatan di atas).")
        else:
            print("  -> Belum ada bukti keyakinan user menambah nilai di atas skor screener di data ini.")

    sk = rv["skipped"]
    print(f"\n  -- Trade yang DILEWATI ({sk['n']} record) " + "-" * 20)
    if sk["n"] == 0:
        print("  Tidak ada trade yang dilewati tercatat.")
    else:
        for status, cnt in sk["status_counts"].items():
            print(f"  status={status:<28s} n={cnt}")
        if sk["hypothetical_mean_pnl"] is not None:
            print(f"\n  Hasil hipotetis trade DILEWATI (n={sk['n_hypothetical']}, andai tetap diambil, "
                  f"mekanik+biaya sama): E[R] = {sk['hypothetical_mean_pnl']:+.3f}")
            print(f"  Hasil aktual trade DIAMBIL                                  : E[R] = {perf['expectancy']:+.3f}")
            if sk["skip_better_than_taken"]:
                print("  -> Trade yang DILEWATI justru lebih baik daripada yang DIAMBIL -- "
                      "kemampuan menyaring user NEGATIF di data ini.")
            else:
                print("  -> Trade yang DILEWATI tidak lebih baik daripada yang DIAMBIL di data ini.")
        else:
            print("\n  Belum ada trade yang dilewati dengan hasil hipotetis yang bisa dihitung "
                  "(entry belum tersentuh / data belum cukup berjalan / gagal ambil data).")

    print(line + "\n")


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Jurnal trade sebagai instrumen riset")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="Catat kondisi objektif + tesis utk satu sinyal")
    p_new.add_argument("symbol")
    p_new.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    p_new.add_argument("--supersedes", type=int, default=None,
                        help="trade_id lama yang direvisi record ini (append-only -- tidak menimpa)")

    p_close = sub.add_parser("close", help="Catat exit aktual utk trade yang DIAMBIL")
    p_close.add_argument("id", type=int)
    p_close.add_argument("--exit-price", type=float, required=True)
    p_close.add_argument("--exit-reason", choices=VALID_EXIT_REASONS, required=True)

    sub.add_parser("review", help="Statistik + kalibrasi (tidak ada saran ambil/lewati)")

    args = ap.parse_args()
    if args.cmd == "new":
        cmd_new(args)
    elif args.cmd == "close":
        cmd_close(args)
    elif args.cmd == "review":
        cmd_review(args)


if __name__ == "__main__":
    main()
