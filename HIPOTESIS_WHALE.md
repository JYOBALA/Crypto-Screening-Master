# Uji Kelayakan Pelacakan Wallet Whale Memecoin Solana — Pra-registrasi (FASE 0)

**Ditulis & di-commit SEBELUM mengambil data apa pun dan sebelum melihat hasil
apa pun.** Semua ambang, definisi, tanggal, dan kriteria lulus di bawah
ditetapkan sekarang. Tidak diedit setelah melihat data. Kalau hasil tidak cocok
dugaan, dilaporkan apa adanya di `HASIL_WHALE.md`.

Tujuan: **RISET (portofolio riset kuantitatif)** + kemungkinan **swing trade
manual**. FASE 0 hanya menjawab: apakah ada sesuatu yang layak dibangun.
**Tidak membangun screener / dashboard / notifikasi di fase ini.**

Ini **kategori data ketujuh** yang diuji proyek ini. Enam sebelumnya (backtest
komposit, faktor tunggal, mekanik entry-acak, detektor regime, aliran order,
fundamental DefiLlama) **semuanya null** di universe/periode masing-masing —
lihat `RINGKASAN_AKHIR.md`, `HASIL_ORDERFLOW.md`, `HASIL_DEFI.md`. Uji ini
memakai populasi yang sama sekali berbeda (wallet on-chain Solana, bukan koin
Binance top-volume) dan pertanyaan yang berbeda (apakah identitas pembeli
membawa informasi). Bukan jaminan ada sinyal.

---

## KONTEKS EKSEKUSI — menentukan seluruh desain

User **tidak punya infrastruktur kecepatan**. Tidak menyalin transaksi whale,
tidak balapan di blok yang sama, tidak pakai bot co-trade. Yang diuji: apakah
**pola akumulasi banyak wallet selama beberapa hari** masih menyisakan gerakan
harga **SETELAH pola itu terlihat lengkap oleh pengamat biasa yang menonton
data harian**.

### ATURAN HARGA MASUK (paling penting — pelanggaran = uji sia-sia)

> Harga masuk = **penutupan harian (candle UTC) PERTAMA setelah timestamp sinyal
> lengkap**. DILARANG memakai harga eksekusi whale, harga rata-rata whale, harga
> intrabar, atau harga apa pun sebelum sinyal terlihat lengkap.

Contoh: entitas berkualitas ke-5 membeli token X pada 2026-04-10 14:00 UTC
(itu melengkapi cluster). Candle harian 2026-04-10 close pada 2026-04-11 00:00
UTC. **Harga masuk = close candle 2026-04-10.** Semua horizon return dihitung
dari close itu.

Kalau aturan ini dilanggar di titik mana pun, seluruh hasil Pertanyaan B tidak
bisa ditradingkan dan uji ini batal.

---

## DUA PERTANYAAN, BERURUTAN

Pertanyaan A dijawab lebih dulu. **Kalau A gagal → BERHENTI, tulis
`HASIL_WHALE.md`, jangan sentuh Pertanyaan B, jangan coba definisi whale lain.**
Mencoba definisi lain sampai ketemu yang lolos = data dredging (dilarang oleh
aturan proyek dan oleh brief ini).

### PERTANYAAN A — apakah "smart money" bertahan?

Hipotesis: wallet yang berperforma tinggi SEBELUM tanggal pemisah juga
berperforma di atas acak SESUDAHnya (skill persisten, bukan keberuntungan).

Prosedur, diulang di **3 tanggal pemisah**:

| # | Tanggal pemisah | Jendela pra (peringkat) | Jendela pasca (ukur) |
|---|---|---|---|
| S1 | **2026-03-01** | 2025-01-01 → 2026-03-01 | 2026-03-01 → 2026-09-01 (~6 bln) |
| S2 | **2026-05-01** | 2025-01-01 → 2026-05-01 | 2026-05-01 → 2026-09-01 (~4 bln) |
| S3 | **2026-07-01** | 2025-01-01 → 2026-07-01 | 2026-07-01 → 2026-09-01 (~2 bln, TIPIS — dicatat) |

1. Hitung PnL realized (USD, metode FIFO — lihat "Perhitungan PnL") tiap wallet
   dari **hanya trade di jendela pra**.
2. **Wallet berkualitas** = PnL realized ≥ **$50.000** DAN win rate ≥ **55%**
   DAN ≥ **20 trade realized** di jendela pra. (Ambang ini juga dipakai di B.)
3. Ambil **500 teratas** berdasarkan PnL realized pra-tanggal, di antara yang
   memenuhi syarat berkualitas. Kalau yang memenuhi syarat < 500, ambil semua
   dan catat jumlahnya.
4. **Sybil grouping dulu** (lihat bagian SYBIL) — peringkat dan hitungan "500"
   adalah **500 entitas**, bukan 500 alamat.
5. **Kontrol**: 500 entitas ACAK yang aktif di jendela pra (≥ 20 trade realized
   pra-tanggal, jadi jumlah trade sebanding), **tidak** termasuk 500 teratas.
   Dicocokkan pada **desil jumlah-trade-realized pra-tanggal** (tiap entitas
   smart-money ditandingkan dengan entitas acak dari desil yang sama). Seed
   RNG = `20260907`.
6. Ukur performa **kedua grup** di jendela pasca:
   - **Metrik primer**: return per-trade realized (%) tingkat-entitas — untuk
     tiap entitas, median return realized per round-trip di jendela pasca;
     lalu bandingkan **median antar-grup** (median dari median entitas).
   - **Metrik sekunder**: % entitas dengan total PnL realized (USD) positif di
     jendela pasca.
   - **Deskriptif** (dilaporkan, bukan kriteria): korelasi rank PnL pra vs
     pasca pada gabungan kedua grup; berapa % top-500 pra masih top-desil pasca.
7. **95% CI bootstrap** (10.000 resample entitas dalam tiap grup) untuk selisih
   `smart − acak` pada metrik primer dan sekunder, di tiap tanggal pemisah.

#### Kriteria LULUS Pertanyaan A (ditetapkan sekarang, tidak diubah)

A dinyatakan **LULUS hanya jika SEMUA** berikut benar **di ketiga tanggal
pemisah**:

1. Selisih metrik primer `median(smart) − median(acak)` ≥ **+3 poin persentase**.
2. 95% CI bootstrap selisih itu **tidak melewati nol**.
3. **Tanda selisih positif dan konsisten** di S1, S2, S3 (tidak boleh ada satu
   pun yang negatif atau nol).
4. Selisih metrik sekunder (% entitas PnL-positif, `smart − acak`) ≥ **+10 pp**
   dengan arah konsisten di ketiga tanggal.

Kalau **salah satu** gagal di **salah satu** tanggal → **A GAGAL → BERHENTI.**

Alasan S3 tetap dimasukkan meski tipis: kalau efek nyata, ia harus muncul juga
di jendela pendek (besaran boleh lebih berisik, CI lebih lebar); kalau S3
membalik arah, itu bukti persistensi rapuh.

### PERTANYAAN B — apakah pola akumulasinya bisa ditradingkan?

**Hanya dijalankan kalau A LULUS.**

**Sinyal cluster**: **≥ 5 entitas berkualitas** (dari peringkat *point-in-time*
— lihat bawah) membeli **token yang sama** dalam **jendela rolling 72 jam**.
Timestamp sinyal lengkap = block_time pembelian entitas ke-5.

- **Peringkat point-in-time**: leaderboard entitas berkualitas dihitung ulang
  **tiap awal bulan** memakai trailing **12 bulan** sampai awal bulan itu
  (ambang sama: PnL ≥ $50k, WR ≥ 55%, ≥ 20 trade realized). Sinyal pada tanggal
  T memakai leaderboard bulanan **terakhir yang jatuh sebelum T**. Tidak ada
  kebocoran: identitas "berkualitas" tidak pernah memakai data setelah T.
- **Dedup sinyal**: beberapa sinyal pada token sama dalam **14 hari** dihitung
  **satu** (pakai yang pertama).
- **Harga masuk**: close candle harian UTC pertama **setelah** timestamp sinyal
  (ATURAN HARGA MASUK di atas).
- **Horizon**: return close-to-close **3, 7, 14 hari kalender** dari harga masuk.
- **Biaya**: return dilaporkan **bersih biaya 10% round-trip** (5% per sisi)
  sebagai angka utama. Sensitivitas 0% dan 30% round-trip dilaporkan
  berdampingan. Catatan wajib di laporan: 5%/sisi **mungkin optimistik** untuk
  token dekat lantai likuiditas.
- **Token yang mati sebelum horizon selesai** (pool reserve < $10k atau LP
  ditarik): return exit dipatok **−90%** (tidak bisa keluar realistis). Bukan
  di-drop.

**Kontrol**: token pasca-graduation **ACAK** di periode kalender yang sama,
lolos lantai likuiditas yang sama pada tanggal acak dalam masa hidupnya, harga
masuk = close harian pertama setelah tanggal acak itu, horizon & biaya & aturan
token-mati identik. Diambil **5× jumlah sinyal**. Seed `20260907`.

#### Kriteria LULUS Pertanyaan B (ditetapkan sekarang, tidak diubah)

B dinyatakan **LULUS hanya jika SEMUA**:

1. **≥ 30 sinyal cluster independen** (setelah dedup 14-hari & sybil grouping).
   Kalau < 30 → hasil **"TIDAK KONKLUSIF"**, bukan "lulus", bukan "gagal".
2. `median return bersih (sinyal) − median return bersih (kontrol)` ≥
   **+10 poin persentase** pada **minimal satu** horizon.
3. 95% CI bootstrap (10.000 resample) selisih itu **tidak melewati nol**.
4. **Tanda** `sinyal − kontrol` **positif di ketiga horizon** (3, 7, 14 hari) —
   besaran boleh beda, arah tidak boleh membalik.
5. Selisih **bertahan** setelah membuang **3 winner teratas** dari grup sinyal
   (bukan artefak ekor gemuk — pelajaran berulang proyek ini, lihat
   `RINGKASAN_AKHIR.md` & "Pelajaran: pembalikan max_plausible_rr" di
   `CLAUDE.md`).

Kalau salah satu gagal → **B GAGAL.** Laporkan dan berhenti.

---

## UNIVERSE (ditetapkan SEBELUM mengambil data)

**Token pasca-graduation Solana saja** — token yang bonding curve Pump.fun-nya
selesai dan likuiditasnya bermigrasi ke pool **PumpSwap (pump_amm)** atau
**Raydium** lewat migrator Pump.fun.

**Konstruksi (sekali, dibekukan ke `.cache_whale/universe_whale.json`):**

1. Enumerasi **semua** event graduation / pembuatan pool migrasi di jendela
   **2025-01-01 → 2026-09-01** dari tabel Dune (`pump_amm` pool-created +
   migrasi Raydium Pump.fun). Ambil mint address token + timestamp graduation.
2. **Survivorship**: token dipertahankan **tanpa memandang** status sekarang —
   yang sudah mati / di-rug / nol likuiditas **tetap di universe**. Ini
   wajib (lihat bagian SURVIVORSHIP).
3. **Lantai likuiditas — diterapkan per-sinyal, bukan saat konstruksi
   universe**: pada timestamp sinyal (B) atau tanggal entry acak (kontrol),
   perkirakan **pool reserve dalam USD** dari reserve AMM
   (`reserve_usd ≈ 2 × sol_reserve × harga_SOL`, produk-konstan). Token dengan
   `reserve_usd < $150.000` **pada saat itu** dikeluarkan dari sinyal/kontrol
   itu. Token tetap ada di universe; hanya sinyal spesifik yang gugur.
   - Kalau reserve AMM tidak bisa ditarik skala penuh di tier gratis Dune:
     proksi = **volume USD trailing-7-hari ≥ $1.000.000** (kira-kira setara
     pool $150k). Kalau proksi dipakai, itu **melemahkan hasil** dan ditulis
     menonjol di `HASIL_WHALE.md`.
4. **Kecualikan dari universe**: mint yang tak pernah punya pool
   PumpSwap/Raydium (belum graduation); wrapped SOL / stablecoin / LST
   (bukan memecoin); token dengan < 5 hari data harga harian sama sekali
   (tak bisa dihitung horizon apa pun — dicatat jumlahnya).

Universe **dibekukan sekali**. Tidak ada penyaringan ulang berdasarkan hasil.

---

## PERHITUNGAN PnL WALLET (ditetapkan sekarang)

Sumber: `dex_solana.trades` (spellbook Dune) — Raydium, Orca, PumpSwap
(`pump_amm`), Meteora, dll. Per swap diketahui `trader_id` (wallet),
mint dibeli/dijual, jumlah token, dan `amount_usd`.

- Per `(wallet, mint)`: buku posisi **FIFO**. Beli = tambah lot (jumlah token,
  biaya USD = `amount_usd`). Jual = tutup lot tertua, **PnL realized USD** =
  `amount_usd_jual × (porsi) − biaya_USD_lot`.
- **Trade realized** = satu event jual yang menutup sebagian/seluruh posisi.
  Ambang "≥ 20 trade" dan win rate dihitung atas event jual realized ini.
- **Win rate** = fraksi event jual realized dengan PnL realized > 0.
- PnL realized total wallet = jumlah seluruh `(wallet, mint)`.
- **Diabaikan** (batasan, dicatat): PnL belum realized, airdrop, token diterima
  bukan lewat swap (transfer masuk), rebate MEV, biaya gas SOL. Konsekuensi:
  wallet yang menerima airdrop lalu jual akan tampak PnL sangat tinggi dengan
  "biaya nol" — sebagian tertangani oleh syarat WR ≥ 55% & ≥ 20 trade, tapi
  tidak seluruhnya. Dicatat di `HASIL_WHALE.md`.

---

## SYBIL — WAJIB DITANGANI (ditetapkan sekarang)

Tanpa ini, "30 whale membeli token X" bisa berarti **satu tim dengan 30
dompet**. Grouping dilakukan **sebelum** peringkat 500-teratas dan **sebelum**
menghitung N pada sinyal cluster.

**Dua graf digabung lewat union-find → entitas:**

1. **Graf pendanaan.** Transfer SOL masuk **pertama** ke tiap wallet (system
   program transfer) = *funder*-nya. Wallet dengan funder sama → satu entitas.
   - **Dua hop**: kalau funder F sendiri wallet dengan < 50 trade seumur hidup
     dan didanai oleh G, atribusikan ke G. Maksimum 2 hop.
   - **Blocklist funder ber-derajat tinggi**: alamat yang mendanai > 500 wallet
     berbeda (CEX hot wallet, bridge, router, disperse tool) **tidak dihitung
     sebagai funder** — daftar ini dibangun dari data **sebelum** analisis apa
     pun dan disimpan ke `.cache_whale/`.
2. **Graf co-trading.** Dua wallet yang sama-sama membeli mint sama dalam
   **5 menit** satu sama lain, pada **≥ 3 mint berbeda**, DAN di mana co-buy
   semacam itu ≥ **50%** dari pembelian wallet yang lebih kecil → satu entitas.

**Dilaporkan di `HASIL_WHALE.md`:**
- % wallet berkualitas yang terserap ke entitas multi-wallet.
- Distribusi ukuran entitas (histogram).
- Berapa banyak sinyal "5 wallet" mentah yang **runtuh di bawah N=5** setelah
  grouping (ini angka kunci — kalau mayoritas runtuh, sinyal cluster sebagian
  besar sybil).

---

## SURVIVORSHIP TOKEN — WAJIB DITANGANI

Universe dienumerasi dari **event graduation**, bukan dari snapshot token yang
masih hidup. Token mati **tetap ada**. Untuk horizon return:

- Harga harian token = close pool utama (reserve terbesar) hari itu.
- Kalau token berhenti diperdagangkan sebelum horizon selesai: harga di-carry
  dari trade terakhir; kalau pool reserve < $10k atau LP ditarik (rug), return
  exit dipatok **−90%** (lihat B).
- Jumlah token yang mati sebelum 3 / 7 / 14 hari **dilaporkan** sebagai statistik
  tersendiri (base rate kematian).

Kalau data hanya berisi token hidup, hasilnya fiksi — ini diperiksa eksplisit:
laporan menyebut jumlah token universe yang **sekarang** nol likuiditas.

---

## BASE RATE — WAJIB DITANGANI

Konteks: Pump.fun Agustus 2026 = **1,03 juta launch, 18.202 graduation (1,8%)**.
Graduation sendiri sudah sangat selektif.

Yang dilaporkan:
- Fraksi **tanpa syarat** token graduated yang return-nya positif (bersih 10%)
  pada 3 / 7 / 14 hari dari tanggal entry acak — ini **kontrol utama** B.
- Median & distribusi return token graduated acak per horizon.
- Selisih sinyal vs base rate ini adalah klaim yang diuji, bukan return absolut
  sinyal.

---

## BIAYA — WAJIB DITANGANI

- **Minimal 5% per sisi (10% round-trip)** sebagai angka utama.
- Sensitivitas: 0%/sisi dan 15%/sisi (30% round-trip) dilaporkan berdampingan.
- Kalimat wajib di laporan: untuk token dekat lantai $150k, 5%/sisi kemungkinan
  **optimistik**; slippage nyata bisa lebih besar, ditambah kemungkinan gagal
  keluar sama sekali saat token jatuh.

---

## DATA & PENYIMPANAN

- **Sumber**: Dune Analytics **tier gratis** (API key user). Retrospektif —
  cocok untuk backtest, bukan real-time.
- **Batasan kredit** (1.000 kredit/bln, batas ukuran hasil): kerja berat
  dilakukan **di SQL Dune** (agregasi wallet-bulan, deteksi cluster, leaderboard,
  deret harga harian). Yang di-*download* hanya agregat: leaderboard (≤ beberapa
  ribu baris), event sinyal (≤ beberapa ribu), deret harga harian per token,
  tabel graf pendanaan/co-trade yang sudah diringkas. **Raw swap tidak pernah
  di-download.**
- Kalau kredit tetap tidak cukup untuk cakupan penuh: **sampling di-pra-
  registrasi di sini** — ambil **acak 40% mint universe**, seed `20260907`,
  ditetapkan sebelum melihat hasil apa pun. Bukan dipilih setelah lihat data.
- **Penyimpanan lokal**: **DuckDB** (`.cache_whale/whale.duckdb`). Volume
  terlalu besar untuk parquet flat / commit.
- `.cache_whale/` **gitignored**. Yang di-commit: `HIPOTESIS_WHALE.md`,
  `HASIL_WHALE.md`, script, dan CSV hasil ringkas (leaderboard teranonim /
  di-hash, tabel IC/return, tidak ada data pribadi wallet mentah kalau bisa
  dihindari — alamat wallet publik di on-chain, tapi tetap di-hash di artefak
  commit untuk kerapian).

---

## HOLDOUT

- **Pertanyaan A**: mekanisme ketahanan = **3 tanggal pemisah**. Tidak ada
  tuning antar-tanggal. Semua parameter sudah beku.
- **Pertanyaan B**: 30% entitas disisihkan (hash id entitas, seed `20260907`)
  dan **tidak disentuh** — dipakai hanya kalau ada pilihan insidental yang
  muncul saat analisis. Angka B yang dilaporkan pakai 70% discovery. Konsisten
  dengan norma proyek (`HIPOTESIS_DEFI.md`).
- Seed `20260907` **baru** untuk proyek ini (`20260904/05/06` sudah terpakai di
  uji lain — tidak dipakai ulang).

---

## BATASAN YANG DIAKUI DI DEPAN (ditulis menonjol di `HASIL_WHALE.md`)

1. **Kelengkapan `dex_solana.trades`** untuk PumpSwap periode awal (2025) mungkin
   tidak sempurna — spellbook menambah adapter seiring waktu.
2. **PnL wallet mengabaikan** unrealized / airdrop / MEV / gas (lihat
   Perhitungan PnL).
3. **Bot sandwich / MEV** bisa lolos masuk set "berkualitas" meski ada syarat
   WR & jumlah trade.
4. **Rekonstruksi likuiditas pool approksimatif** (produk-konstan dari reserve
   SOL; atau proksi volume).
5. **S3 (2026-07-01)** punya jendela pasca hanya ~2 bulan — CI lebih lebar,
   horizon 14 hari mepet.
6. **Logika spellbook as-revised**: Dune menghitung ulang tabel saat adapter
   diperbaiki; tidak ada snapshot point-in-time. Efeknya jauh lebih kecil dari
   kasus DefiLlama (harga/jumlah token swap objektif) tapi dicatat.
7. **Tidak ada koreksi untuk wash trading** intra-entitas di luar yang
   tertangkap sybil grouping.

---

## ATURAN UMUM

- Semua peringkat wallet memakai **hanya data sebelum tanggal pemisah /
  sebelum T**. Tidak ada lookahead.
- Harga masuk B **selalu** close harian pertama setelah sinyal. Tidak pernah
  harga whale.
- Ambang ($50k / 55% / 20 trade / N=5 / $150k / +3pp / +10pp / +10pp / 30 sinyal
  / biaya 10%) **tidak diubah** setelah melihat hasil.
- **Kalau A gagal: laporkan dan BERHENTI.** Jangan coba definisi whale lain —
  itu data dredging.
- **Laporkan semua hasil**, termasuk yang mengecewakan.
- `scoring.py` / `indicators.py` / `accumulation.py` / screener **tidak
  disentuh** — ini proyek riset terpisah, seperti `orderflow_test.py` &
  `defi_test.py`.
- Tidak ada fungsi order/trading. Read-only selamanya.
