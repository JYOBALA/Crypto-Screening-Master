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

**Veto rules** (batal otomatis berapapun skornya): BTC status MERAH · ada komponen bernilai 0 · retracement > 0.786 · Stoch RSI daily > 80 · R:R ke TP1 < minimum.

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

1. **Selalu jalankan `python3 screener.py --selftest` setelah mengubah `scoring.py`, `indicators.py`, atau `accumulation.py`.** Selftest mencakup skema Wyckoff sintetis dan memverifikasi bahwa Phase C/D terdeteksi benar serta skor Wyckoff mengalahkan data trending acak. Selftest berjalan tanpa internet dan memvalidasi konsistensi skor, rentang nilai, dan validitas rencana trade (SL < entry < TP1).
2. **Selalu buang candle berjalan.** `fetch_klines` sudah melakukan `df.iloc[:-1]`. Analisa hanya pada candle yang sudah close — ini aturan inti SOP.
3. **Stoch RSI hanya untuk timing, bukan penentu arah.** Jangan pernah menambah logika yang memberi skor tinggi hanya karena oversold tanpa konteks struktur.
4. **Jangan tambahkan fungsi order/trading.** Proyek ini sengaja read-only.
5. Hindari dependensi baru. Cukup pandas, numpy, requests, pyarrow.

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
| Tidak ada batas atas R:R | R:R 1:66 lolos filter | Veto `max_plausible_rr` (default 15) |
| Support referensi spring | Spring tidak pernah terdeteksi | Referensi dari 60% awal basis, bukan dasar seluruh range |
| Jendela SC terlalu sempit | Selling Climax terlewat | Diperluas 40 bar ke belakang dari awal range |
| Stop loss Phase D | Risiko konyol lebar | Phase C → bawah spring; Phase D → bawah LPS; breakout → bawah kontraksi terakhir |
| Entry vs pola bertentangan | Pola bilang "tunggu breakout 48.5", rencana bilang "limit 37.51" | `build_trade_plan()` kini membaca `pattern_obj` |
| Label kesiapan | `[SIAP]` untuk koin berskor 53 | `_readiness()` memperhitungkan skor + veto |
| VCP palsu | "9 kontraksi menyempit" | >6 leg = chop, bukan VCP (Minervini: 2-6) |
| A/D nol dihitung naik | "A/D Line naik (+0.00)" dapat 4 poin | Ambang 0.05 |
| Skala StochRSI biner | 19 dari 23 koin dapat nilai 3 yang sama | Ditambah tingkat 5 dan 7 |
| UnicodeEncodeError di Windows | Crash saat output di-pipe/ditangkap (cp1252) walau normal di terminal langsung | `_force_utf8()` di tiap entry script + cek regresi di `verify.py` |

## Catatan lingkungan Windows

Output memakai karakter Unicode (✓ ═ ▶ █). Di Windows, Python memilih encoding
berdasarkan tujuan output: console langsung bisa UTF-8, tapi saat output **di-pipe atau
ditangkap program lain** (termasuk Claude Code) Python jatuh ke cp1252 dan crash.
Karena itu tiap entry script memanggil `_force_utf8()` di awal. Jangan hapus fungsi ini,
dan jangan "memperbaiki" gejalanya dengan `chcp 65001` atau `PYTHONIOENCODING` manual —
perbaikannya sudah ada di dalam kode.

## Rencana berikutnya (kalau user meminta)

- **`backtest.py`** — prioritas tertinggi. Jalankan `evaluate()` walk-forward pada tiap bar
  historis TANPA lookahead, simulasikan entry/SL/TP, hitung win rate dan ekspektasi
  per komponen skor. Ini satu-satunya cara mengetahui apakah bobot 25/20/20/20/15 masuk akal.
- **Notifikasi Telegram/Discord** setelah screening selesai.
- **Filter market cap & token unlock** via CoinGecko API (masih dicek manual).
- **Penyetelan bobot berbasis data** dari CSV historis + jurnal trade user.
