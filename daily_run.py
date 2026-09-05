#!/usr/bin/env python3
"""
daily_run.py — Jalankan sekali sehari (cron), HANYA untuk 15 koin di
`universe_frozen.json` (TIDAK fetch universe dari volume hari ini — lihat
CLAUDE.md soal bahaya itu). Tulis data/latest.json + arsip data/YYYY-MM-DD.json
untuk dashboard.html, lalu kirim ringkasan ke Telegram. Exit setelah selesai —
tidak ada proses yang menetap (dijadwalkan lewat cron, lihat DEPLOY.md).

PRINSIP DESAIN (lihat RINGKASAN_AKHIR.md — skor screener TERBUKTI tidak
prediktif): alat ini adalah PENYARING PERHATIAN, bukan penghasil sinyal.
Maksimal SATU notifikasi per hari (dijamin dua lapis: crontab sekali/hari
DAN guard idempotensi di bawah — jalan dua kali di hari yang sama tidak
mengirim dua notifikasi). Tidak ada kata "beli"/"sinyal" (di luar baris
disclaimer wajib)/"peluang"/"entry sekarang" di pesan — divalidasi otomatis
sebelum kirim, lihat _check_forbidden().

    python daily_run.py            # jalan normal (skip kalau sudah jalan hari ini)
    python daily_run.py --force    # paksa jalan ulang & kirim ulang notifikasi hari ini

Read-only terhadap akun: tidak ada API key, tidak ada order.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr    # noqa: E402  (BASE_CANDIDATES/_switch_base/_force_utf8 via import,
                           #   fetch_for_mode, DEFAULT_CFG -- WAJIB reuse, lihat CLAUDE.md)
import scoring             # noqa: E402
import journal as jr       # noqa: E402  (load_clean_records/open_positions/compute_review --
                           #   satu sumber kebenaran, sama dipakai `journal.py review`)

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
UNIVERSE_PATH = os.path.join(ROOT, "universe_frozen.json")
DATA_DIR = os.path.join(ROOT, "data")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
LOG_PATH = os.path.join(ROOT, "daily_run.log")

MAX_OPEN_POSITIONS = 3          # sama dgn KRITERIA_EVALUASI.md
MAX_CANDIDATES_IN_MESSAGE = 5
DASHBOARD_MIN_TRADES_FOR_STATS = 25   # beda dari MIN_TRADES_FOR_CONCLUSION jurnal (50) --
                                       # ini cuma gerbang tampil di dashboard, lihat KRITERIA_EVALUASI.md
DISCLAIMER = "Skor tidak prediktif (5 uji null). Ini penyaring perhatian, bukan sinyal."
FORBIDDEN_WORDS = ("beli", "peluang", "entry sekarang")


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _json_safe(obj):
    """NaN/Infinity bukan JSON valid (browser JSON.parse gagal) -> jadi None."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):   # obj != obj -> NaN
            return None
        return obj
    return obj


def load_universe() -> tuple[list[str], dict]:
    with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return meta["symbols"], meta


def _check_forbidden(text: str) -> None:
    """Gerbang keras aturan bahasa. 'sinyal' HANYA boleh muncul di baris
    disclaimer wajib ('...bukan sinyal.') -- dicek per baris, bukan whole-text,
    supaya disclaimer itu sendiri tidak memicu false positive."""
    lower = text.lower()
    for w in FORBIDDEN_WORDS:
        if w in lower:
            raise AssertionError(f"Pesan mengandung frasa terlarang: {w!r}")
    for line in text.splitlines():
        if "sinyal" in line.lower() and "bukan sinyal" not in line.lower():
            raise AssertionError(f"Kata 'sinyal' dipakai di luar baris disclaimer wajib: {line!r}")


# ─────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID tidak diset (.env) -- notifikasi dilewati.")
        return False
    try:
        _check_forbidden(text)
    except AssertionError as e:
        logging.critical(f"Pesan diblokir sebelum kirim -- melanggar aturan bahasa: {e}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:      # noqa: BLE001
        logging.error(f"Gagal kirim Telegram: {e}")
        return False


def compose_message(date_str: str, regime: dict, n_open: int, candidates: list[dict],
                     failed: list[str]) -> str:
    lines = [f"Screener harian -- {date_str}",
             f"BTC: {regime['status']} -- {regime['message']}",
             f"Posisi terbuka: {n_open}/{MAX_OPEN_POSITIONS}", ""]

    if n_open >= MAX_OPEN_POSITIONS:
        lines.append("Batas 3 posisi tercapai. Tidak ada yang perlu dicek hari ini.")
    elif not candidates:
        lines.append("Tidak ada kandidat untuk dicek chart hari ini.")
    else:
        lines.append(f"Kandidat untuk dicek chart ({len(candidates)}):")
        for r in candidates[:MAX_CANDIDATES_IN_MESSAGE]:
            p = r["plan"]
            lines.append(f"- {r['symbol']}  skor {r['total']}/100 ({r['grade']})  harga {r['price']}")
            lines.append(f"  entry {p['entry']}  SL {p['sl']}  TP1 {p['tp1']} (R:R 1:{p['rr1']})")
            lines.append(f"  https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}")
        rest = len(candidates) - MAX_CANDIDATES_IN_MESSAGE
        if rest > 0:
            lines.append(f"...dan {rest} lainnya (lihat dashboard).")

    if failed:
        lines.append("")
        lines.append(f"Gagal ambil data: {', '.join(failed)}")

    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _candidate_view(r: dict) -> dict:
    p = r["plan"]
    return {
        "symbol": r["symbol"], "total": r["total"], "grade": r["grade"],
        "vetoed": r["vetoed"], "veto_reasons": r["veto_reasons"],
        "s_volume": r["s_volume"], "s_stochrsi": r["s_stochrsi"], "s_fib": r["s_fib"],
        "s_sr": r["s_sr"], "s_pattern": r["s_pattern"],
        "price": r["price"],
        "entry": p["entry"], "sl": p["sl"], "tp1": p["tp1"], "tp2": p["tp2"],
        "rr1": p["rr1"], "rr2": p["rr2"],
        "tv_url": f"https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}",
    }


def _enrich_open_position(e: dict, price_lookup: dict) -> dict:
    sym = e["symbol"]
    price = price_lookup.get(sym)
    if price is None:
        try:
            bias, _ = scr.fetch_for_mode(sym, e.get("mode", "daily"), True)
            price = float(bias["close"].iloc[-1]) if bias is not None else None
        except Exception:      # noqa: BLE001
            price = None
    p = e["plan"]
    dist_sl = ((price - p["sl"]) / price * 100) if price else None
    dist_tp1 = ((p["tp1"] - price) / price * 100) if price else None
    return {
        "trade_id": e["trade_id"], "symbol": sym, "confidence": e["confidence"],
        "opened_utc": e["timestamp_utc"],
        "entry": p["entry"], "sl": p["sl"], "tp1": p["tp1"], "tp2": p["tp2"],
        "current_price": price,
        "dist_to_sl_pct": dist_sl, "dist_to_tp1_pct": dist_tp1,
    }


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                     help="Jalankan ulang & kirim ulang notifikasi walau sudah jalan hari ini")
    args = ap.parse_args()

    _load_dotenv(ENV_PATH)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(DATA_DIR, exist_ok=True)
    archive_path = os.path.join(DATA_DIR, f"{today}.json")

    if os.path.exists(archive_path) and not args.force:
        logging.info(f"{today}: sudah dijalankan hari ini, keluar (pakai --force utk paksa ulang).")
        print(f"{today}: sudah dijalankan hari ini. Pakai --force utk paksa ulang.")
        return

    logging.info(f"=== mulai run {today} ===")
    try:
        universe, uni_meta = load_universe()
        cfg = dict(scr.DEFAULT_CFG)

        btc_bias, _ = scr.fetch_for_mode("BTCUSDT", "daily", True)
        if btc_bias is None:
            raise RuntimeError("gagal mengambil data BTCUSDT")
        regime = scoring.btc_regime(btc_bias)

        results, failed = [], []
        for sym in universe:
            bias, htf = scr.fetch_for_mode(sym, "daily", True)
            if bias is None:
                failed.append(sym)
                continue
            r = scoring.evaluate(sym, bias, htf, regime, cfg)
            if r is None:
                failed.append(sym)
                continue
            results.append(r)
        results.sort(key=lambda r: -r["total"])
    except Exception as e:      # noqa: BLE001
        logging.exception("daily_run gagal saat mengambil/menilai data")
        send_telegram(f"[ERROR] daily_run.py gagal pada {today}: {e}\n"
                       f"Cek daily_run.log di VPS. Tidak ada data baru hari ini.")
        sys.exit(1)

    candidates = [r for r in results if not r["vetoed"] and r["total"] >= cfg["min_score"]]

    records = jr.load_clean_records()
    open_pos = jr.open_positions(records)
    review = jr.compute_review(records)
    price_lookup = {r["symbol"]: r["price"] for r in results}
    open_pos_view = [_enrich_open_position(e, price_lookup) for e in open_pos]

    payload = _json_safe({
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "daily",
        "universe_freeze_date": uni_meta.get("freeze_date_utc"),
        "regime": regime,
        "candidates": [_candidate_view(r) for r in candidates],
        "all_results": [_candidate_view(r) for r in results],
        "failed_symbols": failed,
        "open_positions": open_pos_view,
        "journal_review": review,
        "dashboard_min_trades_for_stats": DASHBOARD_MIN_TRADES_FOR_STATS,
    })

    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    msg = compose_message(today, regime, len(open_pos), candidates, failed)
    ok = send_telegram(msg)
    logging.info(f"selesai: {len(candidates)} kandidat, {len(failed)} gagal, "
                 f"{len(open_pos)} posisi terbuka, telegram_ok={ok}")
    print(f"Selesai. {len(candidates)} kandidat, {len(open_pos)} posisi terbuka, "
          f"telegram {'terkirim' if ok else 'GAGAL -- cek daily_run.log'}.")


if __name__ == "__main__":
    main()
