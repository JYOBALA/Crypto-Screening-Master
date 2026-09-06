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

## 2. Pilih target output: xlsx / Google Sheets / both

`daily_run.py` menulis SATU output yang bertambah tiap hari (LONG+SHORT
digabung, baris terbaru di atas). Pilih lewat `OUTPUT_TARGET` di `.env`:

- `xlsx` (default) — `ide_trade.xlsx` lokal di VPS, seperti sebelumnya.
- `gsheet` — Google Sheets. Bisa dibuka dari HP kapan saja tanpa `scp`/sinkron
  manual, dan sekaligus jadi cadangan di luar VPS (kalau VPS hilang/rusak,
  datanya tetap ada di Google).
- `both` — tulis ke keduanya. Berguna masa transisi, atau sbg cadangan ganda.

Kalau target mencakup `gsheet` dan penulisan gagal (jaringan, kuota API,
izin dicabut, dst) setelah retry, `daily_run.py` **tidak pernah kehilangan
data hari itu** — otomatis fallback ke `data/ide_trade_YYYY-MM-DD.csv`,
persis seperti fallback saat `ide_trade.xlsx` terkunci (lihat bagian 6).

### Setup Google Sheets (kalau `OUTPUT_TARGET=gsheet` atau `both`)

1. **Buat/pilih project di Google Cloud Console**: https://console.cloud.google.com/
2. **Enable Google Sheets API**: menu "APIs & Services" → "Enable APIs and
   Services" → cari "Google Sheets API" → Enable. (Drive API TIDAK perlu
   diaktifkan — `daily_run.py` hanya buka spreadsheet lewat ID, bukan lewat
   pencarian nama di Drive.)
3. **Buat service account**: "APIs & Services" → "Credentials" → "Create
   Credentials" → "Service account". Beri nama bebas (mis.
   `ide-trade-spreadsheet`), lanjut tanpa perlu memberi role project apa pun
   (izin akses diberikan langsung di level spreadsheet, lihat langkah 5).
4. **Buat key JSON**: buka service account yang baru dibuat → tab "Keys" →
   "Add Key" → "Create new key" → pilih **JSON** → download. File ini berisi
   `private_key` — **kunci akses penuh ke spreadsheet manapun yang Anda
   share ke service account ini**, sama sensitifnya dengan `.env`.
   - Salin file itu ke VPS sbg `gsheet_credentials.json` di root repo (atau
     path lain, lalu set `GSHEET_CREDENTIALS` ke path itu di `.env`).
   - **`chmod 600 gsheet_credentials.json`** di VPS — hanya user yang
     menjalankan cron yang boleh bisa membacanya.
   - File ini sudah tercakup `.gitignore` (pola `*.json` + entri eksplisit) —
     tapi tetap cek manual sebelum commit apa pun:
     ```bash
     git status                                   # gsheet_credentials.json TIDAK boleh muncul
     git check-ignore -v gsheet_credentials.json   # harus menampilkan baris .gitignore yang cocok
     ```
5. **Buat spreadsheet baru di Google Sheets** (drive.google.com → Blank
   spreadsheet), lalu **Share** ke alamat email service account (field
   `client_email` di file JSON, bentuknya
   `nama-service-account@nama-project.iam.gserviceaccount.com`) dgn akses
   **Editor**. Tanpa langkah ini, `daily_run.py` akan gagal dgn error izin
   (403) walau kredensialnya benar.
6. **Ambil `GSHEET_ID`** dari URL spreadsheet:
   `https://docs.google.com/spreadsheets/d/INI_BAGIAN_YANG_DIAMBIL/edit` —
   ID-nya adalah string panjang antara `/d/` dan `/edit`.
7. Isi di `.env`: `OUTPUT_TARGET=gsheet` (atau `both`), `GSHEET_ID`,
   `GSHEET_CREDENTIALS` (default `gsheet_credentials.json`, cukup dibiarkan
   kalau filenya memang ditaruh di root repo), `GSHEET_WORKSHEET` (default
   `ide_trade` — worksheet dgn nama ini dibuat otomatis kalau belum ada,
   lengkap dgn baris catatan+header, saat run pertama).

Karena spreadsheet-nya sendiri sudah bisa dibuka & dicek dari HP kapan saja,
**`NOTIFY_CHANNEL=none` jadi pilihan yang wajar** kalau `OUTPUT_TARGET`
mencakup `gsheet` — tidak perlu notifikasi push sama sekali, tinggal buka
spreadsheet-nya. Lihat bagian 3 untuk opsi notifikasi kalau tetap mau dapat
ringkasan tanpa buka spreadsheet.

---

## 3. Siapkan kanal notifikasi

Notifikasi bersifat pluggable — pilih SATU kanal lewat `NOTIFY_CHANNEL` di
`.env`: `telegram`, `ntfy`, `discord`, `email`, atau `none` (lewati kirim
sama sekali, tanpa dianggap gagal — `ide_trade.xlsx` tetap ditulis seperti
biasa). Default kalau `NOTIFY_CHANNEL` tidak diset: `telegram`. Semua kanal
tunduk pada aturan bahasa yang sama (`_check_forbidden()` di
`daily_run.py`) — dicek satu kali di `send_notification()` sebelum dispatch
ke kanal manapun, bukan per-kanal.

### Opsi A — Telegram

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
6. Isi `NOTIFY_CHANNEL=telegram`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### Opsi B — ntfy

1. Pilih nama topic acak yang sulit ditebak orang lain (siapa pun yang tahu
   nama topic di server publik `ntfy.sh` bisa membaca notifikasi Anda) —
   mis. `screener-abc123xyz`.
2. Install app ntfy (Android/iOS) atau buka `https://ntfy.sh/<topic-anda>`
   di browser, lalu subscribe ke topic itu.
3. Isi `NOTIFY_CHANNEL=ntfy`, `NTFY_TOPIC=<topic-anda>`. Opsional:
   `NTFY_SERVER` kalau pakai server ntfy sendiri (bukan `ntfy.sh`), atau
   `NTFY_TOKEN` kalau topic diproteksi auth.

### Opsi C — Discord

1. Di server Discord Anda: Server Settings → Integrations → Webhooks →
   New Webhook. Pilih channel tujuan, salin **Webhook URL**.
2. Isi `NOTIFY_CHANNEL=discord`, `DISCORD_WEBHOOK_URL=<url-webhook>`.

### Opsi D — Email (SMTP)

1. Siapkan akun pengirim + kredensial SMTP. Untuk Gmail, pakai **App
   Password** (bukan password akun biasa — butuh 2FA aktif dulu):
   `https://myaccount.google.com/apppasswords`.
2. Isi `NOTIFY_CHANNEL=email`, `SMTP_HOST`, `SMTP_PORT` (biasanya 587),
   `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`.

### Opsi E — Tanpa notifikasi

Isi `NOTIFY_CHANNEL=none`. Output (`ide_trade.xlsx` dan/atau spreadsheet
Google Sheets, sesuai `OUTPUT_TARGET` di bagian 2) tetap tertulis tiap hari
seperti biasa — cukup buka itu langsung (lihat bagian 5) untuk melihat
hasilnya.

---

## 4. Konfigurasi `.env`

```bash
cp .env.example .env
nano .env
```

Isi sesuai target output (bagian 2) & kanal notifikasi (bagian 3) yang
dipilih — lihat `.env.example` untuk daftar lengkap variabel per opsi
(hanya variabel target/kanal yang aktif yang perlu diisi).

`.env` **tidak boleh** ter-commit (sudah di `.gitignore`). Cek:
```bash
git check-ignore -v .env    # harus menampilkan baris .gitignore yang cocok
```

Uji kirim notifikasi manual dulu (jangan pakai `--force` berulang-ulang di
hari yang sama di produksi — itu untuk testing saja):

```bash
python3 daily_run.py --force
```

Kalau berhasil, pesan masuk lewat kanal yang Anda pilih (kecuali
`NOTIFY_CHANNEL=none`) dan output (`ide_trade.xlsx` dan/atau spreadsheet
Google Sheets, tergantung `OUTPUT_TARGET`) terisi — baris hari ini
disisipkan di paling atas, di bawah baris catatan+header.

---

## 5. Crontab — sekali sehari, setelah candle harian close

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

Kalau `OUTPUT_TARGET` mencakup `gsheet`: tidak ada langkah tambahan di
sini — spreadsheet-nya langsung terupdate begitu cron jalan, tinggal buka
dari HP/browser mana pun (itulah gunanya, lihat bagian 2).

Kalau `OUTPUT_TARGET=xlsx`: `ide_trade.xlsx` ada di working directory repo
di VPS. Untuk melihatnya, sinkronkan ke mesin lokal Anda (mis.
`scp user@vps:/path/ke/screening-crypto/ide_trade.xlsx .`) lalu buka di
Excel — **tutup dulu salinan lokal sebelum `scp` menimpanya**, dan lihat
bagian 6 soal file terkunci kalau sedang dibuka di sisi VPS (seharusnya
tidak pernah terjadi karena tidak ada proses yang membuka Excel di server,
tapi lihat penanganan `PermissionError` di `daily_run.py` kalau suatu saat
file disinkronkan lewat mekanisme yang menguncinya, mis. Dropbox/OneDrive
sync sedang berjalan).

---

## 6. Cek log kalau cron "gagal diam-diam"

Cron yang gagal biasanya tidak muncul di mana-mana kalau outputnya tidak
diarahkan. Baris crontab di atas SUDAH mengarahkan stdout+stderr ke
`cron_daily_run.log` — cek itu dulu:

```bash
tail -50 cron_daily_run.log
tail -50 daily_run.log        # log internal daily_run.py (logging module)
```

Tanda-tanda gagal:
- Baris tanggal hari ini **tidak muncul** di paling atas output (xlsx dan/atau
  spreadsheet, sesuai `OUTPUT_TARGET`) -> cron tidak jalan sama sekali, atau
  gagal sebelum sempat menulis. Cek `cron_daily_run.log`.
- Ada file `data/ide_trade_YYYY-MM-DD.csv` -> salah satu (atau kedua) target
  output gagal saat cron jalan: `ide_trade.xlsx` terkunci (`PermissionError`
  di `daily_run.log`) dan/atau Google Sheets gagal (kredensial/kuota/jaringan
  — lihat traceback di `daily_run.log`, cek juga `GSHEET_ID`/
  `GSHEET_CREDENTIALS`/apakah spreadsheet sudah di-share ke `client_email`
  service account, lihat bagian 2). Data hari itu AMAN di CSV fallback tapi
  belum tergabung ke target yang gagal. Perbaiki penyebabnya, lalu
  `python3 daily_run.py --force` utk menggabungkannya.
- Tidak ada notifikasi TAPI baris hari ini ADA di output -> normal kalau
  `NOTIFY_CHANNEL=none` (memang sengaja dilewati). Kalau kanal lain yang
  dipilih, masalah ada di `send_notification()`/pengirim kanal itu (cek
  `.env`, cek `daily_run.log` utk baris `ERROR` atau `CRITICAL`).
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
| `.env` | Secret target output (`OUTPUT_TARGET`, `GSHEET_*`) & notifikasi (`NOTIFY_CHANNEL` + kanal aktif), TIDAK di-commit |
| `ide_trade.xlsx` | Output lokal (kalau `OUTPUT_TARGET=xlsx`/`both`) — SATU spreadsheet yang bertambah tiap hari (LONG+SHORT, baris terbaru di atas), TIDAK di-commit. Baris tanggal hari ini jg jadi guard idempotensi |
| `gsheet_credentials.json` | Output Google Sheets (kalau `OUTPUT_TARGET=gsheet`/`both`) — kunci service account, SAMA SENSITIFNYA dgn `.env`, TIDAK di-commit, `chmod 600` di VPS |
| `data/ide_trade_YYYY-MM-DD.csv` | Fallback DARURAT — muncul kalau `ide_trade.xlsx` terkunci ATAU Google Sheets gagal saat cron jalan. Kalau ada, gabungkan manual dgn `--force` setelah masalahnya diperbaiki |
| `short_scan.py` | Kandidat SHORT (belum pernah diuji), cek perp `/fapi/*` (host beda dari spot, lihat bagian 1) |
| `journal.jsonl` | Data trading pribadi, TIDAK di-commit, dibaca `daily_run.py` (read-only, hanya utk hitung posisi terbuka) |
