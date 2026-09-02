# Mode GEM — Deteksi Fase Akumulasi

Mode ini mencari altcoin yang sedang **sideways** tapi menunjukkan tanda-tanda sedang dikumpulkan diam-diam, sebelum harganya bergerak. Berbeda total dari mode `daily`/`weekly` yang mencari momentum yang sudah berjalan.

```bash
python screener.py --mode gem --csv
```

---

## Dasar metodologinya

Bukan kriteria karangan sendiri. Lima kerangka yang sudah mapan, masing-masing menutup titik buta yang lain:

| Metode | Yang dijawab | Bobot |
|---|---|---|
| **Wyckoff Accumulation** | Apakah ada jejak smart money mengumpulkan? Di fase mana? | 25 |
| **Weinstein Stage Analysis** | Apakah ini basis Stage 1 (siap naik) atau puncak Stage 3 (siap jatuh)? | 20 |
| **VCP Minervini + BB Squeeze** | Apakah volatilitas sudah terkompresi cukup untuk ledakan? | 20 |
| **OBV / A-D Line** | Apakah uang masuk saat harga diam? | 20 |
| **Relative Strength vs BTC** | Apakah koin ini memimpin atau tertinggal? | 15 |

### Wyckoff — inti sistemnya

Skema akumulasi klasik: **SC → AR → ST → Spring → Test → SOS → LPS**

| Event | Arti | Deteksi otomatis |
|---|---|---|
| **SC** (Selling Climax) | Panic sell diserap smart money | Bar turun lebar, volume ≥2x MA20, dekat dasar range |
| **AR** (Automatic Rally) | Tekanan jual habis, harga memantul | High tertinggi 25 bar setelah SC |
| **ST** (Secondary Test) | Uji ulang dasar, volume lebih kecil | Kembali ke area SC dengan volume <70% SC |
| **Spring** | Jebakan bear — tembus support lalu balik | Low menembus support referensi, close balik ke dalam |
| **Test** | Retest spring dengan volume sangat tipis | Volume <50% spring |
| **SOS** (Sign of Strength) | Kekuatan muncul | Bar naik lebar, close dekat high, volume ≥1.5x, tembus mid-range |
| **LPS** (Last Point of Support) | Pullback tenang yang bertahan | Volume <85% MA, low di atas mid-range |
| **UTAD** | ⚠️ Tanda **distribusi**, bukan akumulasi | Tembus atap range lalu gagal → **veto otomatis** |

Poin penting yang dipegang sistem ini: **spring bervolume RENDAH lebih bernilai daripada spring bervolume tinggi.** Volume rendah artinya tidak ada lagi yang mau jual di harga itu. Terminal shakeout (tembus >3% dengan volume tinggi) diberi skor lebih rendah karena butuh test tambahan sebelum layak dimasuki.

### Klasifikasi fase → kesiapan

| Fase | Arti | Status |
|---|---|---|
| **A** | Penurunan baru berhenti | TERLALU DINI |
| **B** | Membangun sebab, bisa berbulan-bulan | PANTAU |
| **C** | Spring — uji pasokan terakhir | **SIAP** — konviksi tertinggi |
| **D** | SOS muncul, markup mendekat | **SIAP** — tapi sering R:R lebih kecil |
| **distribusi** | UTAD terdeteksi | **VETO** |

---

## Perbedaan aturan entry dengan mode swing biasa

Mode gem tidak menyuruh beli di harga sekarang begitu saja:

- **Phase C (spring baru terjadi ≤10 bar)** → entry parsial sekarang, stop di bawah low spring. Ini titik R:R terbaik.
- **Phase D (SOS sudah muncul)** → jangan kejar bar SOS. Sistem mengarahkan ke zona back-up/LPS, dan stop dipindah ke bawah LPS (bukan bawah spring) supaya risiko tidak konyol lebar.
- **Belum Phase C/D** → entry ditaruh di **pivot breakout** (high kontraksi terakhir), bukan harga sekarang. Yang keluar adalah level alert, bukan perintah beli.

Target memakai proyeksi tinggi range (hukum cause–effect Wyckoff): TP1 = atap + 50% tinggi range, TP2 = atap + 100%.

---

## Opsi khusus mode gem

| Flag | Fungsi |
|---|---|
| `--exclude-top N` | Lewati N pair tervolume terbesar. Untuk mencari nama yang belum ramai. |
| `--phase C` atau `--phase CD` | Filter hanya fase Wyckoff tertentu |
| `--max-run-30d 40` | Perketat batas "sudah pump" (default 60%) |
| `--min-score 60` | Longgarkan ambang (default 65) |

```bash
# Hanya Phase C, lewati 20 koin terbesar, cari yang belum ramai
python screener.py --mode gem --phase C --exclude-top 20

# Bedah satu ticker dengan lensa akumulasi
python inspect_symbol.py NEARUSDT --mode gem
```

---

## Membaca outputnya

```
#   TICKER         SKOR GR  FASE   BAS  VCP  WYC  SM$   RS   BASIS BBW%    RS30        HARGA        PIVOT   R:R
1   XYZUSDT          78 A   C       15   14   19   20   10     94d    8   +4.2%     0.412000     0.478000   3.1
```

- **FASE** — fase Wyckoff. C dan D = siap; A dan B = pantau.
- **BAS/VCP/WYC/SM$/RS** — skor per komponen. Skor 78 yang ditopang WYC 19 + SM$ 20 jauh lebih berarti daripada 78 yang ditopang RS saja.
- **BASIS** — umur basis dalam hari. Makin lama, makin besar "sebab" yang terbangun.
- **BBW%** — persentil Bollinger BandWidth. Di bawah 10 = squeeze ekstrem.
- **PIVOT** — level breakout. Pasang alert di sini.

---

## Veto otomatis mode gem

1. **UTAD terdeteksi** → ini distribusi, bukan akumulasi
2. **Tidak ada basis sideways** → harga masih trending, bukan wilayah mode ini
3. **Sudah naik >60% dalam 30 hari** → sudah bukan hidden gem, kamu telat
4. **R:R ke TP1 < 1:2**
5. **BTC status MERAH** → basis tetap dicatat, tapi entry ditunda

---

## Batasan jujur

- **Fase Wyckoff hanya pasti dikonfirmasi belakangan.** Literatur Wyckoff sendiri menyatakan label baru valid in hindsight. Sistem ini membaca kemungkinan, bukan kepastian.
- **Deteksi ini aproksimasi aturan.** Spring dan SOS dideteksi lewat ambang volume/spread. Mata manusia tetap lebih baik menilai konteks — selalu buka chartnya.
- **Phase B bisa berlangsung berbulan-bulan.** Skor bagus di Phase B bukan sinyal beli, itu sinyal pasang alert.
- **Tidak ada data fundamental.** "Hidden gem" di sini murni struktur harga dan volume. Kualitas proyek, tokenomics, dan jadwal unlock harus kamu cek terpisah — kandidat terbaik secara teknikal bisa saja token dengan unlock besar minggu depan.
- **Belum ada backtest.** Statistik keberhasilan yang beredar di internet untuk VCP maupun Wyckoff berasal dari pasar saham dengan kondisi berbeda; jangan diasumsikan berlaku di kripto.

---

## Alur kerja yang disarankan

1. **Mingguan** — jalankan `--mode gem`, catat kandidat Phase B/C ke watchlist
2. **Pasang alert** di level PIVOT tiap kandidat (bukan pantengin chart)
3. **Saat alert bunyi** — jalankan `inspect_symbol.py TICKER --mode gem`, cek chart manual
4. **Verifikasi manual** — unlock token, market cap, kualitas proyek
5. **Eksekusi** hanya kalau breakout disertai ekspansi volume

Mode gem mencari kandidat lebih awal, artinya kamu menunggu lebih lama. Itu memang harga dari mencoba masuk sebelum ramai.
