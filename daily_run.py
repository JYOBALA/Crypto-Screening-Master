#!/usr/bin/env python3
"""
daily_run.py — Jalankan sekali sehari (cron), HANYA untuk 15 koin di
`universe_frozen.json` (TIDAK fetch universe dari volume hari ini — lihat
CLAUDE.md soal bahaya itu). Tulis SATU output yang bertambah tiap hari
(LONG + SHORT digabung, baris terbaru di atas) ke target yang dipilih lewat
OUTPUT_TARGET di .env: xlsx (ide_trade.xlsx, default), gsheet (Google
Sheets — bisa dibuka dari HP tanpa transfer file, lihat DEPLOY.md), atau
both. Lalu kirim ringkasan lewat kanal notifikasi pluggable (NOTIFY_CHANNEL
di .env: telegram/ntfy/discord/email/none — lihat send_notification()).
Exit setelah selesai — tidak ada proses yang menetap
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

    python daily_run.py            # jalan normal (skip kalau tanggal ini sudah ada di output)
    python daily_run.py --force    # timpa baris tanggal ini & kirim ulang notifikasi

Read-only terhadap akun: tidak ada API key, tidak ada order.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage

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

OUTPUT_TARGETS_VALID = ("xlsx", "gsheet", "both")
MAX_OPEN_POSITIONS = 3          # sama dgn KRITERIA_EVALUASI.md
MAX_CANDIDATES_IN_MESSAGE = 5    # per sisi (long/short)
DISCLAIMER = "Skor tidak prediktif (5 uji null). Ini penyaring perhatian, bukan sinyal."
SHORT_DISCLAIMER = "Kandidat SHORT belum pernah diuji (beda dari long yang sudah 5x null)."
FORBIDDEN_WORDS = ("beli", "peluang", "entry sekarang")

XLSX_NOTE = "IDE TRADE untuk dicek chart manual. BUKAN sinyal. Skor: 5 uji null. Short: belum diuji."
# entry_style: bedakan "harga pasar" dari "limit, tunggu pullback" -- tanpa ini entry
#   jauh di bawah harga terlihat seperti bug (lihat CLAUDE.md). Kolom "catatan" &
#   "onchain_aktivitas" dihapus 2026-09-09: tak pernah diisi kode & tak ada sumber
#   datanya di proyek read-only ini.
XLSX_COLUMNS = ["tanggal", "side", "ticker", "harga", "skor", "entry", "entry_style",
                "sl", "tp", "rr", "size_usd", "validasi", "chart"]
# kolom yang HARUS dikirim ke Google Sheets sebagai angka native (bukan teks) --
# kalau dikirim sbg string, lokal ID (titik=ribuan) menafsir ulang "1.2055" -> 12055
NUMERIC_GSHEET_COLS = {"harga", "skor", "entry", "sl", "tp", "rr", "size_usd"}
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
# Notifikasi -- pluggable per kanal, dipilih lewat NOTIFY_CHANNEL (.env).
# Titik masuk TUNGGAL adalah send_notification() di bawah -- _check_forbidden()
# dijalankan DI SITU, sebelum dispatch ke kanal manapun, supaya aturan bahasa
# tidak bisa lolos hanya krn menambah kanal baru dan lupa memanggilnya.
# ─────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID tidak diset (.env) -- notifikasi dilewati.")
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


def send_ntfy(text: str) -> bool:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        logging.error("NTFY_TOPIC tidak diset (.env) -- notifikasi ntfy dilewati.")
        return False
    headers = {"Title": "Screener harian"}
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.post(f"{server}/{topic}", data=text.encode("utf-8"),
                           headers=headers, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:      # noqa: BLE001
        logging.error(f"Gagal kirim ntfy: {e}")
        return False


def send_discord(text: str) -> bool:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        logging.error("DISCORD_WEBHOOK_URL tidak diset (.env) -- notifikasi discord dilewati.")
        return False
    content = text if len(text) <= 1900 else text[:1900] + "\n... (dipotong)"
    try:
        r = requests.post(webhook, json={"content": content}, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:      # noqa: BLE001
        logging.error(f"Gagal kirim Discord: {e}")
        return False


def send_email(text: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT", "587")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("EMAIL_TO")
    from_addr = os.environ.get("EMAIL_FROM", user)
    if not host or not to_addr or not from_addr:
        logging.error("SMTP_HOST/EMAIL_TO/EMAIL_FROM tidak lengkap (.env) -- notifikasi email dilewati.")
        return False
    msg = EmailMessage()
    msg["Subject"] = "Screener harian"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(text)
    try:
        with smtplib.SMTP(host, int(port), timeout=20) as smtp:
            smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception as e:      # noqa: BLE001
        logging.error(f"Gagal kirim email: {e}")
        return False


NOTIFY_SENDERS = {
    "telegram": send_telegram,
    "ntfy": send_ntfy,
    "discord": send_discord,
    "email": send_email,
}


def send_notification(text: str) -> bool:
    """Titik masuk tunggal notifikasi. NOTIFY_CHANNEL (.env) memilih kanal:
    telegram, ntfy, discord, email, none. 'none' dilewati TANPA dianggap
    gagal (return True). Kanal tidak dikenal/hilang dianggap gagal (return
    False) tapi tidak pernah melempar exception -- pemanggil (main()) tidak
    boleh crash gara-gara notifikasi, ide_trade.xlsx sudah tertulis duluan."""
    channel = os.environ.get("NOTIFY_CHANNEL", "telegram").strip().lower()
    if channel == "none":
        logging.info("NOTIFY_CHANNEL=none -- notifikasi dilewati (bukan kegagalan).")
        return True
    try:
        _check_forbidden(text)
    except AssertionError as e:
        logging.critical(f"Pesan diblokir sebelum kirim -- melanggar aturan bahasa: {e}")
        return False
    sender = NOTIFY_SENDERS.get(channel)
    if sender is None:
        logging.error(f"NOTIFY_CHANNEL={channel!r} tidak dikenal -- notifikasi dilewati. "
                       f"Pilihan valid: {', '.join(NOTIFY_SENDERS)}, none.")
        return False
    try:
        return sender(text)
    except Exception as e:      # noqa: BLE001
        logging.error(f"Gagal kirim notifikasi via {channel}: {e}")
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
        style = p.get("entry_style")
        lines.append(f"  entry {p['entry']}" + (f" ({style})" if style else "")
                     + f"  SL {p['sl']}  TP1 {p['tp1']} (R:R 1:{p['rr1']})")
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
# Baris output -- dipakai BERSAMA oleh xlsx & Google Sheets di bawah
# (kolom/urutan/idempotensi harus sama persis di kedua target).
# ─────────────────────────────────────────────────────────────

def build_output_rows(today_date, long_cands: list[dict], short_cands: list[dict]) -> list[dict]:
    combined = ([(r, "LONG", ss.VALIDASI_LONG) for r in long_cands]
                + [(r, "SHORT", ss.VALIDASI_SHORT) for r in short_cands])
    combined.sort(key=lambda x: -x[0]["total"])

    if not combined:
        return [{"tanggal": today_date, "side": "-", "ticker": "(tidak ada kandidat)",
                  "harga": None, "skor": None, "entry": None, "entry_style": "",
                  "sl": None, "tp": None, "rr": None,
                  "size_usd": None, "validasi": "",
                  "chart": None}]

    rows = []
    for r, side, validasi in combined:
        p = r["plan"]
        rows.append({
            "tanggal": today_date, "side": side, "ticker": r["symbol"],
            "harga": r["price"], "skor": r["total"],
            "entry": p["entry"], "entry_style": p.get("entry_style", ""),
            "sl": p["sl"], "tp": p["tp1"], "rr": p["rr1"],
            "size_usd": p.get("position_size"),
            "validasi": validasi,
            "chart": f"https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}",
        })
    return rows


# ─────────────────────────────────────────────────────────────
# ide_trade.xlsx -- satu spreadsheet yang bertambah, baris terbaru di atas
# ─────────────────────────────────────────────────────────────

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


def _sync_xlsx_header(ws) -> None:
    """Samakan baris catatan (row 1) + header (row 2) dgn XLSX_COLUMNS/XLSX_NOTE
    saat ini. Dipanggil tiap run supaya perubahan skema kolom (mis. entry_style
    ditambah, catatan/onchain_aktivitas dihapus -- 2026-09-09) tidak membuat
    header & data melenceng. Baris DATA lama tidak disentuh: kalau skemanya
    berubah, tinjau baris sebelum run ini secara manual (dicatat ke log)."""
    ncol = len(XLSX_COLUMNS)
    current = [ws.cell(row=2, column=c + 1).value for c in range(ncol)]
    if (current == XLSX_COLUMNS and ws.cell(row=1, column=1).value == XLSX_NOTE
            and ws.max_column == ncol):
        return
    logging.warning("Skema kolom ide_trade.xlsx berubah -- header row 1-2 ditulis ulang. "
                    "Baris data sebelum run ini masih memakai kolom lama.")
    for mr in list(ws.merged_cells.ranges):
        if mr.min_row == 1:
            ws.unmerge_cells(str(mr))
    # Buang kolom sisa skema lama (mis. catatan/onchain_aktivitas dihapus
    # 2026-09-09) supaya tidak ada kolom hantu di sebelah 'chart'. HANYA kalau
    # kolom itu benar-benar kosong di semua baris -- kalau masih ada data lama
    # di sana, biarkan & andalkan peringatan log di atas (kontrak: baris data
    # lama tidak disentuh).
    while ws.max_column > ncol:
        c = ws.max_column
        if any(ws.cell(row=r, column=c).value not in (None, "")
               for r in range(1, ws.max_row + 1)):
            break
        ws.delete_cols(c, 1)
    for c in range(1, max(ncol, ws.max_column) + 1):
        ws.cell(row=1, column=c).value = XLSX_NOTE if c == 1 else None
        ws.cell(row=2, column=c).value = XLSX_COLUMNS[c - 1] if c <= ncol else None
    for cell in ws[2]:
        cell.font = Font(bold=True)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)


def write_ide_trade_xlsx(path: str, rows: list[dict], today_str: str, force: bool) -> str:
    """Return 'written' atau 'skipped'. Melempar PermissionError apa adanya
    kalau file terkunci (mis. sedang dibuka di Excel) -- caller yang menangani
    fallback, supaya pesan errornya jelas di satu tempat."""
    if os.path.exists(path):
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        _sync_xlsx_header(ws)
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
# Google Sheets -- opsional (OUTPUT_TARGET=gsheet|both di .env). Kolom,
# urutan baris (terbaru di atas, baris 1=catatan, baris 2=header, data
# mulai baris 3) dan aturan idempotensi SAMA PERSIS dengan ide_trade.xlsx
# di atas -- dipakai bersama lewat XLSX_COLUMNS/XLSX_NOTE, bukan diduplikasi.
# gspread/google-auth diimpor LAZY di tiap fungsi supaya instalasi yang
# hanya pakai OUTPUT_TARGET=xlsx tidak wajib install dependensi ini.
# Auth pakai service account (bukan OAuth user) -- VPS jalan tanpa
# interaksi manusia, lihat DEPLOY.md.
# ─────────────────────────────────────────────────────────────

GSHEET_FILL_LONG = {"red": 0xD9 / 255, "green": 0xEA / 255, "blue": 0xD3 / 255}     # = FILL_LONG xlsx
GSHEET_FILL_SHORT = {"red": 0xF4 / 255, "green": 0xCC / 255, "blue": 0xCC / 255}    # = FILL_SHORT xlsx
GSHEET_MAX_RETRIES = 3


def _gsheet_client():
    import gspread
    creds_path = os.environ.get("GSHEET_CREDENTIALS", "gsheet_credentials.json")
    if not os.path.isabs(creds_path):
        creds_path = os.path.join(ROOT, creds_path)
    if not os.path.exists(creds_path):
        raise RuntimeError(f"File kredensial Google Sheets tidak ditemukan: {creds_path} "
                            f"(lihat DEPLOY.md soal cara membuat service account).")
    return gspread.service_account(filename=creds_path)


def _gsheet_retry(fn, *args, **kwargs):
    """Retry maks GSHEET_MAX_RETRIES kali dgn backoff eksponensial, HANYA
    utk error rate-limit/transient (429/503) -- error lain (403 izin
    ditolak, 404 spreadsheet salah, dst) dilempar langsung karena retry
    tidak akan memperbaikinya."""
    import gspread

    delay = 1.0
    for attempt in range(GSHEET_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = None
            try:
                status = e.response.status_code
            except Exception:      # noqa: BLE001
                pass
            if status in (429, 503) and attempt < GSHEET_MAX_RETRIES:
                logging.warning(f"Google Sheets API {status} -- retry {attempt + 1}/{GSHEET_MAX_RETRIES} "
                                 f"setelah {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise


def _gsheet_init_header(ws) -> None:
    last_col = get_column_letter(len(XLSX_COLUMNS))
    _gsheet_retry(ws.update, values=[[XLSX_NOTE] + [""] * (len(XLSX_COLUMNS) - 1), XLSX_COLUMNS],
                  range_name="A1")
    _gsheet_retry(ws.merge_cells, f"A1:{last_col}1")
    _gsheet_retry(ws.format, f"A2:{last_col}2", {"textFormat": {"bold": True}})
    _gsheet_retry(ws.freeze, rows=2)


def _gsheet_open_worksheet(gc, create_if_missing: bool):
    """Return worksheet, atau None kalau belum ada dan create_if_missing=False
    (dipakai _peek_gsheet_has_date -- worksheet belum ada berarti belum
    pernah jalan, bukan error)."""
    import gspread

    sheet_id = os.environ.get("GSHEET_ID")
    if not sheet_id:
        raise RuntimeError("GSHEET_ID tidak diset (.env).")
    ws_name = os.environ.get("GSHEET_WORKSHEET", "ide_trade")
    sh = _gsheet_retry(gc.open_by_key, sheet_id)
    try:
        return _gsheet_retry(sh.worksheet, ws_name)
    except gspread.exceptions.WorksheetNotFound:
        if not create_if_missing:
            return None
        ws = _gsheet_retry(sh.add_worksheet, title=ws_name, rows=1000, cols=len(XLSX_COLUMNS))
        _gsheet_init_header(ws)
        return ws


def _peek_gsheet_has_date(date_str: str) -> bool:
    """Cek cepat apakah tanggal ini sudah ada di baris 3. Dipanggil di guard
    idempotensi TOP-LEVEL (_already_ran_today, sebelum fetch data apa pun) --
    caller HARUS menangani exception apa pun dari sini sbg 'belum tentu
    sudah jalan' (fail-open), bukan sbg kegagalan run."""
    gc = _gsheet_client()
    ws = _gsheet_open_worksheet(gc, create_if_missing=False)
    if ws is None:
        return False
    row = _gsheet_retry(ws.row_values, 3)
    return bool(row) and row[0] == date_str


def write_gsheet_rows(rows: list[dict], today_str: str, force: bool) -> str:
    """Return 'written' atau 'skipped'. Melempar exception apa adanya kalau
    API Sheets gagal (kredensial hilang, GSHEET_ID salah, kuota, jaringan,
    dst, setelah retry 429/503 di _gsheet_retry habis) -- caller (main())
    menangani fallback ke CSV, sama seperti PermissionError di
    write_ide_trade_xlsx()."""
    from gspread.utils import ValueInputOption

    gc = _gsheet_client()
    ws = _gsheet_open_worksheet(gc, create_if_missing=True)

    all_values = _gsheet_retry(ws.get_all_values)
    existing_today_rows = [i for i, row in enumerate(all_values[2:], start=3)
                            if row and row[0] == today_str]

    if existing_today_rows:
        if not force:
            return "skipped"
        for i in sorted(existing_today_rows, reverse=True):
            _gsheet_retry(ws.delete_rows, i)

    values_matrix = []
    for rowdata in rows:
        row_vals = []
        for col in XLSX_COLUMNS:
            val = rowdata.get(col)
            if val is None or val == "":
                row_vals.append("")
            elif col == "tanggal":
                # tetap string -- guard idempotensi membandingkan row[0] == today_str
                row_vals.append(val.isoformat() if hasattr(val, "isoformat") else str(val))
            elif col in NUMERIC_GSHEET_COLS:
                # ANGKA NATIVE: float Python, bukan str(). Dengan RAW, Sheets tidak
                # menafsir ulang per lokal (ID: titik=ribuan, koma=desimal).
                row_vals.append(float(val))
            else:
                # teks apa adanya. "chart" = URL polos: Sheets auto-linkify saat
                # ditampilkan, dan tidak bergantung pemisah argumen rumus per lokal
                # (di lokal ID =HYPERLINK("a","b") gagal -> #ERROR! karena butuh ';').
                row_vals.append(str(val))
        values_matrix.append(row_vals)

    # RAW: angka native disimpan sbagai angka tanpa reparse lokal; string tetap string.
    _gsheet_retry(ws.insert_rows, values=values_matrix, row=3,
                  value_input_option=ValueInputOption.raw)

    last_col = get_column_letter(len(XLSX_COLUMNS))
    formats = []
    for offset, rowdata in enumerate(rows):
        r_idx = 3 + offset
        color = (GSHEET_FILL_SHORT if rowdata.get("side") == "SHORT" else
                 GSHEET_FILL_LONG if rowdata.get("side") == "LONG" else None)
        if color:
            formats.append({"range": f"A{r_idx}:{last_col}{r_idx}",
                             "format": {"backgroundColor": color}})
    if formats:
        _gsheet_retry(ws.batch_format, formats)

    return "written"


# ─────────────────────────────────────────────────────────────

def _resolve_output_target() -> str:
    target = os.environ.get("OUTPUT_TARGET", "xlsx").strip().lower()
    if target not in OUTPUT_TARGETS_VALID:
        logging.warning(f"OUTPUT_TARGET={target!r} tidak dikenal -- pakai default 'xlsx'. "
                         f"Pilihan valid: {', '.join(OUTPUT_TARGETS_VALID)}.")
        return "xlsx"
    return target


def _already_ran_today(today_str: str, target: str) -> bool:
    """Guard idempotensi TOP-LEVEL (sebelum fetch data apa pun). Kalau
    target mencakup gsheet dan pengecekannya gagal (mis. jaringan/kuota),
    anggap BELUM jalan (fail-open) -- lebih aman drpd diam-diam melewatkan
    run yang sah gara-gara Sheets API sedang bermasalah. write_*_rows()
    tetap melakukan pengecekan idempotensi sendiri sblm menulis, jadi
    fail-open di sini tidak bisa menduplikasi baris -- paling buruk cuma
    fetch data Binance yang terbuang."""
    if target in ("xlsx", "both") and _peek_xlsx_has_date(XLSX_PATH, today_str):
        return True
    if target in ("gsheet", "both"):
        try:
            if _peek_gsheet_has_date(today_str):
                return True
        except Exception as e:      # noqa: BLE001
            logging.warning(f"Gagal cek idempotensi Google Sheets, lanjut jalan spekulatif: {e}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                     help="Timpa baris tanggal ini di output & kirim ulang notifikasi")
    args = ap.parse_args()

    _load_dotenv(ENV_PATH)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )

    target = _resolve_output_target()
    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()

    if _already_ran_today(today_str, target) and not args.force:
        logging.info(f"{today_str}: sudah ada di output ({target}), keluar (pakai --force utk paksa ulang).")
        print(f"{today_str}: sudah dijalankan hari ini. Pakai --force utk paksa ulang.")
        return

    logging.info(f"=== mulai run {today_str} (OUTPUT_TARGET={target}) ===")
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
        send_notification(f"[ERROR] daily_run.py gagal pada {today_str}: {e}\n"
                           f"Cek daily_run.log di VPS. Tidak ada data baru hari ini.")
        sys.exit(1)

    long_cands = [r for r in long_results if not r["vetoed"] and r["total"] >= cfg["min_score"]]
    short_cands = [r for r in short_results if not r["vetoed"] and r["total"] >= cfg["min_score"]]

    rows = build_output_rows(today, long_cands, short_cands)
    statuses: dict[str, str] = {}
    fallback_written = False

    if target in ("xlsx", "both"):
        try:
            statuses["xlsx"] = write_ide_trade_xlsx(XLSX_PATH, rows, today_str, args.force)
        except PermissionError:
            logging.error(f"{XLSX_PATH} terkunci (kemungkinan sedang dibuka di Excel) -- tulis fallback CSV.")
            if not fallback_written:
                fb = write_fallback_csv(rows, today_str)
                fallback_written = True
                print(f"PERINGATAN: Tutup ide_trade.xlsx di Excel dulu -- file sedang terkunci.")
                print(f"Data hari ini AMAN, ditulis ke fallback: {fb}")
                print("Jalankan lagi 'python daily_run.py --force' setelah Excel ditutup utk menggabungkannya.")
            statuses["xlsx"] = "locked"

    if target in ("gsheet", "both"):
        try:
            statuses["gsheet"] = write_gsheet_rows(rows, today_str, args.force)
        except Exception as e:      # noqa: BLE001
            logging.exception("Gagal tulis ke Google Sheets -- tulis fallback CSV.")
            if not fallback_written:
                fb = write_fallback_csv(rows, today_str)
                fallback_written = True
                print(f"PERINGATAN: Google Sheets gagal ({e}) -- data hari ini AMAN, ditulis ke fallback: {fb}")
                print("Cek GSHEET_ID/GSHEET_CREDENTIALS/kuota di DEPLOY.md, lalu jalankan lagi dgn --force.")
            statuses["gsheet"] = "failed"

    if any(s == "skipped" for s in statuses.values()):
        logging.info(f"{today_str}: baris sudah ada di output (race dgn peek), tidak menulis ulang. "
                     f"status={statuses}")

    records = jr.load_clean_records()
    open_pos = jr.open_positions(records)

    msg = compose_message(today_str, regime, len(open_pos), long_cands, short_cands, failed, no_perp)
    channel = os.environ.get("NOTIFY_CHANNEL", "telegram").strip().lower()
    ok = send_notification(msg)
    logging.info(f"selesai: {len(long_cands)} long, {len(short_cands)} short, {len(failed)} gagal, "
                 f"{len(open_pos)} posisi terbuka, output={statuses}, notify_channel={channel}, notify_ok={ok}")
    if channel == "none":
        notify_status = "dilewati (NOTIFY_CHANNEL=none)"
    else:
        notify_status = "terkirim" if ok else "GAGAL -- cek daily_run.log"
    print(f"Selesai. {len(long_cands)} kandidat LONG, {len(short_cands)} kandidat SHORT, "
          f"{len(open_pos)} posisi terbuka, notifikasi ({channel}): {notify_status}.")
    if statuses.get("xlsx") == "written":
        print(f"ide_trade.xlsx: {XLSX_PATH}")
    if statuses.get("gsheet") == "written":
        print(f"Google Sheets: spreadsheet {os.environ.get('GSHEET_ID')} "
              f"(worksheet {os.environ.get('GSHEET_WORKSHEET', 'ide_trade')})")


if __name__ == "__main__":
    main()
