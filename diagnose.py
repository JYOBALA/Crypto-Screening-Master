#!/usr/bin/env python3
"""diagnose.py — Cek sebaran volume universe & alasan veto. Jalankan kalau hasil screening kosong."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as S


def _force_utf8() -> None:
    """Windows memakai cp1252 saat output di-pipe/ditangkap program lain
    (mis. Claude Code), sehingga karakter seperti ✓ dan ═ memicu
    UnicodeEncodeError. Paksa UTF-8 agar output konsisten di semua lingkungan."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8()

print("Endpoint aktif:", S.BASE)
data = S._get("/api/v3/ticker/24hr")
print(f"Total simbol dikembalikan API: {len(data)}")

usdt = [t for t in data if t["symbol"].endswith("USDT")]
print(f"Pair USDT: {len(usdt)}")

vols = sorted((float(t.get("quoteVolume", 0)) for t in usdt), reverse=True)
print("\nSebaran volume 24h (USD) — berapa pair yang lolos tiap ambang:")
for th in (100e6, 50e6, 20e6, 10e6, 5e6, 2e6, 1e6):
    n = sum(1 for v in vols if v >= th)
    print(f"  >= ${th/1e6:>6.0f} juta : {n:>4} pair")

print("\n10 pair volume terbesar:")
for t in sorted(usdt, key=lambda x: -float(x.get('quoteVolume', 0)))[:10]:
    print(f"  {t['symbol']:<14} ${float(t['quoteVolume']):>18,.0f}")

print("\nSetelah filter stablecoin & leveraged token:")
for mode, th in (("daily", S.DEFAULT_CFG["min_volume_usd_daily"]),
                 ("weekly", S.DEFAULT_CFG["min_volume_usd_weekly"])):
    u = S.fetch_universe(S.DEFAULT_CFG, mode)
    print(f"  mode {mode:<7} (min ${th:,.0f}) : {len(u)} pair")
