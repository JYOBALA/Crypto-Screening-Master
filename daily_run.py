#!/usr/bin/env python3
"""
daily_run.py — Jalankan sekali sehari (cron), HANYA untuk 15 koin di
`universe_frozen.json` (TIDAK fetch universe dari volume hari ini — lihat
CLAUDE.md soal bahaya itu). Tulis SATU spreadsheet yang bertambah tiap hari,
ide_trade.xlsx (LONG + SHORT digabung, baris terbaru di atas), lalu kirim
ringkasan ke Telegram. Exit setelah selesai — tidak ada proses yang menetap
(dijadwalkan lewat cron, lihat DEPLOY.md). Tidak ada dashboard lagi — dicabut,
lihat CLAUDE.md.

PRINSIP DESAIN (lihat RINGKASAN_AKHIR.md — skor screener TERBUKTI tidak
prediktif): alat ini adalah PENYARING PERHATIAN, bukan penghasil sinyal.
Maksimal SATU notifikasi per hari (dijamin dua lapis: crontab sekali/hari
DAN guard idempotensi di bawah — jalan dua kali di hari yang sama tidak
mengirim dua notifikasi ATAU menduplikasi baris). Tidak ada kata
"beli"/"sinyal" (di luar baris disclaimer wajib)/"peluang"/"entry sekarang"
di pesan — divalidasi otomatis sebelum kirim, lihat _check_forbidden().

SISI SHORT (short_scan.py) belum pernah diuji sama sekali -- beda dari long
yang sudah 5x null. Kolom `validasi` di ide_trade.xlsx dan baris terpisah di
Telegram menandai ini secara eksplisit di SETIAP kandidat short.

    python daily_run.py            # jalan normal (skip kalau tanggal ini sudah ada di xlsx)
    python daily_run.py --force    # timpa baris tanggal ini & kirim ulang notifikasi

Read-only terhadap akun: tidak ada API key, tidak ada order.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests
import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr      # noqa: E402  (BASE_CANDIDATES/_switch_base/_force_utf8 via import,
                             #   fetch_for_mode, DEFAULT_CFG -- WAJIB reuse, lihat CLAUDE.md)
import scoring                # noqa: E402
import short_scan as ss      # noqa: E402  (kandidat SHORT -- BELUM PERNAH DIUJI, lihat modul itu)
import journal as jr         # noqa: E402  (open_positions -- dipakai utk gerbang batas posisi.
                             #   compute_review() TETAP di journal.py, tapi tidak dipanggil di sini
                             #   lagi krn dashboard sudah dicabut)

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
UNIVERSE_PATH = os.path.join(ROOT, "universe_frozen.json")
XLSX_PATH = os.path.join(ROOT, "ide_trade.xlsx")
DATA_DIR = os.path.join(ROOT, "data")     # HANYA dipakai utk fallback kalau xlsx terkunci
LOG_PATH = os.path.join(ROOT, "daily_run.log")

MAX_OPEN_POSITIONS = 3          # sama dgn KRITERIA_EVALUASI.md
MAX_CANDIDATES_IN_MESSAGE = 5    # per sisi (long/short)
DISCLAIMER = "Skor tidak prediktif (5 uji null). Ini penyaring perhatian, bukan sinyal."
SHORT_DISCLAIMER = "Kandidat SHORT belum pernah diuji (beda dari long yang sudah 5x null)."
FORBIDDEN_WORDS = ("beli", "peluang", "entry sekarang")

XLSX_NOTE = "IDE TRADE untuk dicek chart manual. BUKAN sinyal. Skor: 5 uji null. Short: belum diuji."
XLSX_COLUMNS = ["tanggal", "side", "ticker", "harga", "skor", "entry", "sl", "tp", "rr",
                "size_usd", "catatan", "validasi", "onchain_aktivitas", "chart"]
FILL_LONG = PatternFill(start_color="FFD9EAD3", end_color="FFD9EAD3", fill_type="solid")   # hijau pucat
FILL_SHORT = PatternFill(start_color="FFF4CCCC", end_color="FFF4CCCC", fill_type="solid")  # merah pucat


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


def _format_side_block(label: str, candidates: list[dict], extra_disclaimer: str | None) -> list[str]:
    lines = [f"Kandidat {label} untuk dicek chart ({len(candidates)}):"]
    if extra_disclaimer:
        lines.append(extra_disclaimer)
    for r in candidates[:MAX_CANDIDATES_IN_MESSAGE]:
        p = r["plan"]
        fund = ""
        if r.get("funding_rate_last_8h_pct") is not None:
            fund = f"  funding 8j terakhir: {r['funding_rate_last_8h_pct']:+.4f}%"
        lines.append(f"- {r['symbol']}  skor {r['total']}/100 ({r['grade']})  harga {r['price']}{fund}")
        lines.append(f"  entry {p['entry']}  SL {p['sl']}  TP1 {p['tp1']} (R:R 1:{p['rr1']})")
        lines.append(f"  https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}")
    rest = len(candidates) - MAX_CANDIDATES_IN_MESSAGE
    if rest > 0:
        lines.append(f"...dan {rest} lainnya (lihat ide_trade.xlsx).")
    return lines


def compose_message(date_str: str, regime: dict, n_open: int,
                     long_cands: list[dict], short_cands: list[dict],
                     failed: list[str], no_perp: list[str]) -> str:
    lines = [f"Screener harian -- {date_str}",
             f"BTC: {regime['status']} -- {regime['message']}",
             f"Posisi terbuka: {n_open}/{MAX_OPEN_POSITIONS}", ""]

    if n_open >= MAX_OPEN_POSITIONS:
        lines.append("Batas 3 posisi tercapai. Tidak ada yang perlu dicek hari ini.")
    else:
        if long_cands:
            lines += _format_side_block("LONG", long_cands, None)
        else:
            lines.append("Tidak ada kandidat LONG untuk dicek chart hari ini.")
        lines.append("")
        if short_cands:
            lines += _format_side_block("SHORT", short_cands, SHORT_DISCLAIMER)
        else:
            lines.append("Tidak ada kandidat SHORT untuk dicek chart hari ini.")

    if no_perp:
        lines.append("")
        lines.append(f"{len(no_perp)} koin di universe tidak punya perpetual futures -- "
                      f"dikeluarkan dari kandidat short: {', '.join(no_perp)}")

    if failed:
        lines.append("")
        lines.append(f"Gagal ambil data: {', '.join(failed)}")

    lines += ["", DISCLAIMER]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# ide_trade.xlsx -- satu spreadsheet yang bertambah, baris terbaru di atas
# ─────────────────────────────────────────────────────────────

def build_xlsx_rows(today_date, long_cands: list[dict], short_cands: list[dict]) -> list[dict]:
    combined = ([(r, "LONG", ss.VALIDASI_LONG) for r in long_cands]
                + [(r, "SHORT", ss.VALIDASI_SHORT) for r in short_cands])
    combined.sort(key=lambda x: -x[0]["total"])

    if not combined:
        return [{"tanggal": today_date, "side": "-", "ticker": "(tidak ada kandidat)",
                  "harga": None, "skor": None, "entry": None, "sl": None, "tp": None, "rr": None,
                  "size_usd": None, "catatan": "", "validasi": "", "onchain_aktivitas": "",
                  "chart": None}]

    rows = []
    for r, side, validasi in combined:
        p = r["plan"]
        rows.append({
            "tanggal": today_date, "side": side, "ticker": r["symbol"],
            "harga": r["price"], "skor": r["total"],
            "entry": p["entry"], "sl": p["sl"], "tp": p["tp1"], "rr": p["rr1"],
            "size_usd": p.get("position_size"),
            "catatan": "", "validasi": validasi, "onchain_aktivitas": "",
            "chart": f"https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}",
        })
    return rows


def _cell_date_str(v) -> str:
    """openpyxl membaca kembali sel bertipe tanggal sebagai datetime.datetime
    (bukan date), walau yang ditulis awalnya objek date -- .isoformat() datetime
    menyertakan jam ('...T00:00:00') dan TIDAK PERNAH cocok dgn date_str biasa.
    Normalisasi ke tanggal saja dulu."""
    if hasattr(v, "date") and callable(v.date):
        return v.date().isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _peek_xlsx_has_date(path: str, date_str: str) -> bool:
    """Cek cepat (tanpa load penuh) apakah baris tanggal ini sudah ada --
    baris terbaru SELALU disisipkan di row 3 (setelah catatan+header), jadi
    cukup intip row 3 kolom A."""
    if not os.path.exists(path):
        return False
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        v = ws.cell(row=3, column=1).value
        wb.close()
    except Exception:      # noqa: BLE001
        return False
    return _cell_date_str(v) == date_str


def write_ide_trade_xlsx(path: str, rows: list[dict], today_str: str, force: bool) -> str:
    """Return 'written' atau 'skipped'. Melempar PermissionError apa adanya
    kalau file terkunci (mis. sedang dibuka di Excel) -- caller yang menangani
    fallback, supaya pesan errornya jelas di satu tempat."""
    if os.path.exists(path):
        wb = openpyxl.load_workbook(path)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ide_trade"
        ws.append([XLSX_NOTE])
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(XLSX_COLUMNS))
        ws.append(XLSX_COLUMNS)
        for cell in ws[2]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A3"

    existing_today_rows = []
    for i in range(3, ws.max_row + 1):
        v = ws.cell(row=i, column=1).value
        if _cell_date_str(v) == today_str:
            existing_today_rows.append(i)

    if existing_today_rows:
        if not force:
            return "skipped"
        for i in sorted(existing_today_rows, reverse=True):
            ws.delete_rows(i, 1)

    n = len(rows)
    ws.insert_rows(3, amount=n)
    for offset, rowdata in enumerate(rows):
        r_idx = 3 + offset
        for c_idx, col in enumerate(XLSX_COLUMNS, start=1):
            cell = ws.cell(row=r_idx, column=c_idx)
            val = rowdata[col]
            if col == "tanggal" and val is not None:
                cell.value = val
                cell.number_format = "yyyy-mm-dd"
            elif col == "chart" and val:
                cell.value = "Chart"
                cell.hyperlink = val
                cell.font = Font(color="0563C1", underline="single")
            else:
                cell.value = val
        fill = FILL_SHORT if rowdata.get("side") == "SHORT" else (
            FILL_LONG if rowdata.get("side") == "LONG" else None)
        if fill:
            for c_idx in range(1, len(XLSX_COLUMNS) + 1):
                ws.cell(row=r_idx, column=c_idx).fill = fill

    last_row = ws.max_row
    ws.auto_filter.ref = f"A2:{get_column_letter(len(XLSX_COLUMNS))}{last_row}"

    for c_idx, col in enumerate(XLSX_COLUMNS, start=1):
        maxlen = len(col)
        for r_idx in range(2, last_row + 1):      # lewati baris catatan (row 1)
            v = ws.cell(row=r_idx, column=c_idx).value
            if v is not None:
                maxlen = max(maxlen, len(str(v)))
        ws.column_dimensions[get_column_letter(c_idx)].width = min(maxlen + 2, 40)

    wb.save(path)      # PermissionError kalau file sedang dibuka -> dilempar apa adanya
    return "written"


def write_fallback_csv(rows: list[dict], today_str: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"ide_trade_{today_str}.csv")
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                     help="Timpa baris tanggal ini di ide_trade.xlsx & kirim ulang notifikasi")
    args = ap.parse_args()

    _load_dotenv(ENV_PATH)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )

    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()

    if _peek_xlsx_has_date(XLSX_PATH, today_str) and not args.force:
        logging.info(f"{today_str}: sudah ada di ide_trade.xlsx, keluar (pakai --force utk paksa ulang).")
        print(f"{today_str}: sudah dijalankan hari ini. Pakai --force utk paksa ulang.")
        return

    logging.info(f"=== mulai run {today_str} ===")
    try:
        universe, uni_meta = load_universe()
        cfg = dict(scr.DEFAULT_CFG)

        btc_bias, _ = scr.fetch_for_mode("BTCUSDT", "daily", True)
        if btc_bias is None:
            raise RuntimeError("gagal mengambil data BTCUSDT")
        regime = scoring.btc_regime(btc_bias)

        perp_avail = ss.check_perp_availability(universe)
        no_perp = [s for s in universe if not perp_avail[s]]
        logging.info(f"perp tersedia: {sum(perp_avail.values())}/{len(universe)}. "
                     f"Tidak bisa di-short: {no_perp}")

        long_results, short_results, failed = [], [], []
        for sym in universe:
            bias, htf = scr.fetch_for_mode(sym, "daily", True)
            if bias is None:
                failed.append(sym)
                continue
            rl = scoring.evaluate(sym, bias, htf, regime, cfg)
            if rl is not None:
                long_results.append(rl)
            else:
                failed.append(sym)

            if perp_avail[sym]:
                rs = ss.evaluate_short(sym, bias, htf, regime, cfg)
                if rs is not None:
                    short_results.append(rs)

        long_results.sort(key=lambda r: -r["total"])
        short_results.sort(key=lambda r: -r["total"])
    except Exception as e:      # noqa: BLE001
        logging.exception("daily_run gagal saat mengambil/menilai data")
        send_telegram(f"[ERROR] daily_run.py gagal pada {today_str}: {e}\n"
                       f"Cek daily_run.log di VPS. Tidak ada data baru hari ini.")
        sys.exit(1)

    long_cands = [r for r in long_results if not r["vetoed"] and r["total"] >= cfg["min_score"]]
    short_cands = [r for r in short_results if not r["vetoed"] and r["total"] >= cfg["min_score"]]

    rows = build_xlsx_rows(today, long_cands, short_cands)
    try:
        status = write_ide_trade_xlsx(XLSX_PATH, rows, today_str, args.force)
    except PermissionError:
        logging.error(f"{XLSX_PATH} terkunci (kemungkinan sedang dibuka di Excel) -- tulis fallback CSV.")
        fb = write_fallback_csv(rows, today_str)
        print(f"PERINGATAN: Tutup ide_trade.xlsx di Excel dulu -- file sedang terkunci.")
        print(f"Data hari ini AMAN, ditulis ke fallback: {fb}")
        print("Jalankan lagi 'python daily_run.py --force' setelah Excel ditutup utk menggabungkannya.")
        status = "locked"

    if status == "skipped":
        logging.info(f"{today_str}: baris sudah ada di ide_trade.xlsx (race dgn peek), tidak menulis ulang.")

    records = jr.load_clean_records()
    open_pos = jr.open_positions(records)

    msg = compose_message(today_str, regime, len(open_pos), long_cands, short_cands, failed, no_perp)
    ok = send_telegram(msg)
    logging.info(f"selesai: {len(long_cands)} long, {len(short_cands)} short, {len(failed)} gagal, "
                 f"{len(open_pos)} posisi terbuka, xlsx={status}, telegram_ok={ok}")
    print(f"Selesai. {len(long_cands)} kandidat LONG, {len(short_cands)} kandidat SHORT, "
          f"{len(open_pos)} posisi terbuka, telegram {'terkirim' if ok else 'GAGAL -- cek daily_run.log'}.")
    if status == "written":
        print(f"ide_trade.xlsx: {XLSX_PATH}")


if __name__ == "__main__":
    main()
