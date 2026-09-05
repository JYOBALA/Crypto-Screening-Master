# Deploy — VPS Ubuntu

Setup untuk menjalankan `daily_run.py` via cron sekali sehari. Baca
`KRITERIA_EVALUASI.md` dan `RINGKASAN_AKHIR.md` dulu kalau belum — VPS ini
menjalankan penyaring perhatian, bukan sinyal beli otomatis.

---

## 1. Setup awal VPS (Ubuntu 22.04/24.04)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

git clone <url-repo-anda> screening-crypto
cd screening-crypto

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Cek koneksi ke Binance dari VPS ini (mungkin BEDA dari mesin Anda —
lihat catatan blocker DNS di `CLAUDE.md`):

```bash
python3 diagnose.py
```

Kalau ini gagal dengan `SSLError`/timeout, VPS Anda kemungkinan berada di
jaringan yang juga memblokir `api.binance.com`. **Tidak perlu diapa-apakan** —
`data-api.binance.vision` sudah jadi endpoint DEFAULT di `screener.py`
(`BASE_CANDIDATES`), jadi perilakunya sama persis di kedua mesin tanpa env
var manual. Kalau VPS Anda TIDAK diblokir dan `api.binance.com` justru lebih
cepat, sistem tetap jalan — endpoint dipilih otomatis, cuma urutan cobanya
yang beda.

**Futures (`/fapi/*`, dipakai `short_scan.py`)** ada di host TERPISAH
(`fapi.binance.com`) dan TIDAK punya padanan `data-api.binance.vision`.
Ini bisa saja diblokir juga di jaringan yang sama walau `api.binance.com`
sudah diperbaiki — tidak sempat diverifikasi dari mesin dev proyek ini
(kedua-duanya terblokir di sana). Cek manual:
```bash
python3 -c "import short_scan as ss; print(ss.check_perp_availability(['BTCUSDT']))"
```
Kalau hasilnya `{'BTCUSDT': False}`, host futures terblokir di VPS ini juga —
`daily_run.py` tetap jalan (gagal aman: semua kandidat short dikeluarkan,
LONG tidak terpengaruh), tapi sisi short tidak akan pernah menghasilkan
kandidat sampai konektivitas ini diperbaiki (VPN, host alternatif, dll).

---

## 2. Buat bot Telegram

1. Buka Telegram, cari **@BotFather**, kirim `/newbot`.
2. Ikuti instruksinya (nama bot, username harus berakhiran `bot`).
3. BotFather memberi **token** seperti `123456789:AAExampleTokenHere` — simpan.
4. Kirim pesan apa saja ke bot Anda (baru dibuat) supaya bot punya percakapan
   dengan Anda.
5. Ambil **chat ID** Anda:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
   ```
   Cari field `"chat":{"id": ...}` di respons JSON-nya — itu chat ID Anda.

---

## 3. Konfigurasi `.env`

```bash
cp .env.example .env
nano .env
```

Isi:
```
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenHere
TELEGRAM_CHAT_ID=123456789
```

`.env` **tidak boleh** ter-commit (sudah di `.gitignore`). Cek:
```bash
git check-ignore -v .env    # harus menampilkan baris .gitignore yang cocok
```

Uji kirim notifikasi manual dulu (jangan pakai `--force` berulang-ulang di
hari yang sama di produksi — itu untuk testing saja):

```bash
python3 daily_run.py --force
```

Kalau berhasil, pesan masuk ke Telegram Anda dan `ide_trade.xlsx` (baris hari
ini disisipkan di paling atas, di bawah baris catatan+header) terisi.

---

## 4. Crontab — sekali sehari, setelah candle harian close

Candle harian Binance close di **00:00 UTC**. `fetch_klines` sudah membuang
candle yang belum close (`df.iloc[:-1]`), tapi beri jeda 5 menit supaya data
klines benar-benar sudah tersedia di sisi Binance.

Cek timezone VPS dulu:
```bash
timedatectl
```

**Kalau timezone VPS = UTC** (rekomendasi — set dgn `sudo timedatectl set-timezone UTC`
supaya crontab & log gampang dibaca):
```
5 0 * * * cd /path/ke/screening-crypto && .venv/bin/python3 daily_run.py >> cron_daily_run.log 2>&1
```

**Kalau timezone VPS BUKAN UTC**, konversi 00:05 UTC ke jam lokal VPS dulu
sebelum menulis jadwal cron (cron memakai jam lokal sistem, bukan UTC).

Edit crontab: `crontab -e`, tempel baris di atas (sesuaikan path).

`ide_trade.xlsx` ada di working directory repo di VPS. Untuk melihatnya,
sinkronkan ke mesin lokal Anda (mis. `scp user@vps:/path/ke/screening-crypto/ide_trade.xlsx .`)
lalu buka di Excel — **tutup dulu salinan lokal sebelum `scp` menimpanya**,
dan lihat bagian 6 soal file terkunci kalau sedang dibuka di sisi VPS
(seharusnya tidak pernah terjadi karena tidak ada proses yang membuka Excel
di server, tapi lihat penanganan `PermissionError` di `daily_run.py` kalau
suatu saat file disinkronkan lewat mekanisme yang menguncinya, mis. Dropbox/
OneDrive sync sedang berjalan).

---

## 5. Cek log kalau cron "gagal diam-diam"

Cron yang gagal biasanya tidak muncul di mana-mana kalau outputnya tidak
diarahkan. Baris crontab di atas SUDAH mengarahkan stdout+stderr ke
`cron_daily_run.log` — cek itu dulu:

```bash
tail -50 cron_daily_run.log
tail -50 daily_run.log        # log internal daily_run.py (logging module)
```

Tanda-tanda gagal:
- Baris tanggal hari ini **tidak muncul** di paling atas `ide_trade.xlsx` ->
  cron tidak jalan sama sekali, atau gagal sebelum sempat menulis. Cek
  `cron_daily_run.log`.
- Ada file `data/ide_trade_YYYY-MM-DD.csv` -> `ide_trade.xlsx` terkunci saat
  cron jalan (lihat pesan `PermissionError` di `daily_run.log`); data hari
  itu AMAN di CSV fallback tapi belum tergabung. Tutup apa pun yang mengunci
  `ide_trade.xlsx`, lalu `python3 daily_run.py --force` utk menggabungkannya.
- Tidak ada notifikasi Telegram TAPI baris hari ini ADA di `ide_trade.xlsx`
  -> masalah di `send_telegram()` (cek `.env`, cek `daily_run.log` utk baris
  `ERROR` atau `CRITICAL`).
- `daily_run.log` berisi `[ERROR] daily_run.py gagal` -> masalah jaringan/data,
  cek traceback lengkap di atas baris itu (`logging.exception` menulis
  traceback penuh).
- Cron tidak jalan sama sekali tapi tidak ada file log juga -> cek dulu cron
  daemon aktif: `systemctl status cron`. Cek juga crontab benar-benar
  tersimpan: `crontab -l`.
- Selalu ada JEJAK: kalau tidak ada notifikasi DAN tidak ada apa pun di
  kedua log itu, curigai path `cd` di baris crontab salah (cron tidak
  mewarisi `$PATH`/direktori kerja shell interaktif Anda) — selalu pakai
  path absolut di crontab.

Uji manual dgn environment yang SAMA seperti cron (tanpa shell interaktif):
```bash
env -i /bin/sh -c 'cd /path/ke/screening-crypto && .venv/bin/python3 daily_run.py --force'
```

---

## Ringkasan file yang terlibat

| File | Peran |
|---|---|
| `daily_run.py` | Orkestrasi harian (cron) — TIDAK menetap sbg proses |
| `universe_frozen.json` | 15 simbol dibekukan (`KRITERIA_EVALUASI.md`) — daily_run.py TIDAK fetch universe dari volume hari ini |
| `.env` | Secret Telegram, TIDAK di-commit |
| `ide_trade.xlsx` | SATU spreadsheet yang bertambah tiap hari (LONG+SHORT, baris terbaru di atas), TIDAK di-commit. Baris tanggal hari ini jg jadi guard idempotensi |
| `data/ide_trade_YYYY-MM-DD.csv` | Fallback DARURAT — hanya muncul kalau `ide_trade.xlsx` terkunci saat cron jalan. Kalau ada, gabungkan manual dgn `--force` setelah file dibuka |
| `short_scan.py` | Kandidat SHORT (belum pernah diuji), cek perp `/fapi/*` (host beda dari spot, lihat bagian 1) |
| `journal.jsonl` | Data trading pribadi, TIDAK di-commit, dibaca `daily_run.py` (read-only, hanya utk hitung posisi terbuka) |
