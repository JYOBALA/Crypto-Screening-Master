# Ringkasan Akhir — Validasi Sistem Skor Swing Screener

*2026-09-04. Ditutup setelah rangkaian uji backtest, faktor tunggal, dan mekanik.*

---

## 1. Apa yang diuji

Semua uji: walk-forward, **tanpa lookahead**, biaya fee 0.1% + slippage 0.05% per
sisi, SL menang kalau satu bar menyentuh SL & TP sekaligus. `scoring.py`,
`indicators.py`, `accumulation.py` **tidak** diubah — semua murni pengukuran.

| Uji | Isi | Data |
|---|---|---|
| **Backtest komposit** (`backtest.py`) | Skor 100 poin (Volume 25 / StochRSI 20 / Fib 20 / S/R 20 / Pattern 15) + veto + rencana trade, dijalankan tiap bar | 26 pair (v1) lalu **422 pair USDT, 2021–2026** (v2, `.cache_history/`) |
| **Uji faktor tunggal** (`factor_test.py`) | 9 faktor mentah diuji sendiri-sendiri per kuintil, dengan koreksi within-symbol (demeaned per koin) + ANOVA identitas koin + **validasi holdout 30% simbol** (seed tetap, sekali jalan) | 13.020 sinyal non-veto, 364 koin |
| **Uji mekanik** (`mechanics_test.py`) | 6 varian manajemen posisi dengan **ENTRY ACAK** (seleksi dinetralkan total), set entry sama untuk semua varian | 8.000 entry acak, 343 koin |
| **Uji regime — Tahap 1** (`regime_test.py`) | 5 detektor regime (SMA200 / EMA50 / breadth / dominasi-proksi / BTC 90d), walk-forward; ukur return 30-hari-ke-depan universe saat BULL vs BEAR | ~130 minggu evaluasi, 422 koin |
| **Konteks** | Return buy-and-hold per koin | 381 koin, dari bar warm-up ke-250 sampai akhir data |

Faktor yang diuji: `vol_ratio`, `obv_slope`, `stochrsi_k`, `stochrsi_htf`,
`fib_retr`, `dist_to_res_pct`, `dist_ema50_pct`, `atr_pct`, `rs_btc_30d`,
`dd_from_1y_high_pct`.
Varian mekanik: A baseline (SL struktur, TP1 2R 50% + BE, TP2 4R, timeout 30) ·
B tanpa BE · C SL 3× ATR · D trailing chandelier 3× ATR · E timeout 90 ·
F full exit di TP1.

---

## 2. Apa yang ditemukan

### Lahan
Median koin di universe ini **−83,4%** sejak 2021 (rata-rata −63,8%, hanya **9%**
koin yang naik, persentil-75 tetap −53%). Setiap sistem long-only di sini
menghadapi tekanan luar biasa.

### Backtest komposit (422 pair)
- **Total P&L −120 R dari 10.682 trade.** Rugi.
- Korelasi skor total ↔ hasil: Pearson −0,01, Spearman −0,13. Kelima komponen
  |r| < 0,02. Pita skor **tidak monoton**.
- Skor ≥70 (yang benar-benar user ambil): 206 trade, E[R] +0,165 R — tapi buang
  **3 winner teratas → −0,007 R**. Edge = 3 trade dari 206. Per tahun tidak stabil.
- Skor maksimum yang pernah dicapai = **85**; grade A+ (≥80) cuma 16 trade seumur
  data. Skala 100 poin efektif mentok di ~65 (persentil-95 = 64) → **masalah desain**.
- Regime BTC **anti-prediktif**: trade saat HIJAU E[R] −0,10, saat KUNING +0,03.
- Counterfactual veto MERAH: E[R] ~0 (median rugi penuh) — veto tidak jelas
  menolong maupun merugikan.

### Uji faktor tunggal
- **ANOVA:** identitas koin menjelaskan hanya **2,3%** varians `pnl_r` (F=0,8).
  Return hold jangka panjang koin ≠ hasil trade jangka pendek.
- **Discovery:** hanya `dist_to_res_pct` lolos 4 kriteria (Spearman demeaned +0,101).
  `fib_retr` & `atr_pct` = proksi kualitas koin (Spearman mentah ±0,16 hilang jadi
  ~0 setelah demeaned — lolos palsu kalau tanpa koreksi ini).
- `rs_btc_30d`: arahnya **terbalik** dari dugaan (Q5, koin yang paling mengungguli
  BTC, justru terburuk −0,17 R) — konsisten dengan regime BTC anti-prediktif — tapi
  demeaned ±0,015, jadi bukan sinyal timing.
- **Holdout:** `dist_to_res_pct` **GUGUR** — arah kebalikan discovery, tidak monoton,
  tidak stabil antar-periode. Tidak diuji ulang (holdout seed 20260904 terpakai).
- **Tidak ada faktor tunggal dengan sinyal timing yang bertahan out-of-sample.**

### Uji mekanik (entry acak, n=8.000)

| Var | win | E[R] | PF | E[R] tanpa 5 terbaik | 95% CI E[R] |
|---|---:|---:|---:|---:|---|
| A baseline | 33,6% | **−0,082** | 0,87 | −0,084 | −0,112 .. −0,051 |
| B tanpa BE | 33,6% | −0,085 | 0,87 | −0,087 | −0,117 .. −0,054 |
| C SL 3×ATR | 35,7% | −0,055 | 0,88 | −0,057 | −0,079 .. −0,031 |
| D trailing | 34,8% | −0,095 | 0,74 | −0,109 | −0,118 .. −0,072 |
| E timeout 90 | 32,6% | −0,091 | 0,87 | −0,093 | −0,123 .. −0,059 |
| F full exit TP1 | 33,6% | −0,065 | 0,90 | −0,066 | −0,094 .. −0,035 |

- **Keenam varian negatif.** Selang kepercayaan 95% semuanya **seluruhnya di bawah
  nol**.
- E[R] tanpa 5 trade terbaik ≈ E[R] penuh → di n=8.000 ini **bukan** efek ekor,
  ini negatif struktural.
- Semua varian positif hanya di **2023** (+0,19). 2022, 2025, 2026 negatif tajam
  (2025 ≈ −0,25). "Edge" tahun 2023 adalah edge *regime*, bukan edge mekanik.

### Jawaban pertanyaan utama
**Tidak ada mekanik yang menghasilkan ekspektasi positif dengan entry acak.**
Masalahnya bukan "seleksi vs. manajemen posisi" — **pendekatan long-only pada
universe & timeframe ini tidak menghasilkan edge dalam konfigurasi apa pun yang
diuji.**

### Uji regime long/short — Tahap 1 (prasyarat, `HASIL_REGIME.md`)
Menguji apakah ada label regime yang memprediksi return 30-hari-ke-depan universe.
5 detektor, semua walk-forward. **Tidak ada yang lulus** (selisih BULL−BEAR ≥ 5pp
+ CI tak lewati nol + arah stabil). Selisih terbesar R2/EMA50 = +2,8 pp, CI
[−2,5, +8,1]. R3/R4/R5 (breadth, dominasi, momentum BTC) **anti-prediktif** —
"BULL" justru punya return ke depan lebih buruk. Universe bleeds di hampir semua
regime (return30 −1% s/d −4%). **Tahap 2 (S1–S4 long/short + short perp/funding)
tidak dijalankan** — tidak ada detektor terbukti untuk membangunnya.

Pengembangan sistem skor ditutup di sini.

### Bug yang ditemukan sepanjang proses
| Bug | Perbaikan |
|---|---|
| TP2 ≤ TP1 di 23 sinyal (`build_trade_plan`) | Kumpulkan level di atas entry, urut, TP1 terdekat / TP2 berikutnya; urutan dijamin |
| Regime BTC basi di backtest — `regime_at` bandingkan epoch ms vs ns → selalu ambil bar terakhir. **Diperkenalkan oleh "perbaikan" UserWarning Claude sendiri.** | `DatetimeIndex.searchsorted` + sanity-check sebaran regime |
| `max_plausible_rr` diturunkan 15→8 atas dasar backtest v1 yang ternyata artefak sampel kecil | Dikembalikan ke 15 (sanity-check geometri, bukan filter kinerja) |

---

## 3. Apa yang TIDAK boleh disimpulkan dari data ini

1. **BUKAN "swing trading crypto tidak mungkin."** Yang diuji: satu exchange
   (Binance USDT spot), satu timeframe (harian), satu arah (**long-only**), satu
   jendela 2021–2026 yang didominasi bear altcoin brutal (2022) dan penurunan
   2025–2026. TF lebih tinggi/rendah, short, spread/pair, atau periode lain tidak
   diuji dan bisa berbeda.

2. **BUKAN "indikator ini (volume, StochRSI, Fibonacci, OBV, ATR, dst.) tidak
   berguna."** Yang diuji: indikator-indikator itu **sebagai komponen formula skor
   spesifik ini**, dan sebagai **faktor tunggal pada signal set ini dengan mekanik
   exit ini**. Kombinasi, pembobotan, atau konteks lain tidak terbantahkan.

3. **BUKAN "buy & hold lebih baik."** Hold median koin −83%. Tidak ada di sini yang
   menyarankan hold sebagai alternatif.

4. **BUKAN "alat ukurnya bias/bug sehingga hasilnya jelek."** Alat diaudit; satu
   bug nyata ditemukan & diperbaiki (regime BTC), dan hasil di bawah adalah
   **setelah** perbaikan itu. Baseline entry-acak menyilang-validasi backtest
   komposit — keduanya menunjuk arah yang sama.

5. **BUKAN "`dist_to_res_pct` terbukti tidak berguna di mana pun."** Ia gugur di
   **satu** split holdout (seed 20260904). Split/periode lain bisa berbeda — tapi
   sesuai protokol pra-registrasi, sekali holdout dipakai, pengujian ulang di
   holdout yang sama dilarang. Untuk menindaklanjuti butuh data baru.

6. **BUKAN klaim signifikansi di luar yang ditopang n.** Agregat besar (8k–13k
   trade) kuat, tapi pecahan per-tahun jauh lebih kecil; positifnya 2023 bisa
   keberuntungan regime. Jangan ekstrapolasi satu tahun bagus.

7. **Survivorship belum sepenuhnya dikoreksi.** `.cache_history/` berisi pair yang
   **masih listing** di Binance hari ini. Koin yang sudah delisting (kemungkinan
   yang terburuk) tidak bisa ditarik dari API — hasil di sini kemungkinan masih
   **lebih optimis** dari kenyataan penuh.

8. **BUKAN "screener-nya tidak boleh dipakai."** Screener tetap alat bantu SOP
   manual read-only. Kesimpulannya lebih sempit: **skornya tidak memberi edge
   terukur, jadi jangan perlakukan angka skor sebagai sinyal prediktif** —
   perlakukan sebagai checklist yang tetap butuh verifikasi chart & penilaian
   diskresioner user.

9. **BUKAN "regime tidak bisa dideteksi, titik."** 5 detektor spesifik gagal di
   universe/periode ini dengan ambang yang ditetapkan sebelumnya. R4 (dominasi)
   memakai **proksi harga**, bukan dominasi mcap sebenarnya. Detektor lain, TF
   lain, atau definisi regime lain tidak diuji.

10. **Sisi SHORT tidak pernah diuji.** Tahap 2 (long/short, short-only) tidak
    dijalankan karena Tahap 1 gagal. Semua kesimpulan di sini adalah tentang
    **long-only**. Apakah short atau market-neutral punya edge di universe ini —
    tidak diketahui dari data ini.

---

## Artefak

- `backtest.py`, `factor_test.py`, `mechanics_test.py`, `regime_test.py` — alat ukur (read-only).
- `fetch_history.py` — pengisi `.cache_history/` (422 pair, 2021–2026).
- `HIPOTESIS_FAKTOR.md`, `HIPOTESIS_REGIME.md` — protokol pra-registrasi.
- `FACTOR_TEST_HASIL.md`, `HASIL_REGIME.md` — hasil lengkap.
- CSV (di-`.gitignore`, dikirim ke user): `backtest_v2_422pair.csv`,
  `factor_test_signals_v3.csv`, `mechanics_test_trades.csv`.
