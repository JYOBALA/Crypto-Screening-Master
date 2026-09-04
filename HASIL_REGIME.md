# Hasil Uji Regime — Tahap 1 (2026-09-04)

Protokol & hipotesis: [`HIPOTESIS_REGIME.md`](HIPOTESIS_REGIME.md) — di-commit sebelum run.
Script: `regime_test.py`. Data: `.cache_history/` (422 pair, 2021–2026).
Ukuran: return **30 hari ke depan** universe (per koin), dibandingkan antara titik
evaluasi mingguan berlabel BULL vs BEAR. 95% CI = bootstrap blok per minggu
(2000 resample) — menghormati fakta bahwa semua koin bergerak bersama, jadi
sampel efektif ≈ 130 minggu, bukan puluhan ribu observasi.

## Kriteria lulus (ketiganya)
1. Selisih BULL − BEAR ≥ 5 poin persen
2. 95% CI selisih tidak melewati nol
3. Arah selisih sama di 2021–2023 dan 2024–2026

## Hasil

| Detektor | BULL ret30 | BEAR ret30 | Selisih | 95% CI | Arah stabil? | Verdict |
|---|---:|---:|---:|---|:---:|:---:|
| R1 BTC > SMA200 | −1,9% | −1,7% | **−0,2 pp** | [−5,1, +5,1] | tidak (+2,6 / −1,4) | gagal |
| R2 BTC > EMA50 *(aturan sekarang)* | −0,4% | −3,2% | **+2,8 pp** | [−2,5, +8,1] | ya (+6,9 / +0,7) | gagal |
| R3 breadth > 50% | −3,4% | −1,4% | **−1,9 pp** | [−9,0, +5,3] | ya (arah **terbalik**) | gagal |
| R4 dominasi-proksi turun | −4,1% | −1,0% | **−3,2 pp** | [−8,6, +2,5] | ya (arah **terbalik**) | gagal |
| R5 BTC 90d > 0 | −2,6% | −1,1% | **−1,5 pp** | [−6,9, +3,7] | ya (arah **terbalik**) | gagal |

## Temuan

- **Tidak ada detektor yang lulus.** Semua CI melewati nol; selisih terbesar
  (R2, +2,8 pp) di bawah ambang 5 pp.
- Return 30-hari-ke-depan universe **negatif di hampir semua regime** (−1% s/d
  −4%). Konsisten dengan median koin −83%: universe bleeds terus-menerus, dan
  label regime nyaris tidak memisahkan seberapa cepat.
- **R3, R4, R5 anti-prediktif** — "BULL" justru punya return ke depan **lebih
  buruk** dari "BEAR". Breadth tinggi, dominasi turun, dan momentum BTC positif
  semuanya menandai puncak lokal di universe ini, bukan awal kenaikan.
- **R2 (aturan EMA50 sekarang)** satu-satunya yang arahnya "benar" (BULL
  −0,4% > BEAR −3,2%) dan stabil arah, tapi magnitudo 2,8 pp tidak signifikan
  (CI [−2,5, +8,1]). Bukan fondasi yang cukup.
- CI lebar (±5 pp) meski puluhan ribu observasi koin — karena bootstrap blok
  menghitung dengan benar bahwa observasi dalam satu minggu berkorelasi. Sampel
  regime efektif hanya ~130 minggu.

## Kesimpulan

**Regime tidak bisa dideteksi dengan nilai prediktif yang stabil di universe &
periode ini.** Tahap 2 (perbandingan strategi long/short) **TIDAK dijalankan** —
tidak ada detektor terbukti untuk membangunnya, sesuai protokol.

Data futures perp (`/fapi/*`), funding rate, dan simulasi short tidak ditarik/
dibuat karena prasyaratnya tidak terpenuhi.
