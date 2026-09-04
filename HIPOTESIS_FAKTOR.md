# Uji Faktor Tunggal — Protokol & Hipotesis (pra-registrasi)

**Ditulis dan di-commit SEBELUM melihat hasil.** Tujuannya: mencegah penafsiran
post-hoc. Kalau hasil tidak cocok dengan hipotesis di bawah, itu dilaporkan apa
adanya — hipotesis TIDAK diedit setelah melihat data.

Skor komposit 5 komponen (`scoring.py`) sudah terbukti **gagal** di backtest
422-pair (korelasi skor↔hasil ~nol, pita tidak monoton, total −120 R). Skor itu
tidak akan disetel. Pertanyaan sekarang berbeda: **apakah ada SATU faktor mentah
yang, sendirian, memisahkan trade menang dari kalah?**

## Sumber sinyal

Signal set = keluaran `scoring.evaluate()` yang TIDAK ter-veto, walk-forward tanpa
lookahead, di `.cache_history/` (422 pair, 2021–2026). Sama persis dengan backtest
v2 (~10.682 trade terisi). Nilai faktor dihitung dari `df.iloc[:t+1]` di bar sinyal.
`pnl_r` dari mekanik eksekusi yang sama (entry limit ≤10 bar, SL/TP1 parsial/TP2/
timeout 30 bar, fee 0.1% + slip 0.05%, SL menang kalau sebar).

Skor total **diabaikan** untuk pembagian kuintil.

## Kriteria kelulusan (satu faktor "punya sinyal" HANYA jika SEMUA terpenuhi)

1. Ekspektasi (R) **monoton** di 5 kuintil, ATAU minimal **|Q5 − Q1| > 0.15 R**.
2. **Spearman** (nilai faktor vs `pnl_r`) minimal **|0.10|**.
3. **n ≥ 500** per kuintil.
4. **Arah stabil**: tanda (Q5 − Q1) sama di sub-periode **2021–2023** dan **2024–2026**.

Arah terbalik juga dihitung sebagai sinyal: kalau **Q1 ≫ Q5** dengan kriteria yang
sama terpenuhi, faktor itu punya sinyal — tandanya saja kebalikan dari dugaan.

## Konteks wajib (dilaporkan SEBELUM hasil faktor)

Return buy-and-hold rata-rata & median per koin di universe ini, 2021–2026 (dari
bar warm-up ke-250 sampai akhir data). Kalau hold saja rugi besar, seluruh sistem
long-only di lahan ini menghadapi tanah buruk — itu membingkai semua hasil.

## Faktor yang diuji (9) + hipotesis arah

| # | Faktor | Definisi (di bar sinyal, kausal) | Hipotesis (pra-registrasi) |
|---|---|---|---|
| 1 | `vol_ratio` | volume[-1] / rata-rata volume 20 bar sebelumnya | **Q5 > Q1** — lonjakan volume = konfirmasi. Dugaan lemah; volume sering hanya noise. |
| 2 | `obv_slope` | kemiringan % OBV atas 20 bar terakhir (`slope_pct`) | **Q5 > Q1** — OBV naik = akumulasi. |
| 3a | `stochrsi_k` (harian) | Stoch RSI %K bar terakhir (0–100) | **Q1 > Q5** — masuk saat oversold, bukan overbought. |
| 3b | `stochrsi_htf` (mingguan) | Stoch RSI %K mingguan | **Q1 > Q5**, tapi lebih lemah dari 3a. |
| 4 | `fib_retr` | retracement harga thd impuls naik terakhir (0–1+) | **non-monoton** — golden zone (~0.5–0.618) terbaik, dangkal (Q1) & sangat dalam (Q5) buruk. Kalau harus satu arah: **Q1 > Q5** (retr dangkal = tren kuat). |
| 5 | `dist_to_resistance_pct` | (resistance terdekat − harga)/harga × 100 | **Q5 > Q1** — lebih banyak ruang ke atas = target lebih realistis. |
| 6 | `dist_ema50_pct` | (harga − EMA50)/EMA50 × 100 | **Q1 > Q5** — dekat/di bawah EMA50 = pullback sehat; jauh di atas = kepanjangan. |
| 7 | `atr_pct` | ATR(14)/harga × 100 | **Q1 > Q5** — volatilitas rendah = struktur lebih bersih. Dugaan lemah. |
| 8 | `rs_btc_30d` | (return koin 30 bar) − (return BTC 30 bar), poin persentase | **Q5 > Q1** secara konvensional (pemimpin menang), **TAPI temuan 3d (regime BTC anti-prediktif) memberi alasan kuat menduga arah ini terbalik atau nol.** Faktor prioritas. |
| 9 | `dd_from_1y_high_pct` | (harga − high tertinggi 252 bar)/high × 100 (≤ 0) | **Q5 > Q1** — dekat high 1 tahun = uptrend utuh. Alternatif kontrarian: drawdown dalam = mean-reversion (Q1 > Q5). |

## Koreksi v2 — uji within-symbol (demeaned per koin)

Ditambahkan SEBELUM melihat hasil v2 (hasil v1 mentah belum pernah dilihat penuh —
run v1 dibatalkan di tengah, hanya konteks buy-and-hold yang sudah dilihat:
median koin −83%, 9% naik).

**Masalah:** 91% koin di universe ini rugi kalau di-hold. Faktor yang berkorelasi
dengan KUALITAS/UKURAN koin akan lolos uji kuintil mentah tanpa punya kemampuan
memilih MOMEN. Screener memilih *kapan* masuk, bukan *koin apa*.

**Koreksi:** untuk tiap koin hitung rata-rata `pnl_r` koin itu, lalu
`pnl_r_demeaned = pnl_r − rata_rata_pnl_r_koin`. Ulangi seluruh uji kuintil 9
faktor memakai `pnl_r_demeaned`. Ini membuang efek antar-koin, menyisakan
pertanyaan sebenarnya: **di dalam koin yang sama, apakah faktor membedakan momen
baik dari momen buruk?**

Laporkan berdampingan per faktor: Spearman mentah, Spearman demeaned, selisih.
**Verdict 4-kriteria dinilai pada versi DEMEANED.**

Tafsiran:
- mentah kuat, demeaned nol → faktor cuma proksi kualitas koin, tak berguna untuk timing
- mentah nol, demeaned kuat → sinyal timing yang tertutup derau antar-koin (temuan paling berharga)
- keduanya nol → faktor memang tidak punya sinyal

Tambahan: ANOVA satu arah — berapa % varians `pnl_r` dijelaskan identitas koin saja
(eta-squared). Angka besar = pemilihan koin ≫ pemilihan waktu (implikasi strategis).

## Validasi holdout (v3 — ditambahkan sebelum melihat hasil faktor apa pun)

Kalau ada faktor lolos 4 kriteria di **discovery**, ia TIDAK langsung dipakai /
dimasukkan ke `scoring.py`. Validasi dulu:

1. **70% simbol** = discovery, **30% simbol** = holdout. Split per SIMBOL (bukan
   periode), seed tetap `20260904` — supaya tidak ada kebocoran lewat korelasi
   antar-koin di waktu yang sama.
2. Discovery: cari faktor yang lolos 4 kriteria (versi demeaned).
3. Holdout: uji faktor itu **sekali saja**. Ambang sama (n≥200/kuintil karena
   holdout lebih kecil). Arah (tanda Spearman demeaned) harus sama dengan discovery.
4. Lolos discovery TAPI gugur di holdout → **faktor gugur, selesai**. Tidak diuji
   ulang dengan parameter lain di holdout yang sama.
5. Tidak ada yang lolos discovery → holdout **tidak disentuh** (tetap murni).

## Aturan

- Tidak menggabungkan faktor. Satu per satu.
- `scoring.py` / `indicators.py` / `accumulation.py` TIDAK diubah. Murni pengukuran.
- Kalau **tidak ada** faktor yang lolos 4 kriteria: kesimpulannya "tidak ada
  sinyal di sembilan faktor" — itu hasil yang sah dan berguna, dilaporkan tanpa
  dihaluskan.
