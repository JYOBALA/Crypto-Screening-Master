# Crypto Swing Screener

Screener teknikal untuk swing trade crypto (Binance USDT spot, timeframe harian)
**yang diuji sendiri sampai terbukti tidak prediktif.** Repo ini berisi dua hal:
(1) screener SOP berbasis skor 100 poin — Volume, Stoch RSI, Fibonacci,
Support–Resistance, Chart Pattern — yang menghasilkan daftar ticker + rencana
trade lengkap untuk eksekusi manual; dan (2) kerangka validasi kuantitatif yang
dipakai untuk mengukur apakah skor itu punya nilai prediktif. Setelah lima
rangkaian uji walk-forward (backtest komposit, faktor tunggal, mekanik
entry-acak, detektor regime, aliran order — **semuanya null**), kesimpulannya:
skor **tidak** memberi edge terukur di universe/periode ini. Alat tetap berguna
sebagai **penyaring perhatian + pemaksa disiplin** (checklist konsisten, level
entry/SL/TP eksplisit, jurnal append-only), **bukan penghasil sinyal.** Bagian
menarik dari repo ini adalah metodologinya, bukan screenernya.

Data dari Binance public API — tanpa API key, tanpa login, tanpa deposit.
Read-only, tidak pernah melakukan order.

---

## Ringkasan temuan

Semua uji: walk-forward, tanpa lookahead, fee 0,1% + slippage 0,05% per sisi,
SL menang jika satu bar menyentuh SL & TP sekaligus. `scoring.py` /
`indicators.py` / `accumulation.py` **tidak diubah** oleh uji apa pun — murni
pengukuran.

| Uji | Data | Metodologi | Hasil |
|---|---|---|---|
| **Backtest komposit** (`backtest.py`) | 422 pair USDT · 10.682 trade · 2021–2026 | Walk-forward tanpa lookahead; skor 100 poin + veto + rencana trade dijalankan tiap bar | **−120 R total.** Korelasi skor total ↔ hasil: Pearson −0,01 / Spearman −0,13. Kelima komponen \|r\| < 0,02. Pita skor **tidak monoton.** Skor ≥70 kehilangan seluruh "edge" begitu 3 winner teratas dibuang (−0,007 R). |
| **Faktor tunggal** (`factor_test.py`) | 13.020 sinyal · 364 koin | IC Spearman per faktor + koreksi within-symbol (demeaned per koin) + ANOVA identitas koin + holdout 30% simbol (sekali jalan) | **0 dari 9 faktor bertahan.** Satu (`dist_to_res_pct`) lolos discovery lalu **gugur di holdout** (arah kebalikan, tidak monoton). |
| **Mekanik entry-acak** (`mechanics_test.py`) | 8.000 entry acak · 6 varian | Seleksi dinetralkan total (entry acak); set entry identik untuk semua varian; uji A baseline / B tanpa BE / C SL 3×ATR / D trailing / E timeout 90 / F full-exit TP1 | **Keenam varian E[R] negatif** (−0,055 s/d −0,095 R). 95% CI **seluruhnya di bawah nol.** E[R] tanpa 5 trade terbaik ≈ E[R] penuh → negatif **struktural**, bukan efek ekor. |
| **Detektor regime** (`regime_test.py`) | 5 detektor · ~130 minggu evaluasi | Walk-forward; 95% CI = bootstrap blok per minggu (2000 resample) menghormati korelasi antar-koin | **Tidak ada detektor yang lolos.** Selisih BULL−BEAR terbesar +2,8 pp (CI [−2,5, +8,1]). 3 dari 5 detektor **anti-prediktif.** Tahap 2 (long/short) tidak dijalankan — prasyarat gagal. |
| **Aliran order** (`orderflow_test.py`) | 30 uji IC (5 fitur × 3 horizon × 2 universe) | Cross-sectional (IC Spearman harian vs return demeaned); ambang t-stat dinaikkan 3,0→3,5 untuk 30 uji; holdout seed baru | **0 dari 30 lulus semua kriteria.** 4 lolos discovery; kandidat terkuat (`taker_buy_ratio`, t hingga +6,21) **berbalik arah negatif** di 47 simbol holdout. |

Kesimpulan lengkap + daftar "apa yang **tidak** boleh disimpulkan dari data ini":
**[`RINGKASAN_AKHIR.md`](RINGKASAN_AKHIR.md)**.

---

## Metodologi

Yang membedakan repo ini dari screener teknikal lain adalah cara temuannya
diperoleh — dirancang untuk **menyulitkan diri sendiri menemukan sinyal palsu:**

- **Pra-registrasi.** Hipotesis, kriteria lulus, ambang, dan seed holdout ditulis
  dan **di-commit sebelum satu baris hasil pun dilihat**
  ([`HIPOTESIS_FAKTOR.md`](HIPOTESIS_FAKTOR.md),
  [`HIPOTESIS_REGIME.md`](HIPOTESIS_REGIME.md),
  [`HIPOTESIS_ORDERFLOW.md`](HIPOTESIS_ORDERFLOW.md)). Tidak ada ambang yang
  digeser setelah melihat hasil.
- **Holdout sekali pakai.** 30% simbol disisihkan dengan seed yang dicatat
  (`20260904` untuk faktor, `20260905` untuk order flow — sengaja berbeda).
  Begitu sebuah faktor diuji di holdout, ia **tidak pernah diuji ulang** di split
  yang sama; menindaklanjuti butuh data baru.
- **Koreksi multiple testing.** Ambang t-stat dinaikkan seiring jumlah uji
  (order flow: 3,0 → 3,5 karena 30 uji, bukan 15).
- **Koreksi within-symbol.** Setiap faktor diukur setelah *demeaned per koin*,
  memisahkan "faktor ini menandai koin yang bagus" (seleksi) dari "faktor ini
  menandai momen yang bagus di dalam koin yang sama" (timing). Hanya yang kedua
  yang dihitung sebagai sinyal.
- **Netralisasi seleksi.** Uji mekanik memakai entry **acak** supaya kualitas
  manajemen posisi diukur terpisah dari kualitas pemilihan setup.
- **Bootstrap sadar-korelasi.** CI regime dihitung dengan bootstrap blok
  mingguan — mengakui bahwa 30.000 observasi koin yang bergerak bersama hanya
  bernilai ~130 minggu sampel efektif.
- **Batasan ditulis eksplisit.** Setiap dokumen hasil menutup dengan daftar
  kesimpulan yang **tidak** ditopang datanya.

---

## Contoh temuan yang dibatalkan sendiri

Nilai kerangka ini paling terlihat saat ia membunuh temuannya sendiri:

- **`taker_buy_ratio` (aliran order).** IC discovery positif dan makin kuat
  antar horizon — t-stat **+6,21** di h=20 (U1), arah stabil di kedua
  sub-periode. Lolos kriteria 1–4. Di holdout 47 simbol yang belum pernah
  dilihat, **arah IC berbalik total menjadi negatif.** Sinyal palsu klasik dari
  overfitting universe kecil — hanya ketahuan karena protokol mewajibkan holdout
  dengan seed baru.
- **`fib_retr` & `atr_pct` (faktor tunggal).** Spearman mentah kuat (**±0,16**),
  terlihat seperti sinyal timing yang jelas. Setelah demeaned per koin, keduanya
  runtuh ke **~0** (−0,022 dan +0,019). Keduanya cuma **proksi kualitas koin** —
  koin bervolatilitas rendah yang retracement-nya dangkal kebetulan lebih sering
  bertahan. Tanpa koreksi within-symbol, keduanya lolos sebagai "faktor timing"
  palsu.
- **`dist_to_res_pct` (faktor tunggal).** Satu-satunya faktor yang lolos keempat
  kriteria discovery (Spearman demeaned +0,101, monoton, stabil). Di holdout:
  tidak monoton, Q5−Q1 **berbalik arah**, tidak stabil antar-periode. **Gugur.**
  Sesuai protokol, tidak diuji ulang.
- **`max_plausible_rr` (veto rule).** Backtest v1 (357 trade, 26 pair)
  menunjukkan rencana R:R rendah jauh unggul, jadi ambang veto diturunkan
  15 → 8. Backtest v2 (**422 pair, ~16× lebih besar**) membalikkannya —
  kedua pita R:R praktis nol. Ambang **dikembalikan ke 15** dan ditetapkan
  sebagai sanity-check geometri, bukan filter kinerja. Aturan yang lahir dari
  ini: jangan menyetel parameter atas perbedaan < ~0,1 R atau sampel < 100
  per kelompok.

---

## Instalasi

```bash
python3 --version                                     # butuh 3.10+
pip install pandas numpy requests pyarrow openpyxl
python3 screener.py --selftest                        # uji semua logika tanpa internet
```

Selftest keluar `SEMUA LULUS ✓` → siap dipakai. Mencakup skema Wyckoff sintetis,
validitas rencana trade (SL < entry < TP1 < TP2), rentang & konsistensi skor.

---

## Cara pakai

```bash
# Screening swing harian — pemakaian sehari-hari
python3 screener.py --mode daily --csv

# Swing mingguan (Senin) / deteksi akumulasi (mode gem, lihat GEM_MODE.md)
python3 screener.py --mode weekly --csv
python3 screener.py --mode gem --phase C --exclude-top 20

# Bedah satu ticker: skor per komponen, level Fib, zona S/R, rencana trade
python3 inspect_symbol.py NEARUSDT

# Sebaran volume universe
python3 diagnose.py
```

Opsi utama: `--capital` (default 10000), `--risk-pct` (1.5), `--min-score` (70),
`--min-rr` (2.0), `--min-volume`, `--show-all` (tampilkan yang kena veto beserta
alasan), `--limit-symbols` (uji cepat), `--no-cache`. Cache harian di `.cache/`.

**Veto rules** (batal otomatis berapa pun skornya): BTC status MERAH · ada
komponen bernilai 0 · retracement > 0,786 · Stoch RSI daily > 80 · R:R ke TP1
di bawah `--min-rr` atau di atas `max_plausible_rr` (15,0).

### Hasil kosong itu normal

BTC MERAH → semua pair kena veto (memang begitu desainnya). `--show-all` untuk
melihat alasan veto; kalau mayoritas "R:R terlalu kecil", pasar sudah lari
duluan. Melonggarkan ambang hanya untuk "supaya ada yang keluar" adalah cara
salah memakai alat ini.

### Otomatisasi harian

**`daily_run.py`** — cron harian (bukan proses menetap): screening LONG + SHORT
untuk 15 simbol beku di `universe_frozen.json`, sisipkan baris hari ini di paling
atas `ide_trade.xlsx`, notifikasi opsional (Telegram/ntfy/Discord/email). Guard
idempoten. Setup cron di VPS + bot Telegram: **[`DEPLOY.md`](DEPLOY.md)**.
`.env`, `journal.jsonl`, `ide_trade.xlsx` tidak pernah di-commit.

### Kenapa bobot skor tidak boleh disetel dari hasil trade

Backtest 422 pair menunjukkan skor berkorelasi **nol** dengan hasil. Menyetel
bobot 25/20/20/20/15 di atas data seperti itu adalah overfitting, bukan
pengembangan — aturan ini dikunci di `CLAUDE.md`. Kalibrasi keyakinan-vs-hasil
dilakukan lewat `python journal.py review` (butuh ≥50 trade), bukan mengutak-atik
bobot.

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
  daily_run.py           # Cron harian: 15 simbol beku (long+short) + notifikasi + ide_trade.xlsx
  short_scan.py          # Kandidat SHORT — cermin bobot long, BELUM PERNAH DIUJI
  journal.py             # Jurnal trade sebagai instrumen riset (append-only + hash SHA256); journal.py review
  universe_frozen.json   # 15 simbol dibekukan 2026-09-05 — jangan diedit sampai trade ke-50

Kerangka validasi (read-only, tidak mengubah scoring)
  backtest.py        # Walk-forward evaluate() tiap bar di .cache_history/
  factor_test.py     # 9 faktor mentah per kuintil + demeaned + holdout
  mechanics_test.py  # 6 varian mekanik trade dengan entry acak
  regime_test.py     # 5 detektor regime long/short — Tahap 1 + bootstrap
  orderflow_test.py  # 30 uji IC cross-sectional fitur aliran order
  fetch_history.py   # Isi .cache_history/ (422 pair, 2021–2026)
  fetch_orderflow.py # Isi .cache_orderflow/ (kolom qav/trades/tbbav/tbqav)

Dokumentasi
  RINGKASAN_AKHIR.md     # Kesimpulan lengkap 5 rangkaian uji + batasan eksplisit
  HIPOTESIS_*.md         # Protokol pra-registrasi (di-commit sebelum hasil)
  FACTOR_TEST_HASIL.md / HASIL_REGIME.md / HASIL_ORDERFLOW.md   # Hasil lengkap per uji
  KRITERIA_EVALUASI.md   # Pra-registrasi eksperimen trading manual (universe beku)
  GEM_MODE.md            # Dokumentasi lengkap mode gem
  DEPLOY.md              # Setup cron daily_run.py + bot Telegram di VPS
  CLAUDE.md              # Konteks + aturan kerja untuk Claude Code
```

---

## Batasan

- **Skor tidak prediktif.** Sudah dinyatakan di atas — diulang di sini karena
  ini batasan terpenting. Skor 80 berarti "banyak kriteria checklist terpenuhi",
  bukan "80% peluang menang".
- **Long-only.** SOP dirancang untuk swing beli di pullback. `short_scan.py`
  menghasilkan kandidat SHORT (cermin bobot 25/20/20/20/15), **tetapi sisi short
  BELUM PERNAH DIUJI** — sisi long sudah 5× dinyatakan null, short nol kali.
  Perlakukan output short sebagai eksperimen, bukan sinyal.
- **Survivorship tidak bisa dikoreksi penuh.** `.cache_history/` hanya berisi
  pair yang **masih listing** di Binance hari ini. Koin yang sudah delisting
  (kemungkinan besar yang terburuk) tidak bisa ditarik dari API — semua hasil
  backtest di sini kemungkinan **lebih optimis** dari kenyataan penuh.
- **Lahan sangat buruk.** Median koin di universe ini −83% sejak 2021, hanya 9%
  yang naik. Uji apa pun di sini menghadapi tekanan luar biasa dan hasilnya
  belum tentu berlaku di periode/timeframe/exchange lain.
- **Deteksi pattern itu aproksimasi.** Bull flag & triangle dideteksi lewat
  aturan geometris sederhana. Selalu buka chart sebelum entry.
- **Tidak ada data market cap / unlock token.** Cek manual di
  CoinGecko / TokenUnlocks untuk kandidat final.
- **Regime BTC di data ini anti-prediktif.** Trade saat BTC HIJAU E[R] −0,10,
  saat KUNING +0,03 — kebalikan dari desain. Veto MERAH tidak jelas menolong
  maupun merugikan.
- Binance diblokir di sebagian ISP Indonesia — default sudah
  `data-api.binance.vision`; override: `export BINANCE_BASE=...`.

---

*Alat bantu analisa teknikal untuk keputusan pribadi, bukan rekomendasi
investasi. Trading crypto berisiko tinggi. Jalankan paper trading minimal 20–30
trade sebelum pakai uang sungguhan.*
