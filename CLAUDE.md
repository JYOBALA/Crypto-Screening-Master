# Konteks Proyek — Crypto Swing Screener

## ATURAN KERJA (baca ini dulu)

1. **Setelah SETIAP perubahan kode, jalankan `python verify.py`.** Cek sintaks + impor +
   ketahanan encoding + selftest. Jangan pernah menyatakan sebuah perubahan selesai
   sebelum ini lulus. Kalau `verify.py` tidak ada, JANGAN diam-diam menggantinya dengan
   perintah lain — bilang ke user bahwa filenya hilang.
2. **Commit sebelum mengubah apa pun**: `git add -A && git commit -m "sebelum: <rencana>"`.
   Kalau hasilnya lebih buruk, user bisa `git checkout .` untuk kembali.
3. **Jangan pernah menambah fungsi order/trading.** Proyek ini sengaja read-only.
   Tidak ada API key, tidak ada penempatan order, selamanya.
4. **Jangan menyetel ambang batas agar "ada hasil yang keluar".** Hasil kosong sering kali
   jawaban yang benar. Longgarkan angka hanya kalau ada alasan metodologis, bukan karena
   user ingin melihat lebih banyak ticker.
5. **Kalau menemukan bug, laporkan apa adanya** — termasuk bug yang berasal dari saran
   Claude sebelumnya. Riwayat proyek ini sudah membuktikan bug memang ada.
6. **Bahasa Indonesia** untuk semua komunikasi, komentar kode, dan output.

## Perintah harian user

```bash
python screener.py --mode daily --csv     # swing harian, tiap pagi
python screener.py --mode weekly --csv    # swing mingguan, Senin
python screener.py --mode gem --csv       # deteksi akumulasi
python inspect_symbol.py NEARUSDT         # bedah satu ticker
python diagnose.py                        # cek sebaran volume universe
python verify.py                          # gerbang mutu setelah edit kode

python fetch_history.py --start 2021-01-01   # isi .cache_history/ (sekali, untuk backtest)
python backtest.py --history                 # backtest walk-forward di arsip panjang
python fetch_orderflow.py --universe u2      # isi .cache_orderflow/ (kolom qav/trades/tbbav/tbqav)
python journal.py new SYMBOL                 # catat kondisi objektif + tesis sebelum entry
python journal.py close ID --exit-price X --exit-reason SL   # catat exit aktual
python journal.py review                     # kalibrasi keyakinan vs hasil (butuh >=50 trade utk kesimpulan)
python daily_run.py                          # cron harian: screener 15 koin beku (long+short) + notifikasi Telegram + ide_trade.xlsx
```


Screener swing trade crypto berbasis SOP manual. Output berupa daftar ticker untuk dieksekusi manual oleh user. **Read-only, tidak pernah melakukan order.**

## Arsitektur

| File | Isi |
|---|---|
| `screener.py` | CLI, fetch Binance public API, filter universe (Tahap 0), orkestrasi, output tabel + ekspor CSV/JSON |
| `scoring.py` | Sistem skor 100 poin, veto rules, deteksi regime BTC, perhitungan entry/SL/TP/size |
| `indicators.py` | Indikator murni pandas/numpy: RSI, Stoch RSI, OBV, ATR, pivot fractal, Fibonacci, zona S/R, chart pattern |
| `accumulation.py` | Mode GEM: deteksi trading range, event Wyckoff (SC/AR/ST/Spring/Test/SOS/LPS/UTAD), VCP, Bollinger BandWidth percentile, ADL, RS vs BTC |
| `inspect_symbol.py` | Bedah detail satu ticker (mendukung ketiga mode) |
| `diagnose.py` | Diagnostik sebaran volume universe |
| `fetch_history.py` | Unduh sejarah harian panjang (paginasi `startTime`) untuk SEMUA pair USDT TRADING → `.cache_history/`. Koreksi survivorship + mencakup bear 2022 |
| `backtest.py` | Walk-forward `evaluate()` tanpa lookahead. `--history` pakai `.cache_history/`. Ukur monotonisitas pita skor, korelasi komponen, ketahanan (fat tail), pita R:R, split bull/bear, counterfactual veto MERAH |
| `factor_test.py` | Uji 9 faktor mentah per kuintil (bukan komposit): demeaned per koin, ANOVA identitas koin, validasi holdout 30% simbol |
| `mechanics_test.py` | Uji 6 varian mekanik trade dengan ENTRY ACAK (seleksi dinetralkan) |
| `regime_test.py` | Tahap 1 uji regime: 5 detektor walk-forward, return 30-hari-ke-depan universe saat BULL vs BEAR |
| `fetch_orderflow.py` | Unduh sejarah harian TERMASUK kolom aliran order (qav/trades/tbbav/tbqav) yang dibuang `fetch_klines()`/`fetch_history.py` → `.cache_orderflow/`. Universe U1/U2 dibekukan sekali (`universe_<u>.json`) |
| `orderflow_test.py` | Uji cross-sectional (BUKAN simulasi trade): IC Spearman harian, 5 fitur order-flow × 3 horizon × 2 universe, holdout 30% simbol |
| `journal.py` | Jurnal trade sebagai instrumen riset: tangkap kondisi objektif `evaluate()` + tesis/keyakinan/keputusan user, append-only + hash SHA256 per record → `journal.jsonl` (gitignored). `compute_review()` = satu sumber kebenaran statistik, dipakai `journal.py review`. TIDAK pernah menyarankan ambil/lewati |
| `daily_run.py` | Cron harian (bukan proses menetap): screener LONG+SHORT utk 15 simbol `universe_frozen.json`, sisipkan baris hari ini di paling ATAS `ide_trade.xlsx` (gitignored). Guard idempoten (cek baris tanggal hari ini, skip kecuali `--force`), validasi bahasa (`_check_forbidden`) sebelum kirim Telegram, notifikasi ERROR kalau jaringan gagal (tidak diam). Kalau `ide_trade.xlsx` terkunci (mis. sedang dibuka di Excel): fallback ke `data/ide_trade_YYYY-MM-DD.csv`, tidak pernah kehilangan data hari itu |
| `short_scan.py` | Kandidat SHORT — cermin bobot 25/20/20/20/15 punya long, **BELUM PERNAH DIUJI** (beda dari long yang 5x null). Cek ketersediaan perp (`/fapi/v1/exchangeInfo`, host beda dari spot — sering ikut diblokir ISP, gagal aman ke "tidak ada perp"), funding rate INFORMASI saja (bukan skor) |
| `universe_frozen.json` | 15 simbol dibekukan 2026-09-05 (`KRITERIA_EVALUASI.md`) — **jangan diedit** sampai trade ke-50. `daily_run.py` baca file ini, TIDAK fetch universe dari volume hari ini |

**Tidak ada dashboard/web UI** — `dashboard.html` dicabut (2026-09-05).
Output harian dibaca langsung dari `ide_trade.xlsx` (Excel/LibreOffice) atau
Telegram; `journal.py review` tetap jalan di terminal. Jangan menambahkan
dashboard lagi kecuali diminta user secara eksplisit.

`KRITERIA_EVALUASI.md` — pra-registrasi eksperimen trading manual (universe
15 koin dibekukan 2026-09-05, kriteria LANJUT/BERHENTI di trade ke-50,
DITULIS SEBELUM trade pertama). **Jangan diedit setelah trade dimulai.**
Kalau ada klaim statistik baru yang mau ditambahkan ke sana atau ke sini,
verifikasi dulu terhadap data mentah — dua klaim di draft awal dokumen itu
("−40% koin besar vs −83%", "2025 −0,13R/2026 −0,15R") ternyata tidak bisa
direproduksi/tidak cocok dengan data, dan harus dikoreksi sebelum commit.

**Deploy VPS:** `DEPLOY.md` — setup cron `daily_run.py` + bot Telegram.
`.env` (token bot), `journal.jsonl`, dan `ide_trade.xlsx` tidak pernah
di-commit (data operasional/pribadi, bukan kode).

Alur: `fetch_universe` → `btc_regime` (gate) → per simbol: `fetch_for_mode` → `evaluate` → skoring 5 komponen → veto check → `build_trade_plan` → ranking.

## Sistem skor (jangan diubah tanpa diminta user)

| Komponen | Bobot | Fungsi |
|---|---|---|
| Volume | 25 | `score_volume()` |
| Stoch RSI | 20 | `score_stochrsi()` |
| Fibonacci | 20 | `score_fibonacci()` |
| Support & Resistance | 20 | `score_sr()` |
| Chart Pattern | 15 | `score_pattern()` |

Grade: A+ ≥80 · A 70–79 · B 60–69 · C <60

**Veto rules** (batal otomatis berapapun skornya): BTC status MERAH · ada komponen bernilai 0 · retracement > 0.786 · Stoch RSI daily > 80 · R:R ke TP1 < minimum · **R:R ke TP1 > `max_plausible_rr` (15.0)**.

`max_plausible_rr` = **15.0** — sanity-check geometri (menangkap swing basi yang
menghasilkan R:R 1:66), **BUKAN filter kinerja**. Sempat diturunkan ke 8 lalu
dikembalikan; lihat "Pelajaran: pembalikan max_plausible_rr" di bawah.

## Mode GEM — sistem skor terpisah (accumulation.py)

| Komponen | Bobot | Fungsi |
|---|---|---|
| Struktur Basis (Weinstein Stage 1) | 20 | `score_base()` |
| Kontraksi Volatilitas (VCP + BB squeeze) | 20 | `score_contraction()` |
| Event Wyckoff | 25 | `score_wyckoff()` |
| Smart Money (OBV/ADL/up-down vol) | 20 | `score_smart_money()` |
| RS vs BTC | 15 | `score_relative_strength()` |

Veto gem: UTAD terdeteksi (distribusi) · tidak ada basis · sudah naik >60% dalam 30 hari · R:R < 1:2 · BTC MERAH.

**Empat aturan yang JANGAN dilanggar saat memodifikasi accumulation.py:**
1. Support referensi untuk deteksi spring diambil dari **60% awal basis**, bukan dasar seluruh range — kalau tidak, spring tidak akan pernah terdeteksi karena ia sendiri yang membentuk dasar range.
2. Jendela pencarian Selling Climax **diperluas 40 bar ke belakang** dari awal range, karena SC sering terjadi tepat sebelum range terbentuk.
3. Stop loss mengikuti gaya entry: Phase C → di bawah low spring; Phase D → di bawah LPS/low 10 bar (bukan spring, itu terlalu lebar); breakout → di bawah low kontraksi terakhir (aturan VCP).
4. Spring bervolume **rendah** harus diberi skor lebih tinggi daripada spring bervolume tinggi.

## Aturan penting saat memodifikasi

1. **Selalu jalankan `python3 screener.py --selftest` setelah mengubah `scoring.py`, `indicators.py`, atau `accumulation.py`.** Selftest mencakup skema Wyckoff sintetis dan memverifikasi bahwa Phase C/D terdeteksi benar serta skor Wyckoff mengalahkan data trending acak. Selftest berjalan tanpa internet dan memvalidasi konsistensi skor, rentang nilai, dan validitas rencana trade (SL < entry < TP1 < TP2).
2. **Selalu buang candle berjalan.** `fetch_klines` sudah melakukan `df.iloc[:-1]`. Analisa hanya pada candle yang sudah close — ini aturan inti SOP.
3. **Stoch RSI hanya untuk timing, bukan penentu arah.** Jangan pernah menambah logika yang memberi skor tinggi hanya karena oversold tanpa konteks struktur.
4. **Jangan tambahkan fungsi order/trading.** Proyek ini sengaja read-only.
5. Hindari dependensi baru. pandas, numpy, requests, pyarrow, openpyxl
   (`ide_trade.xlsx`, ditambah 2026-09-05 atas permintaan eksplisit user).

## Permintaan lanjutan yang mungkin muncul

- **Backtest** — buat `backtest.py`: jalankan `evaluate()` pada setiap bar historis (walk-forward, tanpa lookahead), simulasikan entry/SL/TP, hitung win rate & ekspektasi per komponen skor.
- **Notifikasi** — kirim hasil ke Telegram bot atau Discord webhook setelah screening.
- **Tuning bobot** — analisa CSV hasil trade user, korelasikan skor komponen dengan hasil, usulkan bobot baru.
- **Exchange lain** — abstraksi lewat `ccxt`, tapi jaga agar `evaluate()` tetap exchange-agnostic.
- **Filter market cap / unlock token** — butuh sumber data eksternal (CoinGecko API gratis).

## Konteks user

User trading manual, tidak mau eksekusi otomatis. Yang dibutuhkan: daftar ticker + rencana trade yang jelas, plus alasan skor supaya bisa diverifikasi sendiri di chart. Komunikasi dalam Bahasa Indonesia.

## Bug yang pernah ditemukan (jangan diulang)

Riwayat ini penting — semuanya lolos dari selftest dan baru ketahuan dari data nyata:

| Bug | Gejala | Perbaikan |
|---|---|---|
| Swing high basi | `fib_retr` NEGATIF, R:R absurd (1:66) | `last_impulse_swing()` merentangkan swing high ke high tertinggi sejak pivot |
| Tidak ada batas atas R:R | R:R 1:66 lolos filter | Veto `max_plausible_rr` = 15 (sempat 8, dikembalikan — lihat "Pelajaran" di bawah) |
| Regime BTC basi di backtest | Veto MERAH tak pernah aktif; split bull/bear ngawur | `regime_at` bandingkan epoch ms vs ns → selalu ambil bar terakhir. Diperkenalkan oleh "perbaikan" UserWarning Claude sendiri. Fix: `DatetimeIndex.searchsorted` + sanity-check sebaran regime |
| TP2 ≤ TP1 di rencana | 23 sinyal (mis. NEARUSDT: tp1 1.4405, tp2 1.341) | `build_trade_plan()`: kumpulkan semua level di atas entry (resistance/fib ext/target pola), urut, TP1 = terdekat, TP2 = berikutnya. Urutan TP2 > TP1 dijamin. Akar: fib ext_1.618 di-anchor ke impuls terakhir; kalau impuls kecil, ia mendarat di bawah resistance historis di atasnya |
| Support referensi spring | Spring tidak pernah terdeteksi | Referensi dari 60% awal basis, bukan dasar seluruh range |
| Jendela SC terlalu sempit | Selling Climax terlewat | Diperluas 40 bar ke belakang dari awal range |
| Stop loss Phase D | Risiko konyol lebar | Phase C → bawah spring; Phase D → bawah LPS; breakout → bawah kontraksi terakhir |
| Entry vs pola bertentangan | Pola bilang "tunggu breakout 48.5", rencana bilang "limit 37.51" | `build_trade_plan()` kini membaca `pattern_obj` |
| Label kesiapan | `[SIAP]` untuk koin berskor 53 | `_readiness()` memperhitungkan skor + veto |
| VCP palsu | "9 kontraksi menyempit" | >6 leg = chop, bukan VCP (Minervini: 2-6) |
| A/D nol dihitung naik | "A/D Line naik (+0.00)" dapat 4 poin | Ambang 0.05 |
| Skala StochRSI biner | 19 dari 23 koin dapat nilai 3 yang sama | Ditambah tingkat 5 dan 7 |
| UnicodeEncodeError di Windows | Crash saat output di-pipe/ditangkap (cp1252) walau normal di terminal langsung | `_force_utf8()` di tiap entry script + cek regresi di `verify.py` |

## Pelajaran: pembalikan max_plausible_rr

Backtest v1 (357 trade, 26 pair) menunjukkan pita R:R rencana 0-3 unggul jauh
(+0.326R) atas pita 5-8 (+0.025R). Atas dasar itu ambang diturunkan 15 -> 8.
Backtest v2 (422 pair, 507k bar) membalikkannya: 5-8 = +0.08R, 0-3 = -0.04R.

Kesimpulan yang benar BUKAN "ternyata pita 5-8 yang unggul" — kedua angka itu
praktis nol. Yang benar: R:R rencana tidak berpengaruh terhadap hasil, dan
temuan v1 adalah artefak sampel kecil berekor gemuk.

ATURAN: max_plausible_rr adalah pemeriksa kewarasan GEOMETRI (menangkap swing
basi yang menghasilkan R:R 1:66), bukan filter kinerja. Jangan setel ulang
berdasarkan hasil backtest.

ATURAN UMUM: jangan mengubah parameter berdasarkan perbedaan ekspektasi
di bawah ~0.1R atau sampel di bawah 100 per kelompok. Perbedaan sekecil itu
tidak bertahan.

## Catatan lingkungan Windows

Output memakai karakter Unicode (✓ ═ ▶ █). Di Windows, Python memilih encoding
berdasarkan tujuan output: console langsung bisa UTF-8, tapi saat output **di-pipe atau
ditangkap program lain** (termasuk Claude Code) Python jatuh ke cp1252 dan crash.
Karena itu tiap entry script memanggil `_force_utf8()` di awal. Jangan hapus fungsi ini,
dan jangan "memperbaiki" gejalanya dengan `chcp 65001` atau `PYTHONIOENCODING` manual —
perbaikannya sudah ada di dalam kode.

## Endpoint Binance & pemblokiran ISP (per 2026-09-05)

`api.binance.com` **diblokir DNS oleh ISP di Indonesia** — resolusinya diarahkan ke
server pemblokir lokal (IP bukan Binance), yang menyajikan sertifikat TLS
kedaluwarsa sehingga koneksi gagal dengan `SSLError`, bukan gagal bersih. Karena itu:

- **`data-api.binance.vision` adalah endpoint DEFAULT** (urutan pertama di
  `BASE_CANDIDATES`, `screener.py`), bukan `api.binance.com`. Endpoint ini adalah
  host data publik resmi Binance, tidak diblokir, dan tidak butuh env var apa pun.
  Override manual masih bisa lewat `export BINANCE_BASE=...` kalau suatu saat perlu.
- **Fallback endpoint HARUS menangani exception koneksi (`SSLError`,
  `ConnectionError`, `Timeout`), bukan cuma status HTTP 403/451.** Pemblokiran
  DNS/TLS datang sebagai exception di level `requests`, bukan respons HTTP —
  kalau `_get()` cuma memeriksa status code, ia akan retry 3x ke endpoint yang
  SAMA lalu menyerah, padahal endpoint lain di `BASE_CANDIDATES` sehat. `_get()`
  di `screener.py` sudah memperbaiki ini: exception koneksi memicu `_switch_base()`
  duluan, baru dihitung sebagai retry kalau tidak ada endpoint lain tersisa.
- **Setiap script baru yang memanggil Binance API wajib memakai `BASE_CANDIDATES` +
  `_switch_base()` + `_force_utf8()` dari `screener.py` (lewat `import screener as scr`
  — `_force_utf8()` otomatis berjalan sebagai efek samping import) — JANGAN menulis
  ulang endpoint/fallback/encoding sendiri.** Pola ini sudah menimbulkan bug 3 kali
  secara terpisah: encoding cp1252 (lihat di atas), endpoint fallback yang cuma
  cek status HTTP, dan penanganan exception TLS. Kalau tiga hal dasar ini sudah
  bermasalah tiga kali, jangan diulang keempat kalinya dengan menulis versi baru
  di script lain.

## Status backtest (per 2026-09-03)

`backtest.py` + `fetch_history.py` dibuat. Backtest terakhir: **422 pair USDT,
2021–2026** (`.cache_history/`, `python backtest.py --history`).

Hasil (setelah perbaikan bug regime BTC, commit c631fda):
- **Sistem TIDAK prediktif.** Korelasi skor total ↔ hasil: Pearson −0.01, Spearman
  −0.13. Kelima komponen |r| < 0.02. Pita skor TIDAK monoton.
- **Total P&L −120 R dari 10.682 trade.** Rugi. Tanpa 5 winner teratas: jauh lebih rugi.
- Skor ≥70: 206 trade, E[R] +0.165 R — TAPI buang 3 winner teratas → −0.007 R.
  "Edge" = 3 trade dari 206. Per-tahun tidak stabil (2025 −0.52 R). **Tidak lolos
  sebagai prediktif.**
- Skor maksimum 85; grade A+ (≥80) cuma 16 trade seumur data. Skala efektif mentok ~65.
- **Regime BTC anti-prediktif:** trade saat HIJAU E[R] −0.10, saat KUNING +0.03.
  Kebalikan dari desain. Counterfactual: setup yang diblokir veto MERAH E[R] ~0
  (median rugi penuh) — veto tidak jelas menolong maupun merugikan.

**JANGAN sentuh bobot 25/20/20/20/15 atau ambang.** Data sudah cukup dan jawabannya:
sistem belum menghasilkan edge yang bisa diukur. Menyetel bobot di atas data yang
korelasinya nol = overfitting.

### Uji faktor tunggal (per 2026-09-04) — `factor_test.py`, `FACTOR_TEST_HASIL.md`

Skor komposit final gagal → 9 faktor mentah diuji sendiri-sendiri per kuintil
(13.020 sinyal), dengan koreksi within-symbol (demeaned per koin) + validasi
holdout 30% simbol (seed tetap, sekali jalan).

- Buy & hold: median koin **−83%**, 9% naik. Lahan sangat buruk.
- ANOVA: identitas koin jelaskan **2,3%** varians pnl_r (F=0,8) — pemilihan koin
  TIDAK mengalahkan pemilihan waktu di sini (return hold ≠ hasil trade pendek).
- Discovery: hanya `dist_to_res_pct` lolos. `fib_retr` & `atr_pct` = proksi
  kualitas koin (Spearman mentah kuat, demeaned nol). `rs_btc_30d` arah terbalik
  (Q5 terburuk) — konsisten regime BTC anti-prediktif — tapi tak lolos.
- Holdout: `dist_to_res_pct` **GUGUR** (arah kebalikan discovery, tidak monoton,
  tidak stabil).
- **TIDAK ADA faktor tunggal dengan sinyal timing yang bertahan out-of-sample.**

**Holdout (seed 20260904) sudah dipakai untuk `dist_to_res_pct`.** Jangan uji
ulang faktor itu di holdout yang sama — pakai seed/split baru kalau perlu.

### Uji mekanik entry-acak (per 2026-09-04) — `mechanics_test.py`

Menetralkan seleksi: 8.000 entry ACAK, 6 varian mekanik (A baseline · B tanpa BE ·
C SL 3×ATR · D trailing · E timeout 90 · F full-exit-TP1).

- **Keenam varian E[R] negatif** (−0,055 s/d −0,095 R), 95% CI seluruhnya < 0.
- E[R] tanpa 5 trade terbaik ≈ E[R] penuh → negatif STRUKTURAL, bukan efek ekor.
- Positif hanya di 2023 (edge regime, bukan mekanik).
- **Tidak ada mekanik yang positif dengan entry acak.** Masalahnya bukan seleksi
  vs manajemen posisi — pendekatan long-only di universe/TF ini tidak punya edge.

### Uji regime long/short — Tahap 1 (per 2026-09-04) — `regime_test.py`, `HASIL_REGIME.md`

Prasyarat sebelum bangun long/short: apakah ada detektor regime yang memprediksi
return 30-hari-ke-depan universe? 5 detektor walk-forward (SMA200, EMA50, breadth,
dominasi-proksi, BTC 90d). **Tidak ada yang lulus** (selisih BULL−BEAR ≥5pp + CI
tak lewati nol + arah stabil). Terbaik R2/EMA50 = +2,8pp (CI [−2,5, +8,1]).
R3/R4/R5 anti-prediktif. **Tahap 2 (S1–S4 long/short + short perp/funding) tidak
dijalankan.** Sisi short tidak pernah diuji.

## Pengembangan sistem skor: DITUTUP (2026-09-04)

Rangkaian uji: backtest komposit → faktor tunggal → mekanik entry-acak → detektor
regime. Semua null. Kesimpulan lengkap + "apa yang TIDAK boleh disimpulkan":
**`RINGKASAN_AKHIR.md`**. Singkatnya: skor tidak memberi edge terukur; jangan
setel bobot; screener tetap checklist SOP manual, bukan sinyal prediktif.

## Uji nilai prediktif aliran order — TIDAK ADA sinyal (per 2026-09-05)

Proyek terpisah dari screener (`fetch_orderflow.py` + `orderflow_test.py`),
metodologi cross-sectional (IC Spearman harian vs return demeaned, BUKAN
simulasi trade). Pra-registrasi: `HIPOTESIS_ORDERFLOW.md`. Hasil lengkap:
`HASIL_ORDERFLOW.md`.

5 fitur (`taker_buy_ratio`, `avg_trade_size` persentil, `trade_intensity`,
`flow_divergence` terkondisi harga datar, `taker_buy_ratio_extreme` dua-arah)
× 3 horizon (5/10/20 hari) × 2 universe (U1 likuid ≥$5jt, U2 luas ≥$1jt) = 30
uji, ambang t-stat 3,5 (koreksi multiple testing), holdout 30% simbol seed
`20260905`. **0 dari 30 lulus.** 4 kombinasi (semua `taker_buy_ratio`/
`trade_intensity` di U1) lolos kriteria discovery tapi GAGAL replikasi di
holdout — `taker_buy_ratio` bahkan **berbalik arah total** di 47 simbol
holdout, contoh nyata kenapa holdout wajib sebelum klaim sinyal.

Temuan universe saat membangun U1/U2: filter `STABLES`/`EXCLUDE_TOKENS`
(`screener.py`) lolos-kan 22 simbol non-kripto (15 saham/ETF ter-tokenisasi
"xStocks" + 7 aset pegged emas/stablecoin baru) yang tidak ada saat filter
itu ditulis — dikeluarkan lewat daftar eksplisit di `fetch_orderflow.py`
(`EXCLUDE_TOKENIZED_EQUITY`/`EXCLUDE_PEGGED_EXTRA`) SEBELUM IC apa pun
dihitung. Kalau menambah universe baru dari Binance, cek ulang apakah ada
produk non-kripto baru yang lolos filter lama.

**JANGAN bangun fitur order-flow ke dalam `scoring.py`.** Proyek terpisah dari
validasi skor (`RINGKASAN_AKHIR.md` tetap khusus skor komposit, tidak diedit
untuk ini), tapi kesimpulannya senada: skor komposit, faktor tunggal, mekanik
entry-acak, detektor regime, DAN sekarang aliran order — lima kategori data
berbeda, semua null di universe/periode ini.

## Rencana berikutnya (kalau user meminta)

- **Selidiki skala skor**: kenapa maksimum ~85 dan pita ≥80 nyaris kosong. Komponen
  mana yang hampir tak pernah menyala penuh? Masalah desain, bukan tuning.
- **Pertimbangkan ulang peran regime BTC** — di data ini ia tidak menyeleksi periode
  yang lebih baik (bahkan HIJAU rugi; RS-vs-BTC arah terbalik).
- **Notifikasi Telegram/Discord** setelah screening selesai.
- **Filter market cap & token unlock** via CoinGecko API (masih dicek manual).
