# Deploy — VPS Ubuntu

Setup untuk menjalankan `daily_run.py` via cron sekali sehari + menyajikan
`dashboard.html` secara privat. Baca `KRITERIA_EVALUASI.md` dan
`RINGKASAN_AKHIR.md` dulu kalau belum — VPS ini menjalankan penyaring
perhatian, bukan sinyal beli otomatis.

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

Kalau berhasil, pesan masuk ke Telegram Anda dan `data/latest.json` +
`data/YYYY-MM-DD.json` terisi.

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

---

## 5. Menyajikan dashboard — BIND KE 127.0.0.1 SAJA

**JANGAN** expose ke internet publik — `data/latest.json` mencerminkan isi
`journal.jsonl` (trade pribadi, tesis, keyakinan). Cara paling sederhana:

```bash
cd /path/ke/screening-crypto
python3 -m http.server 8000 --bind 127.0.0.1
```

Biarkan berjalan di `tmux`/`screen`, atau buat service systemd (opsional,
lebih tahan restart VPS):

```ini
# /etc/systemd/system/screener-dashboard.service
[Unit]
Description=Dashboard screener (127.0.0.1 saja)
After=network.target

[Service]
WorkingDirectory=/path/ke/screening-crypto
ExecStart=/usr/bin/python3 -m http.server 8000 --bind 127.0.0.1
Restart=always
User=<user-non-root-anda>

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now screener-dashboard
```

### Akses dari laptop Anda — SSH tunnel

```bash
ssh -L 8000:127.0.0.1:8000 user@ip-vps-anda
```

Lalu buka `http://127.0.0.1:8000/dashboard.html` di browser Anda. Selama
tunnel SSH terbuka, dashboard bisa diakses; tutup terminal SSH untuk memutus
akses.

### Kalau TETAP ingin akses publik (tidak disarankan)

Data trading pribadi (`journal.jsonl` lewat `data/latest.json`) akan bisa
dilihat siapa saja yang tahu URL-nya kalau di-expose tanpa proteksi. Minimum
mutlak kalau Anda tetap memilih ini:
1. **HTTPS** (mis. lewat Caddy/nginx + Let's Encrypt certbot) — tanpa ini,
   traffic (termasuk kalau nanti ditambah auth) berjalan polos.
2. **HTTP Basic Auth** di depan `http.server` (nginx `auth_basic`, atau ganti
   `http.server` dengan nginx yang serve folder ini + `auth_basic`).
3. Idealnya juga: batasi IP sumber lewat firewall (`ufw allow from <IP-anda>`).

Ini tetap jauh lebih lemah daripada SSH tunnel + bind localhost. Default dan
rekomendasi proyek ini adalah **127.0.0.1 + SSH tunnel**, titik.

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
- `data/YYYY-MM-DD.json` untuk hari ini **tidak ada** -> cron tidak jalan
  sama sekali, atau gagal sebelum sempat menulis. Cek `cron_daily_run.log`.
- Tidak ada notifikasi Telegram TAPI `data/latest.json` ter-update -> masalah
  di `send_telegram()` (cek `.env`, cek `daily_run.log` utk baris `ERROR`
  atau `CRITICAL`).
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
| `data/latest.json` | Snapshot terbaru utk dashboard, TIDAK di-commit (regenerated tiap run) |
| `data/YYYY-MM-DD.json` | Arsip harian, TIDAK di-commit |
| `dashboard.html` | Statis, baca `data/latest.json` lewat `fetch()` — sajikan dari root proyek |
| `journal.jsonl` | Data trading pribadi, TIDAK di-commit, dibaca `daily_run.py` (read-only) |
