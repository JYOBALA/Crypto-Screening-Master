# Uji Nilai Prediktif Aliran Order — Protokol & Hipotesis (pra-registrasi)

**Ditulis & di-commit SEBELUM melihat hasil apa pun** (dan sebelum unduhan data
selesai — lihat catatan blocker jaringan di commit `378785a`). Isi di bawah
tidak diedit setelah melihat data. Kalau hasil tidak cocok dugaan, dilaporkan
apa adanya.

Proyek baru, bukan lanjutan screener/backtest/regime sebelumnya. Metodologi
berbeda secara mendasar: **uji cross-sectional langsung pada return ke depan**,
bukan simulasi trade — mekanik trade (`mechanics_test.py`) sudah terbukti
mengaburkan sinyal apa pun yang ada di seleksi.

---

## Data

`fetch_orderflow.py` mengunduh 4 kolom yang selama ini dibuang oleh
`fetch_klines()`/`fetch_history.py`: quote asset volume (`qav`), number of
trades (`trades`), taker buy base asset volume (`tbbav`), taker buy quote
asset volume (`tbqav`). Disimpan ke `.cache_orderflow/`, terpisah dari
`.cache_history/` (dipakai `backtest.py`) — tidak saling menimpa.

## Universe (ditetapkan SEBELUM melihat hasil apa pun)

Uji dijalankan pada **dua universe**, dilaporkan berdampingan. Keduanya
dihitung sebagai uji terpisah dalam koreksi multiple testing di bawah.

| ID | Definisi | Perkiraan jumlah pair | Alasan |
|---|---|---|---|
| **U1 — Likuid** | volume 24h **≥ $5 juta** pada saat universe dibangun | ~78 | cukup likuid untuk ditradingkan tanpa slippage besar |
| **U2 — Luas** | volume 24h **≥ $1 juta** pada saat universe dibangun | ~201 | sampel lebih banyak, tapi sebagian tidak realistis ditradingkan |

Ambang dolar ($5jt / $1jt) ditetapkan sekarang, sebelum data ditarik — tidak
disesuaikan setelah melihat berapa pair yang lolos atau bagaimana hasilnya.

**Universe dibekukan sekali di awal, lalu TETAP sepanjang periode uji
(2021–2026).** Filter volume 24h dipakai **hanya sekali**, pada saat konstruksi
universe (memakai `/api/v3/ticker/24hr` saat data ditarik), untuk memutuskan
simbol mana yang masuk U1/U2. Tanggal/waktu snapshot volume yang dipakai akan
dicatat di `HASIL_ORDERFLOW.md`. **Tidak ada penyaringan ulang per tanggal
evaluasi memakai volume saat itu** — itu akan memasukkan informasi masa depan
ke seleksi universe (koin yang sekarang likuid belum tentu likuid di 2021, dan
sebaliknya; menyaring ulang per-tanggal secara implisit memilih "pemenang").
Konsekuensinya: kedua universe tetap mengandung koin yang sudah ilikuid atau
belum lahir di sebagian periode 2021–2026 — bar tanpa data pada simbol
tersebut otomatis tidak ikut dihitung (bukan diberi nilai 0).

### Catatan survivorship (batasan permanen, wajib disebut di laporan akhir)

U1 dan U2 dibangun dari volume 24h **hari ini** — mengandung bias bertahan
hidup: koin yang delisting sebelum hari ini (mati, di-rug, atau dihapus
Binance) tidak mungkin masuk universe manapun, walau dulu likuid. API publik
Binance tidak menyediakan cara menarik daftar simbol yang sudah delisting,
jadi ini **bukan sesuatu yang bisa diperbaiki** dalam uji ini — dicatat sebagai
batasan, bukan disembunyikan. Artinya hasil apa pun dari uji ini mencerminkan
"koin yang masih hidup dan likuid hari ini, diuji mundur ke sejarahnya" — bukan
seluruh populasi koin yang pernah ada.

---

## LANGKAH 1 — 5 kandidat fitur (parameter tetap, tidak diubah setelah hasil)

| ID | Definisi | Dugaan arah | Keyakinan |
|---|---|---|---|
| **F1** | `taker_buy_ratio = taker_buy_base / volume` | tinggi → return ke depan lebih baik | sedang |
| **F2** | `avg_trade_size = quote_volume / trades`, dipersentilkan vs 90 hari sejarah koin itu sendiri | naik (partisipan lebih besar) → return ke depan lebih baik | sedang |
| **F3** | `trade_intensity = trades / MA20(trades)` | lonjakan partisipasi → return ke depan lebih baik | sedang |
| **F4** | `flow_divergence` = slope 10-hari `taker_buy_ratio`, **dikondisikan** pada harga datar (\|return 10 hari\| < 5%) | akumulasi diam-diam (versi terukur dari yang dicoba ditangkap OBV) → return ke depan lebih baik | sedang |
| **F5** | `taker_buy_ratio_extreme` = persentil `taker_buy_ratio` vs 90 hari sejarah koin itu sendiri | **diuji dua arah, sengaja** — ekstrem tinggi bisa berarti kekuatan pembeli ATAU kelelahan pembeli. Dugaan netral | rendah (eksploratif dua-arah) |

Kelima fitur dihitung **hanya dari data sampai bar t** (tidak ada lookahead).
F2 dan F5 memakai jendela persentil 90 hari **ke belakang** dari t (tidak
termasuk t+1 dst).

**Tidak ada fitur ke-6 yang akan ditambahkan setelah melihat hasil.** Kalau
ada ide fitur baru selama analisis, dicatat sebagai ide untuk putaran uji
terpisah di masa depan (holdout baru, bukan menambah ke uji ini).

---

## LANGKAH 2 — Uji cross-sectional (bukan simulasi trade)

Untuk tiap universe (U1, U2) × tiap tanggal evaluasi t × tiap horizon
h ∈ {5, 10, 20} hari:

1. Ambil semua koin di universe itu yang punya data di t dan di t+h.
2. Hitung return ke depan `fwd_ret_h = close[t+h]/close[t] − 1` tiap koin.
3. **Demean** `fwd_ret_h` terhadap rata-rata seluruh koin universe pada t —
   menghapus arah pasar; sisa adalah performa relatif.
4. Hitung **Spearman** antara nilai fitur pada t dan return relatif
   ter-demean itu → **Information Coefficient (IC)** harian.

Lalu, per (fitur × horizon × universe):
- rata-rata IC dan **t-statistik deret IC** (t-stat time-series harian, bukan
  per-observasi/per-trade)
- % hari dengan IC positif (jauh dari 50% kalau ada sinyal)
- spread kuintil: rata-rata (Q5 − Q1) return relatif per hari
- stabilitas per sub-periode **2021–2023 vs 2024–2026**
- peluruhan IC antar horizon 5→10→20 (sinyal asli meluruh mulus; sinyal palsu
  melompat tak berpola)

---

## LANGKAH 3 — Kriteria lulus (ditetapkan sekarang, tidak diubah)

Total uji = **5 fitur × 3 horizon × 2 universe = 30 uji independen** (naik
dari rencana awal 15 karena penambahan U1/U2). Karena itu ambang t-stat
dinaikkan dari 3.0 → **3.5** — koreksi multiple-testing yang belum pernah
dilakukan di proyek ini sebelumnya.

Fitur-horizon-universe dianggap punya sinyal **HANYA jika SEMUA** terpenuhi:

1. `|rata-rata IC| ≥ 0.02` (ambang wajar riset faktor, bukan 0.15)
2. `|t-stat IC| ≥ 3.5`
3. Arah IC **sama** di 2021–2023 dan 2024–2026
4. Spread Q5−Q1 **searah** dengan tanda IC
5. **Bertahan di holdout 30% simbol, seed BARU** (dicatat di commit hasil —
   berbeda dari seed `20260904` yang sudah dipakai untuk `dist_to_res_pct` di
   `factor_test.py`, tidak dipakai ulang)

Kalau kombinasi lolos di U1 tapi tidak di U2 (atau sebaliknya): dilaporkan
sebagai temuan **lemah/tidak konsisten-universe**, bukan lolos penuh — sinyal
yang nyata seharusnya tidak sensitif terhadap ambang likuiditas sebesar itu.

---

## Aturan umum

- Fitur dihitung **hanya** dari data sampai t. Tidak ada lookahead.
- Universe dibekukan sekali di awal (lihat di atas) — tidak disaring ulang
  per tanggal.
- Tidak menggabungkan fitur sampai ada yang lulus sendirian.
- Ambang (IC, t-stat, ambang dolar universe) **tidak diubah** setelah melihat
  hasil.
- Laporkan **seluruh 30 hasil**, bukan hanya yang menarik.
- Kalau tidak ada yang lulus: laporkan dan berhenti. Itu hasil yang sah.
- `scoring.py` / `indicators.py` / `accumulation.py` tidak diubah.
- Bias survivorship universe (lihat di atas) disebutkan secara eksplisit di
  `HASIL_ORDERFLOW.md`, bukan catatan kaki yang mudah terlewat.
