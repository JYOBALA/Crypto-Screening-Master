# Crypto Swing Screener

> ## ⚠ Baca dulu: skornya TIDAK prediktif
>
> Lima rangkaian uji walk-forward (skor komposit, faktor tunggal, mekanik
> entry-acak, detektor regime, aliran order) **semuanya null** di universe
> Binance USDT spot harian long-only 2021–2026. Skor total berkorelasi
> **nol** dengan hasil trade (Pearson −0,01, Spearman −0,13); pita skor
> tidak monoton; skor ≥70 kehilangan seluruh "edge"-nya begitu 3 winner
> teratas dibuang. Detail: **`RINGKASAN_AKHIR.md`**.
>
> Perlakukan alat ini sebagai **penyaring perhatian + pemaksa disiplin**
> (checklist SOP yang konsisten, rencana trade dengan level yang eksplisit,
> jurnal yang jujur), **bukan penghasil sinyal**. Skor 80 berarti "banyak
> kriteria checklist terpenuhi", bukan "80% peluang menang". Setiap kandidat
> tetap wajib diverifikasi di chart dengan penilaian sendiri.
>
> **Jangan menyetel bobot/ambang berdasarkan hasil trade** — datanya sudah
> cukup dan korelasinya nol; menyetel di atas itu = overfitting (lihat CLAUDE.md).

Screener otomatis yang menerapkan SOP screening swing trade: **Volume (25) · Stoch RSI (20) · Fibonacci (20) · Support–Resistance (20) · Chart Pattern (15) = 100 poin**, lengkap dengan filter regime BTC, veto rules, dan perhitungan entry/SL/TP/position size.

Output akhir: **daftar ticker siap eksekusi manual.**

Data diambil dari Binance public API — **tanpa API key, tanpa login, tanpa deposit.** Script ini read-only, tidak bisa dan tidak akan pernah melakukan order.

---

## Instalasi (sekali saja)

```bash
# 1. Butuh Python 3.10+
python3 --version

# 2. Install dependency
pip install pandas numpy requests pyarrow openpyxl

# 3. Uji tanpa internet — memastikan semua logika jalan
python3 screener.py --selftest
```

Kalau selftest keluar `SEMUA LULUS ✓`, siap dipakai.

---

## Cara pakai

```bash
# Screening swing harian (bias Daily) — pemakaian sehari-hari
python3 screener.py --mode daily

# Screening swing mingguan (bias Weekly) — dijalankan Minggu malam / Senin pagi
python3 screener.py --mode weekly

# Deteksi fase akumulasi / hidden gem (koin sideways yang sedang dikumpulkan)
# Metode: Wyckoff + VCP Minervini + BB Squeeze + Weinstein Stage 1 + RS vs BTC
# Dokumentasi lengkap: GEM_MODE.md
python3 screener.py --mode gem
python3 screener.py --mode gem --phase C --exclude-top 20

# Sesuaikan modal & risiko, ekspor hasilnya
python3 screener.py --mode daily --capital 5000 --risk-pct 1 --csv

# Longgarkan filter kalau hasilnya kosong terus
python3 screener.py --mode daily --min-score 65 --min-volume 10000000

# Lihat juga yang kena veto beserta alasannya (bagus untuk belajar)
python3 screener.py --mode daily --show-all

# Uji cepat 30 pair saja
python3 screener.py --mode daily --limit-symbols 30
```

### Semua opsi

| Flag | Default | Fungsi |
|---|---|---|
| `--mode` | `daily` | `daily` (swing harian), `weekly` (swing mingguan), `gem` (deteksi akumulasi) |
| `--capital` | `10000` | Modal dalam USD, untuk hitung position size |
| `--risk-pct` | `1.5` | Risiko per trade (%) |
| `--min-score` | `70` | Skor minimum agar masuk daftar "layak eksekusi" |
| `--min-rr` | `2.0` | R:R minimum ke TP1 |
| `--min-volume` | `20jt/50jt` | Override filter volume 24 jam (USD) |
| `--top` | `20` | Jumlah baris ditampilkan |
| `--limit-symbols` | semua | Batasi jumlah pair (uji cepat) |
| `--workers` | `8` | Thread paralel — turunkan kalau kena rate limit |
| `--csv` | off | Ekspor hasil ke CSV + JSON |
| `--show-all` | off | Tampilkan pair yang kena veto |
| `--no-cache` | off | Abaikan cache, tarik data segar |
| `--exclude-top` | `0` | [gem] Lewati N pair tervolume terbesar |
| `--phase` | semua | [gem] Filter fase Wyckoff, mis. `C` atau `CD` |
| `--max-run-30d` | `60` | [gem] Batas kenaikan 30 hari (%) sebelum dianggap sudah pump |
| `--selftest` | — | Uji logika tanpa internet |

Cache disimpan di `.cache/` per hari, jadi run kedua di hari yang sama jauh lebih cepat.

---

## Membaca outputnya

```
▶ LAYAK EKSEKUSI (skor ≥ 70, lolos veto) — 4 ticker

#   TICKER         SKOR GR   VOL  SRS  FIB  S/R  PAT         HARGA        ENTRY         STOP          TP1   R:R
1   SOLUSDT          84 A+     22   20   20   15    7      178.4200     178.4200     161.8500     212.6000   2.9
```

- **VOL / SRS / FIB / S/R / PAT** = skor per komponen. Berguna untuk melihat *dari mana* skornya datang. Skor 84 yang ditopang volume + Stoch RSI berbeda kualitasnya dari 84 yang ditopang pattern saja.
- **ENTRY** = harga sekarang kalau sudah di golden zone, atau tengah zona Fib 0.5–0.618 kalau harga masih di atasnya (artinya: pasang limit order, tunggu).
- **STOP** = titik invalidasi struktur (Higher Low terakhir / bawah zona support / Fib 0.786), bukan angka sembarangan.
- **R:R** = rasio ke TP1. Di bawah `--min-rr` otomatis kena veto.

Bagian **DETAIL KANDIDAT TERATAS** menjelaskan alasan skor tiap komponen dalam kalimat, plus link langsung ke chart TradingView.

Bagian **WATCHLIST** (skor 60–69) adalah kandidat yang kurang satu komponen. Pasang alert harga, cek lagi besok.

---

## Tool pendamping

```bash
# Bedah satu ticker secara detail: skor per komponen, level Fibonacci,
# zona S/R, dan rencana trade lengkap
python3 inspect_symbol.py NEARUSDT
python3 inspect_symbol.py TRXUSDT --mode weekly

# Cek sebaran volume universe — jalankan kalau jumlah pair terasa terlalu sedikit
python3 diagnose.py
```

---

## Kalau hasilnya kosong

Itu normal dan justru sering benar. Urutan pengecekan:

1. **Status BTC MERAH?** Semua pair otomatis kena veto. Ini fitur, bukan bug — sistemnya menolak entry long saat pasar turun.
2. **Turunkan `--min-score` ke 65** untuk melihat kandidat lapis kedua.
3. **Jalankan `--show-all`** untuk melihat alasan veto. Kalau mayoritas "R:R terlalu kecil", berarti pasar sedang sudah lari duluan — kamu telat, bukan salah screener.
4. Kalau berhari-hari kosong saat BTC hijau, kemungkinan filter volume terlalu ketat. Coba `--min-volume 10000000`.

---

## Otomatisasi

Pemakaian harian otomatis lewat **`daily_run.py`** (bukan proses menetap):
screening LONG + SHORT untuk 15 simbol beku di `universe_frozen.json`,
sisipkan baris hari ini di paling atas `ide_trade.xlsx`, kirim notifikasi
(Telegram/ntfy/Discord/email, bisa dimatikan). Guard idempoten — aman
dijalankan dua kali sehari.

```bash
python daily_run.py            # sekali tiap pagi
python daily_run.py --force    # paksa tulis ulang baris hari ini
```

**Setup cron di VPS + bot Telegram: lihat `DEPLOY.md`.** `.env` (token bot),
`journal.jsonl`, dan `ide_trade.xlsx` tidak pernah di-commit.

`screener.py --mode daily/weekly --csv` masih bisa dipakai manual kalau ingin
menjaring seluruh universe (bukan cuma 15 simbol beku).

---

## Menyesuaikan sistem dengan gaya kamu

**Bobot skor 25/20/20/20/15 dan ambangnya JANGAN diubah berdasarkan hasil
trade.** Backtest 422 pair menunjukkan skor berkorelasi nol dengan hasil —
menyetel bobot di atas data seperti itu adalah overfitting, bukan
pengembangan. Aturan ini dikunci di CLAUDE.md. Yang berkembang seharusnya
adalah **disiplin eksekusi kamu**, bukan angka bobot.

Yang masih wajar disetel adalah parameter geometri/universe di
**`screener.py` → `DEFAULT_CFG`**, karena mengubah *apa* yang dilihat, bukan
mengklaim bobot yang lebih prediktif:

| Parameter | Lokasi | Efek kalau dinaikkan |
|---|---|---|
| `pivot_order` | `DEFAULT_CFG` | Swing point lebih sedikit tapi lebih signifikan (coba 3–8) |
| `sr_tolerance_pct` | `DEFAULT_CFG` | Zona S/R lebih lebar, sentuhan lebih mudah terkumpul |
| `min_move_pct` | `indicators.last_impulse_swing()` | Hanya impuls besar yang dianggap valid untuk Fibonacci |

Kalibrasi keyakinan-vs-hasil dilakukan lewat `python journal.py review`
(butuh ≥50 trade sebelum ada kesimpulan), bukan dengan mengutak-atik bobot.

---

## Batasan yang perlu kamu tahu

- **Deteksi pattern itu aproksimasi.** Bull flag dan triangle dideteksi lewat aturan geometris sederhana. Mata manusia masih lebih baik. Perlakukan skor pattern sebagai petunjuk, dan **selalu buka chartnya sebelum entry.**
- **Tidak ada data market cap**, jadi filter rasio Volume/MCap dari SOP belum aktif. Cek manual di CoinGecko untuk kandidat final.
- **Tidak ada data unlock token.** Cek manual di CryptoRank/TokenUnlocks untuk kandidat yang akan dieksekusi.
- **Inti sistem hanya sinyal long.** SOP-nya dirancang untuk swing beli di pullback. `short_scan.py` menghasilkan kandidat SHORT (cermin bobot 25/20/20/20/15), **tetapi sisi short BELUM PERNAH DIUJI** — long sudah 5× dinyatakan null, short nol kali. Perlakukan output short sebagai eksperimen, bukan sinyal.
- **5 rangkaian uji, semua null.** Skor komposit, faktor tunggal, mekanik entry-acak, detektor regime, aliran order — tidak ada yang menunjukkan edge terukur di universe/periode ini. Skor 80 = "banyak kriteria checklist terpenuhi", bukan "80% peluang menang". Backtest total: −120 R dari 10.682 trade. Baca `RINGKASAN_AKHIR.md` (termasuk daftar "apa yang TIDAK boleh disimpulkan"). Tetap paper trade dulu.
- Kalau Binance diblokir di jaringanmu, set endpoint alternatif:
  `export BINANCE_BASE="https://data-api.binance.vision"`

---

## Struktur file

```
Inti screener
  screener.py        # CLI, pengambilan data, filter universe, output & ekspor
  scoring.py         # Sistem skor 100 poin, veto rules, regime BTC, rencana trade
  indicators.py      # RSI, Stoch RSI, OBV, ATR, pivot, Fibonacci, zona S/R, pattern
  accumulation.py    # Mesin deteksi akumulasi (mode gem): Wyckoff, VCP, squeeze, smart money, RS
  inspect_symbol.py  # Bedah detail satu ticker
  diagnose.py        # Diagnostik sebaran volume universe
  verify.py          # Gerbang mutu: sintaks + impor + encoding + selftest (jalankan setelah tiap edit)

Operasional harian
  daily_run.py           # Cron harian: screener 15 simbol beku (long+short) + notifikasi + ide_trade.xlsx
  short_scan.py          # Kandidat SHORT (BELUM PERNAH DIUJI)
  journal.py             # Jurnal trade sebagai instrumen riset (append-only + hash), journal.py review
  universe_frozen.json   # 15 simbol dibekukan 2026-09-05, dibaca daily_run.py — jangan diedit sampai trade ke-50

Riset / validasi (read-only, tidak mengubah scoring)
  backtest.py        # Walk-forward evaluate() di .cache_history/
  factor_test.py     # 9 faktor mentah per kuintil + holdout
  mechanics_test.py  # 6 varian mekanik dengan entry acak
  regime_test.py     # 5 detektor regime long/short — Tahap 1
  orderflow_test.py  # IC cross-sectional 5 fitur aliran order
  fetch_history.py   # Isi .cache_history/ (422 pair, 2021–2026)
  fetch_orderflow.py # Isi .cache_orderflow/ (kolom qav/trades/tbbav/tbqav)

Dokumentasi
  README.md              # File ini
  CLAUDE.md              # Konteks untuk Claude Code
  GEM_MODE.md            # Dokumentasi lengkap mode gem
  DEPLOY.md              # Setup cron daily_run.py + bot Telegram di VPS
  RINGKASAN_AKHIR.md     # Kesimpulan lengkap 5 rangkaian uji (skor tidak prediktif)
  KRITERIA_EVALUASI.md   # Pra-registrasi eksperimen trading manual (universe beku)
  HIPOTESIS_*.md / *_HASIL.md / HASIL_*.md  # Protokol + hasil per rangkaian uji
```

---

*Alat bantu analisa teknikal untuk keputusan pribadi, bukan rekomendasi investasi. Trading crypto berisiko tinggi. Jalankan paper trading minimal 20–30 trade sebelum pakai uang sungguhan.*
