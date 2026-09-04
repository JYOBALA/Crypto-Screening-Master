# Hasil Uji Faktor Tunggal (2026-09-04)

Protokol & hipotesis: [`HIPOTESIS_FAKTOR.md`](HIPOTESIS_FAKTOR.md) — di-commit sebelum run.
Script: `factor_test.py`. Data: `.cache_history/` (422 koin, 2021–2026).
Sinyal terkumpul: **13.020** (364 koin, 2021-09 → 2026-07), semua non-veto walk-forward
tanpa lookahead, `pnl_r` dari mekanik backtest yang sama.

## Konteks — buy & hold per koin

| | |
|---|---|
| n koin | 381 |
| Return rata-rata | **−63,8%** |
| Return median | **−83,4%** |
| Koin naik | **9%** |
| Persentil 25 / 75 | −94,6% / −53,1% |

Bahkan koin persentil-75 kehilangan setengah nilainya. Lahan long-only di sini
sangat buruk.

## Mekanik trade (dari CSV)

75% sinyal (9.775 / 13.020) kena **SL penuh** (−1,10 R). Win rate **23,2%**.
Ekspektasi agregat +0,07 R sepenuhnya dari ~2.500 trade yang menembus TP
(TP1_BE +1,9 R, TP1_TIMEOUT +4,7 R, TP1_TP2 +7,0 R). Per tahun: positif hanya
di 2023 (+0,53) & 2024 (+0,27); 2025 −0,13, 2026 −0,15.

## ANOVA — varians pnl_r dijelaskan identitas koin

**eta-squared = 2,3%, F = 0,8.** F < 1 → identitas koin **tidak** memprediksi
hasil per-trade lebih baik dari kebetulan. Return hold jangka panjang koin ≠
hasil trade jangka pendek (entry limit di pullback, exit 8 bar). Konsekuensi:
hasil mentah & demeaned mirip untuk sebagian besar faktor.

## Discovery (70% koin, 9.245 sinyal) — Spearman vs pnl_r

| Faktor | mentah | demeaned | verdict | tafsiran |
|---|---:|---:|---|---|
| vol_ratio | +0,008 | +0,017 | gagal | tidak ada sinyal |
| obv_slope | +0,097 | +0,009 | gagal | lemah/ambigu |
| stochrsi_k | +0,072 | +0,024 | gagal | lemah/ambigu |
| stochrsi_htf | +0,095 | +0,018 | gagal | lemah/ambigu |
| fib_retr | −0,167 | −0,022 | gagal | **proksi kualitas koin** — bukan timing |
| **dist_to_res_pct** | +0,153 | **+0,101** | **LOLOS** | punya komponen timing |
| dist_ema50_pct | +0,057 | −0,027 | gagal | lemah/ambigu |
| atr_pct | +0,160 | +0,019 | gagal | **proksi kualitas koin** — bukan timing |
| rs_btc_30d | +0,074 | −0,015 | gagal | lemah/ambigu |
| dd_from_1y_high_pct | −0,066 | −0,095 | gagal | lemah/ambigu |

Catatan: `atr_pct` & `fib_retr` punya Spearman mentah kuat (±0,16) yang **hilang**
setelah demeaned — persis pola "faktor cuma proksi ukuran/volatilitas koin".
`rs_btc_30d` (faktor prioritas): Q5 (koin yang paling mengungguli BTC) justru
**terburuk** (−0,17 R). Arahnya kebalikan dugaan, konsisten dengan temuan 3d
(hubungan alt–BTC terbalik) — tapi demeaned +/−0,015, jadi bukan sinyal timing.

## Holdout (30% koin, 3.775 sinyal) — sekali jalan

Hanya `dist_to_res_pct` yang diuji (satu-satunya yang lolos discovery).

| Q | n | E[R] mentah | E[R] demeaned |
|---|---:|---:|---:|
| 1 | 747 | +0,145 | +0,032 |
| 2 | 747 | −0,012 | −0,101 |
| 3 | 747 | +0,178 | +0,101 |
| 4 | 747 | −0,037 | −0,063 |
| 5 | 747 | −0,042 | −0,024 |

Spearman demeaned +0,115 (lolos ambang) **tapi**: tidak monoton, Q5−Q1 = −0,06 R
(arah **Q1>Q5**, kebalikan discovery yang Q5>Q1), tidak stabil antar-periode
(−0,29 / +0,07). **GUGUR.** Tidak diuji ulang.

## Kesimpulan

**Tidak ada faktor yang bertahan.** Sembilan faktor mentah, diuji sendiri-sendiri
di 13.020 sinyal (discovery 9.245 + holdout 3.775), tidak ada satu pun yang
memisahkan momen lebih baik dari momen lebih buruk di dalam koin yang sama,
secara stabil dan lolos out-of-sample.

Screener ini memilih *waktu*. Tidak ada faktor tunggal yang membantunya melakukan
itu. Tidak ada yang untuk dimasukkan ke `scoring.py`.
