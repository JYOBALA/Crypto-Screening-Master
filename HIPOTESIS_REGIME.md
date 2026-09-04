# Uji Regime Long/Short — Protokol & Hipotesis (pra-registrasi)

**Ditulis & di-commit SEBELUM melihat hasil apa pun.** Hipotesis di bawah tidak
diedit setelah melihat data. Kalau hasil tidak cocok, dilaporkan apa adanya.

Dua tahap. **Tahap 2 hanya dijalankan kalau Tahap 1 lulus.**

Konteks yang sudah diketahui (jangan dibangun di atasnya sebelum ada detektor
terbukti): detektor regime yang sekarang (`scoring.btc_regime`, berbasis EMA50)
**anti-prediktif untuk hasil trade** — trade saat HIJAU E[R] −0.095 R, saat KUNING
+0.026 R. Tapi itu hasil trade, bukan return ke depan universe. Tahap 1 menguji
hal yang berbeda: apakah ada label regime yang memprediksi **return 30 hari ke
depan** universe.

---

## TAHAP 1 — Bisakah regime dideteksi dengan nilai prediktif?

### Data & walk-forward

Semua detektor dihitung **hanya dari data sampai bar t** (`df.iloc[:t+1]`). Kalau
ada satu titik saja yang memakai data masa depan → seluruh hasil batal.

Detektor dievaluasi pada **kadens mingguan** (tiap 7 bar sejak bar warm-up ke-250)
untuk membatasi tumpang-tindih jendela 30-hari. Universe = `.cache_history/`
(422 pair USDT, 2021–2026).

### 5 kandidat detektor (semua parameter tetap oleh spesifikasi — TIDAK ada
ambang yang dipilih setelah melihat hasil)

| ID | Definisi | Label BULL bila |
|---|---|---|
| **R1** | BTC close vs SMA200 harian | close > SMA200 |
| **R2** | BTC close vs EMA50 harian (aturan sekarang, pembanding) | close > EMA50 |
| **R3** | Breadth: % koin universe (yang punya ≥200 bar) dengan close > SMA200 masing-masing | breadth > 50% |
| **R4** | Proksi dominasi BTC* — SMA50 dari rasio `BTC_close / altindex` (altindex = rata-rata geometrik close ter-normalisasi seluruh koin universe pada bar t) | SMA50 rasio **turun** (dominasi turun = alt season) |
| **R5** | Return BTC 90 bar ke belakang | return > 0 |

\* Dominasi BTC historis sebenarnya (mcap BTC / total mcap) tidak tersedia gratis
per-hari. R4 memakai proksi berbasis harga: kekuatan relatif BTC terhadap
keranjang alt. Ini didokumentasikan sebagai keterbatasan, bukan disembunyikan.

### Ukuran hasil

Untuk tiap detektor, di tiap titik evaluasi, untuk tiap koin yang punya data di
bar t dan t+30:
- `fwd_ret_30d = (close[t+30] / close[t] − 1) × 100` (poin persen)

Lalu:
- rata-rata `fwd_ret_30d` saat label = BULL
- rata-rata `fwd_ret_30d` saat label = BEAR
- **selisih BULL − BEAR**, plus **95% CI bootstrap blok** (resample per
  titik-evaluasi-mingguan; seluruh koin dari satu minggu diambil bersama supaya
  korelasi lintas-koka dalam minggu dihormati; 2000 resample)
- arah selisih di sub-periode **2021–2023** vs **2024–2026**

### Kriteria LULUS Tahap 1 (SEMUA harus terpenuhi)

1. Selisih BULL − BEAR **≥ 5 poin persen**
2. 95% CI selisih **tidak melewati nol**
3. Tanda selisih **sama** di 2021–2023 dan 2024–2026

Kalau **tidak ada** detektor yang lulus: **BERHENTI.** Laporkan. Tidak lanjut
Tahap 2.

### Hipotesis arah (pra-registrasi)

| Detektor | Dugaan | Keyakinan |
|---|---|---|
| R1 (SMA200) | BULL − BEAR positif — filter tren klasik | sedang |
| R2 (EMA50) | ~nol atau lemah positif — ini yang sekarang, sudah dicurigai buruk | rendah (pembanding) |
| R3 (breadth) | positif, kemungkinan **terkuat** — breadth ukuran risk-on/off nyata | sedang |
| R4 (dominasi proksi) | positif tapi berisik | rendah |
| R5 (BTC 90d) | positif — momentum | sedang |

Dugaan menyeluruh: karena median koin −83% dan hanya 9% naik, "BEAR" kemungkinan
punya return ke depan sangat negatif; pertanyaan sebenarnya apakah "BULL" cukup
kurang-negatif (atau positif) untuk selisih ≥5pp yang **stabil**.

---

## TAHAP 2 — hanya jika Tahap 1 lulus

Pakai detektor **terbaik** dari Tahap 1 (selisih terbesar yang memenuhi ketiga
kriteria). Bandingkan 4 strategi di universe & periode yang sama:

| ID | Strategi |
|---|---|
| S1 | Long-only (baseline yang sudah ada) |
| S2 | Long saat BULL, cash saat BEAR |
| S3 | Long saat BULL, short saat BEAR |
| S4 | Short-only |

### Batasan WAJIB sisi short (tanpa ini hasilnya fiksi)

a. **Hanya koin yang punya pasar perpetual futures di Binance PADA SAAT ITU.**
   Daftar via `/fapi/v1/exchangeInfo` + `onboardDate` tiap simbol. Koin tanpa perp
   (atau perp belum onboard di tanggal sinyal) → **tidak bisa di-short**, keluarkan
   dari universe short untuk periode itu.
b. **Biaya funding:** tarik `/fapi/v1/fundingRate` per simbol, terapkan per 8 jam
   posisi terbuka (short bayar/terima sesuai tanda funding). Kalau data funding
   tidak tersedia untuk suatu periode → **JANGAN asumsikan nol**, keluarkan
   periode itu & sebutkan di laporan.
c. **Fee futures** (bukan spot) + slippage, dua sisi.
d. **Kerugian tak terbatas:** stop wajib di sisi short. Laporkan berapa kali stop
   kena **gap melewati level** (slippage stop di luar level).

### Laporan per strategi

n trade, win rate, ekspektasi R, profit factor, max drawdown, ekspektasi tanpa 5
trade terbaik, pecahan per tahun 2021–2026.

### Pemeriksaan wajib

- Kalau S3/S4 menghasilkan angka luar biasa (mis. >+0.5 R/trade): **JANGAN
  laporkan sebagai temuan.** Periksa dulu: berapa % trade short terjadi di koin
  yang sebenarnya tidak punya perp saat itu? Angka besar di sisi short hampir
  selalu artefak.
- Benchmark paling sederhana: **hold BTC saja** sepanjang periode. Kalau strategi
  rumit tidak mengalahkannya, katakan.

---

## Aturan umum

- Regime HARUS walk-forward. Satu titik lookahead = semua batal.
- Ambang detektor TIDAK dipilih setelah melihat return (semua sudah fixed di atas).
- Tidak menggabungkan detektor. Lima kandidat, satu-satu.
- `scoring.py` / `indicators.py` / `accumulation.py` tidak diubah.
- Kalau Tahap 1 gagal total: kesimpulan "regime tidak bisa dideteksi dengan nilai
  prediktif di universe/periode ini" adalah hasil yang sah — dilaporkan tanpa
  dihaluskan, dan `RINGKASAN_AKHIR.md` diperbarui.
