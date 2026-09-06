# Uji Nilai Prediktif Data Fundamental Protokol (DefiLlama) — Pra-registrasi

**Ditulis & di-commit SEBELUM mengambil data apa pun dan sebelum melihat hasil
apa pun.** Isi di bawah tidak diedit setelah melihat data. Kalau hasil tidak
cocok dugaan, dilaporkan apa adanya di `HASIL_DEFI.md`.

## Kenapa uji ini berbeda dari 5 sebelumnya

Lima rangkaian uji proyek ini (backtest komposit, faktor tunggal, mekanik
entry-acak, detektor regime, aliran order) **semuanya turunan harga/volume** —
data yang sama diolah ulang. Uji ini memakai **kategori data yang berbeda
secara mendasar: pemakaian protokol yang sebenarnya** (TVL, fee, revenue,
volume DEX, pasokan stablecoin per chain). Bukan jaminan ada sinyal — hanya
sumber informasi yang belum pernah diuji di proyek ini.

Metodologi tetap sama persis dengan `orderflow_test.py`: **uji cross-sectional
langsung pada return ke depan**, bukan simulasi trade (mekanik trade sudah
terbukti mengaburkan sinyal apa pun — `mechanics_test.py`).

Ini **kategori data keenam** yang diuji. `RINGKASAN_AKHIR.md` mencatat lima
yang pertama.

---

## Data

Sumber: **`api.llama.fi`** (dan `stablecoins.llama.fi`) — gratis, tanpa API key.
Harga token untuk return: `.cache_history/` (Binance daily, 2021–2026, sama
seperti `backtest.py`). Disimpan ke **`.cache_defi/`** — terpisah dari semua
cache lain, tidak menimpa apa pun.

Yang diunduh, riwayat harian sepanjang tersedia, untuk tiap protokol di universe:

| Data | Endpoint | Dipakai untuk |
|---|---|---|
| TVL USD historis | `/protocol/{slug}` → `tvl[]` | D1, D4 |
| TVL per-token (native + USD) | `/protocol/{slug}` → `tokens[]`, `tokensInUsd[]` | D1 (koreksi harga) |
| Fee & revenue harian | `/summary/fees/{slug}?dataType=dailyFees` / `dailyRevenue` | D2, D3 |
| Volume DEX harian | `/summary/dexs/{slug}` (hanya protokol DEX) | konteks D3 |
| TVL chain historis | `/v2/historicalChainTvl/{chain}` | D4 |
| Pasokan stablecoin per chain | `stablecoins.llama.fi/stablecoincharts/{chain}` | D5 |
| mcap sekarang | `/protocol/{slug}` → `mcap` | referensi (lihat D2) |

Titik data DefiLlama tidak selalu tepat harian (timestamp bisa jam berapa
saja). Semua deret di-**resample ke tanggal UTC**, ambil observasi terakhir per
hari, lalu forward-fill maksimum 3 hari (gap lebih panjang = NaN, bar tidak
ikut dihitung).

---

## Universe (ditetapkan SEBELUM mengambil data)

Protokol DefiLlama yang tokennya diperdagangkan sebagai `{SIMBOL}USDT` spot di
Binance **dan** punya data fundamental yang cukup. Target **100–150 protokol**.

**Konstruksi (sekali, lalu dibekukan ke `.cache_defi/universe_defi.json`):**

1. Tarik `/protocols`. Untuk tiap protokol ambil `symbol` (ticker token).
2. Tarik daftar base asset USDT spot Binance (`/api/v3/exchangeInfo`, lewat
   `screener.py` — sama seperti semua script lain, lihat CLAUDE.md).
3. Petakan: `symbol` protokol (uppercase) == base asset Binance.
   - Tabrakan nama (beberapa protokol klaim ticker sama, atau ticker menabrak
     koin non-DeFi) diselesaikan manual dengan daftar eksplisit di
     `fetch_defi.py` (`SYMBOL_OVERRIDES` / `SYMBOL_BLOCKLIST`), **ditulis
     sebelum run**, dengan alasan per baris.
4. **Kecualikan:**
   - Tidak ada `symbol`, atau `symbol` tidak ada di Binance USDT spot.
   - Stablecoin issuer (`category` mengandung "Stablecoin") — dinamika harga
     token ≠ kripto volatil.
   - Memecoin (`category` == "Memes") — **tidak punya data fundamental,
     jangan dipaksa isi** (jebakan #4).
   - `isParentProtocol` / protokol induk agregat (dobel hitung dengan anak).
   - Riwayat TVL **dan** riwayat fee dua-duanya < 180 hari → tidak cukup untuk
     fitur apa pun.
5. Simbol yang lolos + tabel pemetaan (berhasil / gagal + alasan) dicatat ke
   `universe_defi.json` dan dilaporkan di `HASIL_DEFI.md`. **Jumlah yang gagal
   dipetakan dilaporkan eksplisit, tidak diam-diam dilewati.**

Universe **dibekukan sekali**. Tidak ada penyaringan ulang per tanggal evaluasi
(itu membocorkan info masa depan ke seleksi — lihat `HIPOTESIS_ORDERFLOW.md`).

### Survivorship (batasan permanen, wajib disebut di laporan)

`/protocols` DefiLlama **hanya memuat protokol yang masih hidup**; protokol yang
mati / di-rug / ditinggalkan dihapus dari daftar. Daftar simbol Binance juga
hanya yang masih listing. Jadi universe ini = "protokol yang masih hidup hari
ini dan tokennya masih diperdagangkan, diuji mundur ke sejarahnya" — bukan
seluruh populasi. Persis analog dengan bias koin delisting di
`RINGKASAN_AKHIR.md`. **Tidak bisa diperbaiki** dalam uji ini — dicatat, bukan
disembunyikan.

---

## LANGKAH 1 — 5 kandidat fitur (parameter tetap, TIDAK ditambah/diubah setelah hasil)

Semua fitur dihitung **hanya dari data sampai bar t**, lalu **di-lag 2 hari
tambahan** (`feature[t]` sebenarnya memakai data sampai `t−2`) untuk
mengaproksimasi jeda pelaporan DefiLlama — lihat jebakan #1.

| ID | Definisi | Dugaan arah | Keyakinan |
|---|---|---|---|
| **D1** | `tvl_growth_real_30d` = pertumbuhan TVL **harga-netral (Laspeyres)** 30 hari: `Σ tokens_i[t]·p_i[t−30] / Σ tokens_i[t−30]·p_i[t−30] − 1`, memakai token yang ada di kedua tanggal; `p_i` dari `tokensInUsd/tokens`. **Dikondisikan** pada harga token protokol datar (`|return 30d token| < 10%`). | modal masuk (kuantitas, bukan harga) sebelum harga bergerak → return ke depan lebih baik | sedang |
| **D2** | `mcap_to_fees_annualized_pct` = persentil trailing-365d dari `close_price[t] / fee_tahunan[t]` vs sejarah protokol itu sendiri. `fee_tahunan` = jumlah `dailyFees` 365 hari ke belakang (butuh ≥180 hari; kalau 180–364 hari, pakai rata-rata harian × 365). Rendah = murah relatif pemakaian. **Catatan: mengasumsikan pasokan token ~konstan** dalam jendela trailing (faktor `mcap/price` konstan hilang di persentil) — emisi/burn besar membiaskan ini; dicatat sebagai batasan. | rasio rendah (persentil rendah) → return ke depan lebih baik (arah IC **negatif**) | sedang |
| **D3** | `fees_growth_30d` = `fee_30d[t] / fee_30d[t−30] − 1`, `fee_30d` = jumlah `dailyFees` 30 hari. | pemakaian (fee) naik → return ke depan lebih baik | sedang |
| **D4** | `tvl_share_of_chain_slope30` = slope regresi 30-hari dari `TVL_protokol_USD / TVL_chain_USD` (chain = jumlah atas chain tempat protokol ada). Rasio USD/USD → sebagian besar netral-harga. | merebut pangsa chain → return ke depan lebih baik | sedang |
| **D5** | `stablecoin_inflow_to_chain_14d` = `stablecoin_mcap_chain[t] / stablecoin_mcap_chain[t−14] − 1`, dipetakan ke protokol lewat chain utamanya (`totalCirculatingUSD` semua peg). | uang siap-beli masuk ke chain → return ke depan lebih baik | rendah |

**Tidak ada fitur ke-6.** Ide fitur baru selama analisis dicatat untuk putaran
uji terpisah (holdout baru), bukan ditambahkan ke sini.

---

## LANGKAH 2 — Uji cross-sectional

Untuk tiap tanggal evaluasi t × tiap horizon h ∈ {5, 10, 20} hari:

1. Ambil semua protokol di universe yang punya nilai fitur di t dan harga di
   t dan t+h. Minimal **15 protokol/hari** (`MIN_COINS_PER_DAY`), kalau kurang
   hari itu dilewati.
2. `fwd_ret_h = close[t+h]/close[t] − 1` per protokol (harga Binance).
3. **Demean cross-sectional**: kurangi rata-rata `fwd_ret_h` seluruh universe
   pada t (hapus arah pasar → sisa = performa relatif). Ini versi **primer**.
4. **IC** = Spearman(nilai fitur pada t, return relatif ter-demean).
5. **Versi within-symbol** (sekunder, dilaporkan berdampingan): fitur dan
   `fwd_ret_h` masing-masing di-demean per protokol sepanjang seluruh sejarahnya
   sebelum korelasi pooled dihitung — memisahkan "fitur menandai protokol bagus"
   (seleksi) dari "fitur menandai momen bagus di protokol yang sama" (timing).

Per (fitur × horizon):
- rata-rata IC + **t-statistik deret IC harian** (bukan per-observasi)
- % hari IC positif
- spread kuintil rata-rata (Q5 − Q1) per hari
- stabilitas sub-periode **2021–2023 vs 2024–2026**
- peluruhan IC antar horizon 5→10→20 (sinyal asli meluruh mulus)

---

## LANGKAH 3 — Kriteria lulus (ditetapkan sekarang, tidak diubah)

Total uji = **5 fitur × 3 horizon = 15 uji**. Ambang t-stat **3.5** (koreksi
multiple-testing, konsisten dengan `orderflow_test.py`).

Fitur-horizon dianggap punya sinyal **HANYA jika SEMUA** terpenuhi, pada set
**discovery (70% protokol)**:

1. `|rata-rata IC| ≥ 0.02`
2. `|t-stat IC| ≥ 3.5`
3. Arah IC **sama** di 2021–2023 dan 2024–2026
4. Spread Q5−Q1 **searah** dengan tanda IC
5. **Bertahan di holdout 30% protokol, seed `20260906`** (BARU — seed
   `20260904`/`20260905` sudah terpakai; tidak dipakai ulang). Dipakai
   **SEKALI**. "Bertahan" = tanda IC sama dengan discovery **dan**
   `|IC holdout| ≥ 0.02` (tidak menuntut `|t|≥3.5` lagi pada sampel yang
   sengaja dikecilkan 30%).

Kriteria 1–4 di discovery; kriteria 5 hanya diperiksa untuk yang lolos 1–4.

Kalau versi within-symbol dan cross-sectional **tidak sepakat arah**, kombinasi
itu **tidak lolos** — sinyal timing sejati harus muncul di kedua kerangka.

---

## Jebakan yang ditangani (pra-registrasi penanganannya)

**1. Lookahead — jeda pelaporan & revisi surut adapter.**
DefiLlama menyajikan TVL/fee dengan jeda pelaporan, dan adapter-nya **sering
diperbaiki surut** (sejarah dihitung ulang saat bug adapter ditemukan).
Endpoint historis `/protocol` mengembalikan data **sebagaimana-direvisi
(as-revised)**, bukan sebagaimana-dilaporkan-saat-itu (as-reported) — kami
**tidak punya snapshot point-in-time** untuk mengoreksinya. Mitigasi:
(a) semua fitur fundamental di-**lag 2 hari** (fitur pada t hanya memakai data
sampai t−2); (b) ini **batasan besar** yang ditulis menonjol di `HASIL_DEFI.md`
— **setiap hasil positif harus dibaca sebagai batas atas optimistik**, karena
revisi surut cenderung membuat data lama "tahu" apa yang terjadi berikutnya.
Kalau sebuah fitur lolos, itu belum tentu bisa ditradingkan secara real-time.

**2. Survivorship.** Lihat bagian Universe di atas. Protokol mati dihapus dari
`/protocols`. Disebut eksplisit di laporan.

**3. TVL sirkular (naik hanya karena harga token naik).** D1 memakai
pertumbuhan TVL **harga-netral (Laspeyres)** dari `tokens[]` (kuantitas native),
dinilai pada harga tetap t−30 — bukan `totalLiquidityUSD` mentah. Ditambah
kondisi harga token protokol datar. Tanpa koreksi ini D1 hampir pasti lolos
palsu. D4 memakai rasio USD/USD (pembilang & penyebut bergerak bersama pasar) →
sebagian besar netral-harga; sisa efek dicatat.

**4. Memecoin tidak punya data fundamental.** Dikecualikan di konstruksi
universe (`category == "Memes"`, atau riwayat TVL+fee dua-duanya < 180 hari).
Tidak dipaksa isi dengan nol/interpolasi.

---

## Aturan umum

- Fitur dihitung hanya dari data sampai t (lalu di-lag 2 hari). Tidak ada lookahead.
- Universe dibekukan sekali — tidak disaring ulang per tanggal.
- **Tidak menggabungkan fitur sampai ada yang lulus sendirian.**
- Ambang (IC, t-stat, 180 hari, lag 2 hari, seed) **tidak diubah** setelah
  melihat hasil.
- **Laporkan seluruh 15 hasil**, bukan hanya yang menarik.
- Kalau tidak ada yang lulus: **laporkan dan berhenti.** Itu hasil yang sah.
- `scoring.py` / `indicators.py` / `accumulation.py` **tidak diubah.**
- Survivorship + lookahead as-revised disebut eksplisit di `HASIL_DEFI.md`,
  bukan catatan kaki.
