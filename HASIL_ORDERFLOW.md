# Hasil Uji Nilai Prediktif Aliran Order (2026-09-05)

Protokol & hipotesis: [`HIPOTESIS_ORDERFLOW.md`](HIPOTESIS_ORDERFLOW.md) — di-commit sebelum
run apa pun. Script: `orderflow_test.py`. Data: `.cache_orderflow/` (kolom
`qav`/`trades`/`tbbav`/`tbqav` yang sebelumnya dibuang `fetch_klines()`).

## Data

- **U1 (likuid, vol24h ≥ $5jt):** 62 pair, **57** dengan data cukup (≥100 bar
  setelah unduh — 3 tanpa file parquet, 2 lagi <100 bar), **75.436** bar-hari.
  *(Dikoreksi 2026-09-11 — audit independen: angka semula 59/75.587 dari
  laporan awal menghitung file yang ada, bukan yang lolos syarat ≥100 bar di
  `compute_symbol_panel()`. Tidak mengubah satu pun IC — lihat
  `AUDIT_2026-09-11.md`.)*
- **U2 (luas, vol24h ≥ $1jt):** 158 pair, **148** dengan data cukup (6 tanpa
  parquet, 4 lagi <100 bar), **180.471** bar-hari. *(Dikoreksi sama seperti
  U1 di atas.)*
- Rentang: 2021-01-01 s/d 2026-09-04. Tidak ada gap tanggal, tidak ada NaN/nol di
  kolom order flow bahkan di bar paling awal.
- Snapshot universe: U1 diambil 2026-09-05T00:53:03Z, U2 2026-09-05T00:52:09Z
  (dicatat di `.cache_orderflow/universe_u1.json` / `universe_u2.json`, tidak
  di-commit — lihat `.gitignore`).
- **22 simbol non-kripto** (15 saham/ETF ter-tokenisasi "xStocks" + 7 aset
  pegged emas/stablecoin baru) ditemukan lolos filter universe lama dan
  dikeluarkan **sebelum** IC apa pun dihitung — lihat commit `daa941f`.
- Holdout: seed **20260905** (baru, tidak memakai ulang `20260904` dari
  `factor_test.py`), 111 simbol discovery / 47 simbol holdout dari gabungan
  U1∪U2 (158 simbol unik).

## Kriteria lulus (kelimanya, dihitung SEBELUM melihat hasil)

1. `|rata-rata IC| ≥ 0,02`
2. `|t-stat IC| ≥ 3,5` (dinaikkan dari 3,0 karena 30 uji, bukan 15)
3. Arah IC sama di 2021–2023 vs 2024–2026
4. Spread kuintil (Q5−Q1) searah dengan tanda IC
5. Bertahan di holdout 30% simbol — dioperasionalkan (sebelum run) sebagai:
   arah sama DAN `|IC holdout| ≥ 0,02`

Kriteria 1–4 dihitung pada set **discovery** (111 simbol). Kriteria 5 hanya
diperiksa untuk kombinasi yang lolos 1–4.

## Hasil — seluruh 30 uji

| Universe | Fitur | h | IC (disc) | t-stat | IC 21-23 | IC 24-26 | Arah stabil | Spread(Q5-Q1) | Verdict | Holdout |
|---|---|---:|---:|---:|---:|---:|:---:|---:|:---:|---|
| U1 | taker buy ratio | 5 | +0,0220 | +4,69 | +0,0139 | +0,0312 | ya | +0,0038 | GAGAL (holdout) | IC=−0,014 |
| U1 | taker buy ratio | 10 | +0,0240 | +5,02 | +0,0214 | +0,0269 | ya | +0,0087 | GAGAL (holdout) | IC=−0,009 |
| U1 | taker buy ratio | 20 | +0,0297 | +6,21 | +0,0267 | +0,0331 | ya | +0,0144 | GAGAL (holdout) | IC=−0,003 |
| U1 | avg trade size pct | 5 | −0,0186 | −3,70 | −0,0199 | −0,0174 | ya | +0,0018 | GAGAL | − |
| U1 | avg trade size pct | 10 | −0,0134 | −2,66 | −0,0075 | −0,0196 | ya | +0,0003 | GAGAL | − |
| U1 | avg trade size pct | 20 | −0,0123 | −2,44 | +0,0011 | −0,0264 | tidak | −0,0026 | GAGAL | − |
| U1 | trade intensity | 5 | +0,0013 | +0,24 | −0,0094 | +0,0131 | tidak | +0,0035 | GAGAL | − |
| U1 | trade intensity | 10 | +0,0079 | +1,50 | −0,0070 | +0,0245 | tidak | +0,0029 | GAGAL | − |
| U1 | trade intensity | 20 | +0,0269 | +5,16 | +0,0113 | +0,0444 | ya | +0,0145 | GAGAL (holdout) | IC=+0,008 |
| U1 | flow divergence | 5 | +0,0295 | +2,46 | +0,0343 | +0,0275 | ya | +0,0045 | GAGAL | − |
| U1 | flow divergence | 10 | +0,0051 | +0,41 | +0,0270 | −0,0045 | tidak | +0,0024 | GAGAL | − |
| U1 | flow divergence | 20 | +0,0038 | +0,29 | +0,0393 | −0,0121 | tidak | −0,0034 | GAGAL | − |
| U1 | taker buy ratio extreme | 5 | +0,0050 | +1,05 | −0,0061 | +0,0165 | tidak | +0,0031 | GAGAL | − |
| U1 | taker buy ratio extreme | 10 | +0,0045 | +0,94 | −0,0010 | +0,0103 | tidak | +0,0053 | GAGAL | − |
| U1 | taker buy ratio extreme | 20 | +0,0069 | +1,45 | +0,0013 | +0,0128 | ya | +0,0071 | GAGAL | − |
| U2 | taker buy ratio | 5 | +0,0094 | +3,07 | +0,0143 | +0,0038 | ya | +0,0015 | GAGAL | − |
| U2 | taker buy ratio | 10 | +0,0129 | +4,11 | +0,0160 | +0,0094 | ya | +0,0036 | GAGAL | − |
| U2 | taker buy ratio | 20 | +0,0167 | +5,39 | +0,0172 | +0,0161 | ya | +0,0052 | GAGAL | − |
| U2 | avg trade size pct | 5 | −0,0171 | −4,81 | −0,0277 | −0,0061 | ya | −0,0001 | GAGAL | − |
| U2 | avg trade size pct | 10 | −0,0113 | −3,29 | −0,0216 | −0,0006 | ya | −0,0008 | GAGAL | − |
| U2 | avg trade size pct | 20 | −0,0125 | −3,67 | −0,0240 | −0,0004 | ya | −0,0012 | GAGAL | − |
| U2 | trade intensity | 5 | −0,0182 | −4,87 | −0,0288 | −0,0065 | ya | +0,0014 | GAGAL | − |
| U2 | trade intensity | 10 | −0,0155 | −4,09 | −0,0266 | −0,0032 | ya | +0,0014 | GAGAL | − |
| U2 | trade intensity | 20 | −0,0034 | −0,95 | −0,0126 | +0,0068 | tidak | +0,0036 | GAGAL | − |
| U2 | flow divergence | 5 | +0,0142 | +2,31 | +0,0243 | +0,0067 | ya | +0,0029 | GAGAL | − |
| U2 | flow divergence | 10 | +0,0103 | +1,67 | +0,0161 | +0,0060 | ya | +0,0026 | GAGAL | − |
| U2 | flow divergence | 20 | −0,0008 | −0,13 | +0,0111 | −0,0097 | tidak | +0,0008 | GAGAL | − |
| U2 | taker buy ratio extreme | 5 | −0,0031 | −0,99 | −0,0001 | −0,0063 | ya | +0,0010 | GAGAL | − |
| U2 | taker buy ratio extreme | 10 | −0,0021 | −0,67 | −0,0005 | −0,0039 | ya | +0,0027 | GAGAL | − |
| U2 | taker buy ratio extreme | 20 | −0,0013 | −0,41 | −0,0001 | −0,0025 | ya | +0,0041 | GAGAL | − |

Data mentah lengkap (termasuk angka IC "full-sample", n hari per uji):
`arsip/orderflow_ic_results.csv`.

## Temuan

- **0 dari 30 uji lulus SEMUA kriteria.** Hasil yang sah sesuai protokol —
  tidak ada penyesuaian ambang.
- **4 kombinasi lolos kriteria 1–4 di discovery, tapi GAGAL di holdout** —
  semuanya di U1 (universe lebih kecil, 62 simbol → lebih rentan
  overfitting-per-koin):
  - `taker_buy_ratio` (F1) di ketiga horizon: IC discovery positif & signifikan
    (t hingga +6,21), tapi **arah IC BERBALIK NEGATIF** di 47 simbol holdout.
    Pola klasik sinyal yang "bekerja" karena beberapa koin tertentu di sampel
    discovery, bukan efek aliran order yang genuin.
  - `trade_intensity` (F3) h=20: IC discovery +0,027 (t=+5,16), IC holdout
    masih searah (+0,008) tapi di bawah ambang minimum 0,02 — melemah drastis
    begitu koin discovery diganti.
- **`avg_trade_size_pct` (F2) arahnya KONSISTEN NEGATIF** di kedua universe —
  berlawanan dengan dugaan pra-registrasi (dugaan: partisipan lebih besar →
  return lebih baik). t-stat cukup besar (hingga −4,81) tapi **magnitudo IC
  tidak pernah mencapai 0,02 di discovery** (maksimum |0,0186|) — jadi tidak
  pernah lolos kriteria 1 sama sekali, holdout tidak perlu diperiksa. Arah
  yang salah dan konsisten ini sendiri instruktif: ukuran trade rata-rata
  yang MENAIK relatif terhadap sejarah koin sendiri sedikit **anti-prediktif**,
  bukan tanda "smart money masuk" seperti diduga.
- **`flow_divergence` (F4)** — fitur yang paling dekat menangkap ide OBV
  (akumulasi diam-diam saat harga datar) — IC positif di kedua universe pada
  horizon pendek (h=5: +0,0295 U1, +0,0142 U2) tapi **arah TIDAK stabil**
  antar sub-periode (positif kuat 2021-2023, melemah/berbalik 2024-2026) dan
  t-stat tidak pernah tembus 2,5. Sampel jauh lebih kecil dari fitur lain
  (n≈700-1.230 hari, bukan ~2.000) karena syarat kondisi harga datar —
  sesuai dugaan, tapi tidak cukup untuk lolos ambang manapun.
- **`taker_buy_ratio_extreme` (F5), diuji dua arah sesuai rencana** — IC
  nyaris nol di semua kombinasi (|IC| ≤ 0,010, |t| ≤ 2,4). Tidak ada sinyal
  ke arah manapun; ekstrem taker-buy-ratio tidak menandai kekuatan maupun
  kelelahan pembeli di data ini.
- **Peluruhan IC antar horizon tidak mulus untuk sebagian besar fitur** —
  F1 di U1/U2 justru MENINGKAT dari h=5 ke h=20 (pola tidak lazim untuk sinyal
  order-flow asli, yang biasanya meluruh), sementara F3 di U2 dan F4 di kedua
  universe berbalik arah antar horizon. Pola melompat ini konsisten dengan
  sinyal palsu / noise, bukan sinyal asli yang meluruh mulus seperti diduga
  di protokol.
- Bias survivorship universe (dibangun dari volume hari ini) berlaku penuh di
  sini juga — lihat `HIPOTESIS_ORDERFLOW.md`.

## Kesimpulan

**Tidak ada fitur aliran order (dari 5 yang diuji) dengan nilai prediktif
cross-sectional yang bertahan discovery + holdout, di kedua universe, di
kedua sub-periode.** Yang paling dekat lolos (`taker_buy_ratio`) justru
berbalik arah total begitu diuji pada koin yang belum pernah dilihat — sinyal
palsu klasik dari overfitting universe kecil, ditangkap justru karena
protokol mewajibkan holdout dengan seed baru.

Konsisten dengan seluruh rangkaian uji proyek ini sebelumnya (skor komposit,
faktor tunggal, mekanik entry-acak, detektor regime) — lihat
`RINGKASAN_AKHIR.md`. Aliran order (taker buy ratio, ukuran trade, intensitas
trade, divergensi, ekstrem) menambah satu kategori data lagi yang **tidak**
menghasilkan edge terukur di universe/periode ini.
