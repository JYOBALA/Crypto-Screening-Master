# Kriteria Evaluasi — Eksperimen Trading Manual (pra-registrasi)

**Ditulis & di-commit SEBELUM ada satu trade pun.** Fungsinya sama seperti
`HIPOTESIS_ORDERFLOW.md`/`HIPOTESIS_REGIME.md` — kriteria di bawah tidak
diedit setelah melihat hasil trade apa pun.

Konteks: seluruh rangkaian uji proyek ini (`RINGKASAN_AKHIR.md`,
`HASIL_REGIME.md`, `HASIL_ORDERFLOW.md`) menyimpulkan skor screener TIDAK
prediktif. `journal.py` dibangun untuk menguji pertanyaan yang berbeda:
apakah **penilaian diskresioner manusia** menambah nilai di atas skor yang
sudah terbukti nol itu. Dokumen ini adalah aturan main eksperimen tersebut.

---

## Retraksi: klaim "−40% vs −83%" DICABUT

Draft awal dokumen ini akan memakai alasan "median return koin besar −40%
vs universe penuh −83%" sebagai dasar pemilihan universe. Angka −40% itu
ternyata berasal dari catatan dalam kurung di laporan buy&hold sebelumnya
**yang komposisi koinnya tidak pernah didefinisikan** ("BTC/ETH/SOL/BNB/dst")
dan **tidak bisa direproduksi**. Dicabut di sini dan tidak dipakai sebagai
dasar apa pun di bawah.

### Verifikasi pengganti — point-in-time, tanpa lookahead

Pertanyaan yang sebenarnya bisa diuji: apakah membatasi ke koin bervolume
tinggi, **dipilih tanpa lookahead** (rank memakai data SEBELUM t0 saja),
menghasilkan buy&hold yang lebih baik dari universe penuh?

**Metode:** untuk t0 ∈ {2021-06-01, 2022-06-01, 2023-06-01}, per koin di
`.cache_history` yang punya ≥30 bar data sampai t0: rank memakai rata-rata
**proksi volume USD (close × volume base)** 30 hari trailing sampai t0 —
kolom `qav` (quote volume asli) TIDAK dipakai untuk cek historis ini karena
`.cache_orderflow` hanya berisi ~158 pair yang lolos filter volume **2026**
(hari ini); memakainya sebagai kolam kandidat di t0=2021 akan memasukkan
bias lookahead persis yang ingin dihindari. Return ke depan = harga
terakhir tersedia (≈2026-09-04) dibagi harga di t0, dikurangi 1 — buy&hold
murni, bukan simulasi trade.

| t0 | Grup | n | Median | Mean | % naik | P25 | P75 |
|---|---|---:|---:|---:|---:|---:|---:|
| 2021-06-01 (n universe=124) | top8 | 8 | −43,6% | −15,8% | 37,5% | −88,6% | +47,8% |
| | top15 | 15 | −63,8% | +4,8% | 33,3% | −83,2% | +61,9% |
| | top30 | 30 | −83,5% | −25,1% | 20,0% | −95,0% | −58,4% |
| | seluruh | 124 | −93,7% | −70,7% | 7,3% | −97,2% | −81,8% |
| 2022-06-01 (n universe=184) | top8 | 8 | +80,4% | +57,9% | 62,5% | −98,2% | +152,7% |
| | top15 | 15 | −52,4% | +24,2% | 46,7% | −80,2% | +139,8% |
| | top30 | 30 | −67,1% | −21,9% | 26,7% | −92,1% | −4,5% |
| | seluruh | 184 | −84,6% | −56,7% | 10,3% | −92,6% | −61,6% |
| 2023-06-01 (n universe=207) | top8 | 8 | +3,7% | +27,4% | 50,0% | −86,0% | +136,1% |
| | top15 | 15 | −44,8% | +17,9% | 40,0% | −85,5% | +77,2% |
| | top30 | 30 | −48,1% | −6,4% | 30,0% | −86,8% | +11,2% |
| | seluruh | 207 | −79,8% | −38,2% | 13,0% | −87,7% | −47,1% |

**Temuan:** arah **konsisten di ketiga t0** — top8/top15/top30 SELALU
mengungguli "seluruh universe" pada median. Untuk top15 spesifik, selisih
median vs seluruh cukup stabil: +29,9pp (2021), +32,2pp (2022), +35,0pp
(2023). Grup top8 sendiri **tidak monoton** terhadap top15 (kadang jauh
lebih baik, kadang lebih buruk) — wajar untuk n=8, didominasi 1-2 koin
idiosinkratik (mis. SOL naik >5000% sejak 2021 mendominasi rata-rata top8
manapun ia masuk).

**Dua catatan wajib:**
1. **Survivorship tetap ada** — koin yang delisting sebelum arsip ini dibuat
   tidak bisa ditarik dari API sama sekali, di kedua sisi (universe penuh
   MAUPUN grup top-N). Dugaan (BUKAN hasil terukur): ini kemungkinan memukul
   kelompok koin kecil lebih keras (lebih banyak yang delisting total),
   sehingga selisih sebenarnya antara koin likuid vs universe penuh bisa
   **lebih besar** dari yang terukur di tabel atas.
2. **Ini temuan tentang BUY & HOLD, bukan swing trading.** Relevansinya
   terhadap pemilihan universe untuk `journal.py` bersifat **tidak
   langsung** — arah konsisten & selisih besar cukup meyakinkan sebagai
   konteks pendukung, tapi bukan bukti bahwa universe likuid akan
   menghasilkan swing trade yang lebih baik.

**Kesimpulan pemakaian:** arahnya cukup konsisten untuk dijadikan **konteks
pendukung** (bukan pembenaran tunggal) pemilihan universe likuid. Alasan
utama pembatasan universe tetap dua hal di bawah yang tidak butuh dukungan
backtest apa pun.

---

## Universe

**Alasan utama** (sah tanpa dukungan statistik apa pun):
1. **Praktis** — 10-15 koin bisa dipantau manual tiap hari; 200+ tidak.
2. **Likuiditas** — slippage & spread jauh lebih kecil di pair likuid,
   penting karena `journal.py` mengukur pnl_r sampai ke desimal ketiga.

**Alasan pendukung** (lemah, tidak langsung — lihat verifikasi di atas):
koin likuid historis menunjukkan buy&hold yang konsisten lebih baik dari
universe penuh di ketiga t0 yang diuji.

**Metode pembekuan (dijalankan sekali, sekarang, 2026-09-05):** dari
`.cache_orderflow` (union U1∪U2), simbol dgn ≥500 bar riwayat (~1,4 tahun,
menyaring listing baru/hype) dan bukan xStocks/aset pegged (sudah difilter
di `fetch_orderflow.py`), diurutkan berdasarkan **rata-rata `qav` 30 hari
terakhir** (quote volume asli, bukan proksi — data ini tersedia utk
tanggal sekarang), diambil 15 teratas apa adanya, TANPA kurasi manual:

| # | Simbol | Avg 30d volume (USD) | Lama listing |
|---|---|---:|---:|
| 1 | BTCUSDT | $1.171.523.432 | 5,7 thn |
| 2 | ETHUSDT | $655.294.919 | 5,7 thn |
| 3 | SOLUSDT | $237.365.813 | 5,7 thn |
| 4 | XRPUSDT | $193.805.868 | 5,7 thn |
| 5 | ZECUSDT | $133.995.189 | 5,7 thn |
| 6 | BNBUSDT | $89.829.980 | 5,7 thn |
| 7 | DOGEUSDT | $56.861.598 | 5,7 thn |
| 8 | TUTUSDT | $41.693.480 | 1,4 thn |
| 9 | SUIUSDT | $39.707.246 | 3,3 thn |
| 10 | ENAUSDT | $39.624.025 | 2,4 thn |
| 11 | TRUMPUSDT | $39.231.850 | 1,6 thn |
| 12 | UNIUSDT | $31.344.086 | 5,7 thn |
| 13 | PEPEUSDT | $30.522.351 | 3,3 thn |
| 14 | TRXUSDT | $30.208.018 | 5,7 thn |
| 15 | LINKUSDT | $29.080.230 | 5,7 thn |

Dua nama paling kurang "blue-chip" di daftar ini (`TUTUSDT`, `TRUMPUSDT`)
lolos murni karena memenuhi kriteria objektif di atas (likuiditas + listing
≥1,4 tahun) — **tidak** dikeluarkan atau ditambahkan secara manual. Kalau
user ingin mengganti salah satunya sebelum trade pertama karena alasan
reputasi/kenyamanan pribadi, itu keputusan sah — tapi harus dilakukan
**sekarang**, sebelum commit ini, bukan setelah melihat hasil trade.

**Dibekukan.** Tidak ditambah/dikurangi selama 50 trade pertama, apa pun
yang terlihat menarik di luar daftar ini sepanjang jalan.

---

## Aturan eksekusi

- Risiko tetap **1% per trade**, tanpa pengecualian.
- Maksimal **3 posisi terbuka** bersamaan.
- **SL tidak pernah digeser menjauh.**
- Timeframe: bias harian, hold beberapa hari sampai beberapa minggu.

## Fase 1: paper trading / ukuran sangat kecil — 50 trade

Perkiraan 6-12 bulan. **TIDAK menaikkan ukuran sebelum trade ke-50 selesai,
apa pun hasilnya di sepanjang jalan.**

## Kriteria di trade ke-50 (ditulis sekarang, TIDAK BOLEH diubah nanti)

Evaluasi via `journal.py review` setelah trade ke-50 (AMBIL, sudah ditutup)
tercatat.

**LANJUT dengan ukuran kecil, evaluasi ulang di trade ke-100**, jika SEMUA:
- Ekspektasi > 0 R
- Kalibrasi keyakinan monoton (keyakinan 5 > keyakinan 2)
- Trade yang DIAMBIL lebih baik dari trade yang DILEWATI
- Ekspektasi tetap > 0 setelah 3 trade terbaik dibuang

**LANJUT MENGUMPULKAN DATA tanpa menaikkan ukuran**, jika:
- Ekspektasi sekitar nol (−0,1 s/d +0,1 R) — belum terbukti, belum terbantah

**BERHENTI dan tulis kesimpulan**, jika:
- Ekspektasi < −0,1 R, ATAU
- Trade yang dilewati lebih baik dari yang diambil (penyaringan merugikan)

## Yang TIDAK boleh dilakukan

- Mengubah kriteria di atas setelah melihat hasil.
- Menaikkan ukuran karena "sedang panas".
- Menambah koin di luar universe beku karena ada yang terlihat menarik.
- Menyimpulkan apa pun sebelum trade ke-50.
- Berhenti mencatat trade yang memalukan.
- Mengutip ulang klaim "−40%" yang sudah dicabut di atas, di dokumen
  manapun di proyek ini.

## Konteks rezim

**Koreksi angka:** draft awal menyebut "2025 (−0,13 R) dan 2026 (−0,15 R)".
Angka itu tidak cocok dengan data mentah. Dicek langsung dari
`mechanics_test_trades_v1.csv` (varian baseline A, entry acak, `RINGKASAN_AKHIR.md`):
**2025 ≈ −0,25 R** dan **2026 ≈ −0,10 R** (data 2026 parsial s.d. awal
September). Rata-rata lintas 6 varian mekanik: 2025 ≈ −0,24 R, 2026 ≈ −0,10 R —
konsisten dgn baseline.

Kedua tahun **negatif** untuk long-only di universe ini. Eksperimen ini
dimulai di rezim yang sejauh ini sulit. Hasil merah di Fase 1 belum tentu
berarti tidak ada kemampuan diskresioner — tapi kriteria di atas tetap
berlaku apa adanya, **tanpa pembelaan setelah fakta**.

---

## Artefak

- `journal.py` — pencatat + `review` (statistik, tidak pernah menyarankan ambil/lewati).
- `journal.jsonl` — data (gitignored, personal).
- Verifikasi universe di atas: dihitung ad-hoc dari `.cache_history/` (buy&hold
  point-in-time) dan `.cache_orderflow/` (pembekuan universe sekarang);
  tidak disimpan sebagai script terpisah karena sifatnya pemeriksaan
  satu-kali untuk dokumen ini, bukan uji berulang.
