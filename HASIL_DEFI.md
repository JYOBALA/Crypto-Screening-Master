# Hasil Uji Nilai Prediktif Data Fundamental Protokol — DefiLlama (2026-09-06)

Protokol & hipotesis: [`HIPOTESIS_DEFI.md`](HIPOTESIS_DEFI.md) — di-commit
(`510e115`) **sebelum** `fetch_defi.py` / `defi_test.py` dibuat dan sebelum
angka apa pun dilihat. Ambang & metodologi di bawah **tidak diubah** setelah
melihat hasil.

Script: `fetch_defi.py` (unduh) + `defi_test.py` (uji). Data: `.cache_defi/`
(gitignored, seperti `.cache_orderflow/`). Harga token: `.cache_history/`
(Binance daily, sama seperti `backtest.py`).

**Ini kategori data KEENAM yang diuji di proyek ini.** Lima sebelumnya (skor
komposit, faktor tunggal, mekanik entry-acak, detektor regime, aliran order)
semuanya turunan harga/volume dan semuanya null — `RINGKASAN_AKHIR.md`. Uji ini
memakai data yang berbeda secara mendasar: **pemakaian protokol sebenarnya**
(TVL, fee/revenue, volume DEX, pasokan stablecoin per chain).

---

## Data & pemetaan protokol → simbol Binance

| | |
|---|---|
| Base asset USDT spot Binance | 478 |
| Protokol di `/protocols` DefiLlama | 8.190 |
| **Berhasil dipetakan** | **194** |
| Gagal dipetakan | 284 |
| &nbsp;&nbsp;— tidak ada protokol DefiLlama dengan ticker itu | 243 |
| &nbsp;&nbsp;— kategori dikecualikan (Chain 33 / CEX 4 / Basis Trading 4) | 41 |
| Dipetakan lewat **tabrakan ticker** (dipilih TVL sekarang terbesar) | 107 dari 194 |
| Protokol dengan riwayat < 180 hari (TVL & fee) → dibuang dari uji | 20 |
| Tanpa deret harga `.cache_history` yang cukup | 2 |
| **Universe efektif uji** | **172 protokol** |
| Panel | 201.776 baris (protokol × hari), 2021-01-01 … 2026-09-02 |
| Split holdout (seed `20260906`, 30%) | 120 discovery / 52 holdout |

**Catatan pemetaan (dilaporkan, bukan disembunyikan):**

- **107 dari 194 dipetakan lewat tabrakan ticker** — beberapa protokol berbagi
  ticker token, atau ticker menabrak koin lain. Kanonik dipilih = TVL sekarang
  terbesar (tie-break: nama terpendek). Contoh yang benar: `UNI`→Uniswap V3,
  `AAVE`→Aave V3, `CRV`→Curve DEX, `CAKE`→PancakeSwap AMM. Contoh yang
  **borderline**: `ARB`/`OP`/`STRK`/`METIS`/`AVAX`/`MANTA`/`G` dipetakan ke
  **canonical bridge** chain masing-masing (kategori "Canonical Bridge" tidak
  ada di daftar exclude pra-registrasi) — TVL bridge kanonik pada dasarnya
  proksi TVL chain, jadi 12 protokol "Canonical Bridge" di universe efektif
  fungsinya mirip data level-chain, bukan protokol murni. Tidak dikeluarkan
  pasca-fakta (itu akan jadi penyetelan seleksi setelah lihat data); dicatat
  sebagai keterbatasan.
- `EXCLUDE_CATEGORIES` di `fetch_defi.py` memperluas daftar pra-registrasi
  (Stablecoin / Memes / parent protocol) dengan **Chain, CEX, Basis Trading** —
  ditetapkan **sebelum** IC apa pun dihitung, alasannya: bukan data pemakaian
  protokol on-chain. Dicatat di sini untuk transparansi.
- 1 protokol berkategori `Meme` (singular, "Official Trump" — filter
  pra-registrasi menyebut `Memes` plural) lolos filter kategori, tapi punya
  0 hari TVL & fee → otomatis terbuang dari universe efektif. Tanpa dampak.
- 4 chain gagal diunduh pada percobaan pertama (nama berisi spasi: "Polygon
  zkEVM" dst.) — bug URL-encoding di `fetch_defi.py`, **diperbaiki**
  (`urllib.parse.quote`) dan chain-chain itu berhasil ditarik ulang sebelum
  `defi_test.py` dijalankan.

### Survivorship (batasan permanen)

`/protocols` DefiLlama **hanya memuat protokol yang masih hidup** — yang mati /
di-rug / ditinggalkan dihapus dari daftar. Daftar simbol Binance juga hanya yang
masih listing. Universe ini = "protokol yang masih hidup hari ini dan tokennya
masih diperdagangkan, diuji mundur ke sejarahnya" — bukan seluruh populasi.
Persis analog bias koin delisting di `RINGKASAN_AKHIR.md`. **Tidak bisa
diperbaiki** dalam uji ini.

### Lookahead — data as-revised (batasan BESAR)

Endpoint historis `/protocol` dan `/summary/fees` DefiLlama mengembalikan data
**sebagaimana-direvisi (as-revised)**, bukan sebagaimana-dilaporkan-saat-itu.
Adapter DefiLlama **sering diperbaiki surut** — ketika bug adapter ditemukan,
seluruh sejarah TVL/fee protokol itu dihitung ulang. Tidak ada snapshot
point-in-time untuk mengoreksi ini.

Mitigasi yang dilakukan: semua fitur fundamental **di-lag 2 hari** (fitur pada
hari t hanya memakai data sampai t−2). Ini **tidak menghilangkan** masalah
revisi surut. **Konsekuensi: seandainya ada fitur yang lolos, hasilnya harus
dibaca sebagai batas atas optimistik** — belum tentu bisa ditradingkan
real-time. Karena tidak ada yang lolos, poin ini jadi akademis di sini, tapi
tetap wajib dicatat.

---

## Hasil — seluruh 15 uji

5 fitur × 3 horizon (5/10/20 hari). IC = Spearman harian cross-sectional antara
fitur pada t (di-lag 2 hari) dan return ke depan h-hari ter-demean. `within` =
IC pooled setelah fitur & return masing-masing di-demean per protokol
(memisahkan seleksi protokol dari timing). Discovery = 120 protokol.

**Kriteria lulus (semua wajib):** `|IC| ≥ 0,02` · `|t| ≥ 3,5` · arah sama
2021–2023 vs 2024–2026 · spread Q5−Q1 searah IC · `within` searah IC · bertahan
di holdout.

| Fitur | h | IC (disc) | t (disc) | IC 21–23 | IC 24–26 | within | spread Q5−Q1 | Verdict | Gagal di |
|---|--:|--:|--:|--:|--:|--:|--:|:--:|---|
| **D1** tvl_growth_real_30d | 5 | +0,012 | +1,47 | +0,048 | +0,003 | −0,018 | +0,003 | GAGAL | t<3,5; within lawan arah |
| **D1** | 10 | +0,026 | +3,27 | +0,126 | +0,003 | −0,030 | +0,005 | GAGAL | t<3,5; within lawan arah; 21–23 vs 24–26 timpang ekstrem |
| **D1** | 20 | +0,027 | +3,27 | +0,132 | +0,002 | −0,026 | +0,011 | GAGAL | t<3,5; within lawan arah; timpang ekstrem |
| **D2** mcap_to_fees_pct | 5 | −0,025 | −4,08 | −0,057 | −0,022 | −0,019 | **+0,003** | GAGAL | spread Q5−Q1 **berlawanan** tanda IC (tidak monoton) |
| **D2** | 10 | −0,026 | −4,72 | −0,026 | −0,026 | −0,030 | **+0,008** | GAGAL | spread berlawanan tanda IC (tidak monoton) |
| **D2** | 20 | −0,024 | −4,28 | −0,024 | −0,024 | −0,049 | **+0,020** | GAGAL | spread berlawanan tanda IC (tidak monoton) |
| **D3** fees_growth_30d | 5 | +0,007 | +1,33 | +0,013 | +0,004 | +0,002 | +0,005 | GAGAL | IC & t jauh di bawah ambang |
| **D3** | 10 | +0,002 | +0,52 | +0,015 | −0,002 | −0,010 | +0,008 | GAGAL | tidak ada sinyal; arah tak stabil |
| **D3** | 20 | −0,012 | −2,53 | +0,002 | −0,018 | −0,028 | +0,002 | GAGAL | arah berbalik antar sub-periode |
| **D4** tvl_share_of_chain_slope30 | 5 | +0,018 | +4,90 | +0,029 | +0,011 | **−0,003** | +0,004 | GAGAL | **within ≈ 0** — sinyal 100% seleksi protokol, bukan timing |
| **D4** | 10 | +0,024 | +6,08 | +0,033 | +0,017 | **−0,003** | +0,007 | GAGAL | within ≈ 0 — seleksi, bukan timing |
| **D4** | 20 | +0,027 | **+7,09** | +0,044 | +0,015 | **−0,004** | +0,010 | GAGAL | within ≈ 0 — seleksi, bukan timing |
| **D5** stablecoin_inflow_chain_14d | 5 | +0,000 | +0,08 | −0,007 | +0,009 | +0,015 | n/a | GAGAL | tidak ada sinyal; arah tak stabil |
| **D5** | 10 | +0,007 | +1,73 | +0,006 | +0,009 | +0,005 | n/a | GAGAL | IC & t di bawah ambang |
| **D5** | 20 | +0,013 | +3,23 | +0,012 | +0,015 | −0,011 | n/a | GAGAL | t<3,5; within lawan arah |

`spread` D5 = n/a: fitur level-chain → semua protokol di chain sama punya nilai
identik → `qcut` 5 kuintil sering gagal (kurang dari 5 nilai unik per hari).
Data mentah lengkap: `arsip/defi_ic_results.csv`.

---

## Temuan

### 0 dari 15 lulus. Tidak ada fitur fundamental dengan sinyal timing yang bertahan.

Tiga fitur (**D1, D2, D4**) melewati sebagian ambang tetapi masing-masing gagal
karena alasan yang **secara metodologis instruktif** — persis jenis lolos-palsu
yang protokol ini dirancang untuk menangkap:

**D4 — `tvl_share_of_chain_slope30`: sinyal cross-sectional kuat, timing nol.**
Ini kasus terbersih. IC discovery +0,018 → +0,027 dengan **t-stat sampai
+7,09** — lolos kriteria IC, t-stat, stabilitas arah, dan spread. **Tapi
`within` (IC setelah demean per protokol) = −0,003 sampai −0,004, praktis nol
dan bahkan sedikit berlawanan arah.** Artinya: "protokol yang sedang merebut
pangsa TVL chain-nya adalah protokol yang lebih baik" (seleksi) — tapi
"protokol X sedang merebut pangsa **sekarang**" tidak mengatakan apa pun tentang
apakah **sekarang** momen yang bagus untuk masuk ke X. Seluruh sinyal ada di
level "protokol mana", bukan "kapan". Tidak bisa dipakai untuk timing entry.

**D1 — `tvl_growth_real_30d` (sudah dikoreksi harga): `within` berlawanan
arah + runtuh setelah 2023.** Meski TVL sudah dibuat harga-netral (Laspeyres,
kuantitas token native dinilai pada harga t−30 — jebakan #3), IC cross-sectional
tetap didominasi sub-periode awal: **IC 2021–2023 = +0,13, IC 2024–2026 =
+0,002** (nyaris hilang). `within` = −0,03 (lawan arah). t-stat discovery 3,27
< 3,5. Gabungan ini: pertumbuhan TVL harga-netral menandai protokol yang lebih
baik **di pasar 2021–2023**, bukan sinyal timing yang berlaku umum.

**D2 — `mcap_to_fees` (valuasi relatif pemakaian): IC kuat & signifikan, tapi
tidak monoton.** IC discovery −0,024 → −0,026, **t-stat −4,1 sampai −4,7**,
arah stabil kedua sub-periode, `within` searah (negatif). Arahnya **sesuai
dugaan** (rasio rendah / murah → return lebih baik). **Tapi spread kuintil
Q5−Q1 justru POSITIF** (+0,003 → +0,020): kuintil termahal (Q5) rata-rata
mengungguli kuintil termurah (Q1), berlawanan dengan korelasi rank yang
negatif. Hubungannya tidak monoton — korelasi rank menangkap kecenderungan
rata-rata "lebih murah sedikit lebih baik" di tengah distribusi, tapi ekstrem
termurah (protokol yang secara historis paling murah relatif fee-nya) justru
berkinerja buruk. Kriteria 4 (spread searah IC) gagal — bukan formalitas, itu
menangkap bahwa "sinyal"-nya tidak bisa diandalkan di ujung distribusi tempat
sebuah trade sesungguhnya diambil.
Catatan tambahan: D2 memakai harga sebagai proksi mcap (asumsi pasokan token
~konstan dalam jendela trailing 365 hari — emisi/burn besar membiaskan
persentil). Karena D2 gagal karena non-monotonisitas yang bukan soal skala,
asumsi ini tidak jadi penentu di sini, tapi tetap dicatat.

**D3 (`fees_growth_30d`) dan D5 (`stablecoin_inflow_chain_14d`): tidak ada
sinyal ke arah mana pun.** IC di bawah 0,014 di semua horizon, t-stat di bawah
ambang, arah tidak stabil antar sub-periode. Pertumbuhan fee 30-hari dan aliran
stablecoin 14-hari ke chain tidak memisahkan return relatif ke depan di
universe/periode ini.

### Peluruhan IC antar horizon

D1, D4, dan (magnitudo) D2 **meningkat** dari h=5 ke h=20, bukan meluruh. Sinyal
order-flow / fundamental asli biasanya paling kuat di horizon pendek lalu
meluruh. Pola menaik ini konsisten dengan yang terlihat di `taker_buy_ratio`
(`HASIL_ORDERFLOW.md`) — indikatif efek yang didorong tren lambat / komposisi
sampel, bukan prediksi genuin yang meluruh mulus.

---

## Kesimpulan

**Tidak ada fitur data fundamental (dari 5 yang diuji) dengan nilai prediktif
cross-sectional untuk timing yang bertahan discovery + holdout.** Kandidat
terkuat gagal bukan karena sampel kecil:

- **D4** gagal karena `within` = 0 — sinyalnya murni seleksi protokol, tidak ada
  komponen timing sama sekali. (Tidak sampai diuji di holdout: gagal gate
  within-symbol lebih dulu.)
- **D2** gagal karena tidak monoton (Q5−Q1 lawan arah IC).
- **D1** gagal karena `within` lawan arah + efeknya terkonsentrasi 2021–2023.

Karena tidak ada yang lolos kriteria 1–5 di discovery, **holdout 30% (seed
`20260906`) tidak terpakai** dan tetap tersedia untuk uji lanjutan bila kelak
ada data/fitur baru.

Konsisten dengan seluruh rangkaian uji proyek ini sebelumnya. Enam kategori data
kini diuji dengan metodologi pra-registrasi yang sama — skor komposit, faktor
tunggal, mekanik entry-acak, detektor regime, aliran order, **dan sekarang data
fundamental protokol** — **semua null** di universe & periode ini.

### Apa yang TIDAK boleh disimpulkan

1. **BUKAN "fundamental DeFi tidak berguna."** Yang diuji: 5 operasionalisasi
   spesifik, sebagai faktor cross-sectional tunggal, pada return token Binance
   harian, di universe protokol yang **masih hidup**, 2021–2026, dengan data
   **as-revised**. Definisi fitur lain, kombinasi, horizon lain, atau data
   point-in-time bisa berbeda.
2. **BUKAN "D4/D2 terbukti tidak berguna di mana pun."** D4 punya sinyal
   cross-sectional nyata (t=7) — hanya bukan sinyal *timing*. Untuk strategi
   yang memang cross-sectional (mis. ranking beli-tahan long-only lintas
   protokol) D4 belum terbantahkan; itu di luar cakupan uji ini.
3. **BUKAN hasil real-time.** Data as-revised + tidak ada snapshot point-in-time
   berarti seandainya ada fitur lolos pun, ia belum tentu bisa ditradingkan.
   Batasan ini tidak teruji karena tidak ada yang lolos.
4. **Survivorship penuh berlaku.** Protokol mati tidak ada di `/protocols`.
5. **Bagian universe berbasis bridge kanonik** (12 protokol) efektif mengukur
   data level-chain, bukan protokol — mengencerkan "kemurnian" uji fundamental
   sedikit, tapi tidak mengubah kesimpulan null.

---

## Artefak

- `fetch_defi.py` — unduh DefiLlama → `.cache_defi/` (read-only, tanpa API key).
- `defi_test.py` — 15 uji IC, output `arsip/defi_ic_results.csv`.
- `HIPOTESIS_DEFI.md` — pra-registrasi (commit `510e115`).
- `.cache_defi/` (gitignored): `universe_defi.json` (tabel pemetaan lengkap),
  `raw/*.json`, `_chains/*.json`, `fetch_log.txt`.
