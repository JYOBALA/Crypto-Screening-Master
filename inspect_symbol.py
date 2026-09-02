#!/usr/bin/env python3
"""
inspect_symbol.py — Bedah satu ticker secara detail.
Menampilkan skor per komponen, semua catatan, level Fibonacci, zona S/R, dan rencana trade.

Contoh:
    python3 inspect_symbol.py NEARUSDT
    python3 inspect_symbol.py TRXUSDT --mode weekly
    python3 inspect_symbol.py NEARUSDT --capital 5000 --risk-pct 1
"""
from __future__ import annotations
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import accumulation as acc
import indicators as ta
import scoring
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


def inspect_gem(sym, bias, regime, cfg, S):
    """Bedah detail mode akumulasi."""
    btc, _ = S.fetch_for_mode("BTCUSDT", "gem", True)
    r = acc.evaluate_gem(sym, bias, btc, regime, cfg)
    if r is None:
        raise SystemExit(f"{sym}: data historis kurang ({len(bias)} bar, "
                         f"butuh {cfg['gem_min_bars']}).")
    f = S.fmt_price
    line = "=" * 78
    print("\n" + line)
    print(f"  {sym}  —  ANALISA AKUMULASI  —  harga ${f(r['price'])}")
    print(line)
    print(f"  SKOR {r['total']}/100 ({r['grade']})   [{r['readiness']}]"
          + ("   ! KENA VETO" if r["vetoed"] else "   lolos veto"))
    print(f"  {r['phase_label']}")
    if r["vetoed"]:
        for v in r["veto_reasons"]:
            print(f"     x {v}")
    print(f"\n  Event Wyckoff terdeteksi: {', '.join(r['events']) if r['events'] else '-'}")

    print(f"\n  -- Rincian skor " + "-" * 55)
    for label, key, mx, nk in (("Struktur Basis", "s_base", 20, "basis"),
                               ("Kontraksi Vol.", "s_contraction", 20, "kontraksi"),
                               ("Event Wyckoff", "s_wyckoff", 25, "wyckoff"),
                               ("Smart Money", "s_smartmoney", 20, "smartmoney"),
                               ("RS vs BTC", "s_rs", 15, "rs")):
        pts = r[key]
        filled = int(pts / mx * 20)
        print(f"  {label:<15} {pts:>2}/{mx:<3} " + "#" * filled + "." * (20 - filled))
        for n in r["notes"][nk]:
            print(f"                  - {n}")

    print(f"\n  -- Data " + "-" * 63)
    if r["range_top"]:
        print(f"  Trading range   : {f(r['range_bottom'])} - {f(r['range_top'])} "
              f"({r['range_width_pct']}% lebar, {r['base_bars']} hari)")
        print(f"  Posisi harga    : {int((r['pos_in_range'] or 0) * 100)}% dari dasar range")
    print(f"  BandWidth pctile: {r['bw_pctile']}   |  ATR pctile: {r['atr_pctile']}")
    print(f"  Kontraksi VCP   : {r['contractions']}")
    print(f"  OBV slope       : {r['obv_slope']}   |  volume beli/jual: {r['up_down_vol']}x")
    print(f"  RS vs BTC       : slope {r['rs_slope']} | 30h {r['vs_btc_30d']}% "
          f"| 90h {r['vs_btc_90d']}%")
    print(f"  Gerak 30 hari   : {r['run_30d']:+.1f}%   |  drawdown 1thn: {r['drawdown_1y']}%")

    p = r["plan"]
    if p:
        print(f"\n  -- Rencana trade " + "-" * 54)
        print(f"     Pivot breakout {f(p['pivot']):>14}")
        print(f"     Entry          {f(p['entry']):>14}   {p['entry_style']}")
        print(f"     Stop           {f(p['sl']):>14}   ({p['sl_pct']}%)")
        print(f"     TP1            {f(p['tp1']):>14}   (+{p['tp1_pct']}%)   R:R 1:{p['rr1']}")
        print(f"     TP2            {f(p['tp2']):>14}   (+{p['tp2_pct']}%)   R:R 1:{p['rr2']}")
        print(f"     Size           ${p['position_size']:>13,.0f}")
    print(f"\n  Chart: https://www.tradingview.com/chart/?symbol=BINANCE:{sym}")
    print(line + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="contoh: NEARUSDT")
    ap.add_argument("--mode", choices=["daily", "weekly", "gem"], default="daily")
    ap.add_argument("--capital", type=float, default=S.DEFAULT_CFG["capital"])
    ap.add_argument("--risk-pct", type=float, default=S.DEFAULT_CFG["risk_pct"])
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    sym = a.symbol.upper()
    cfg = dict(S.DEFAULT_CFG)
    cfg.update({"capital": a.capital, "risk_pct": a.risk_pct})
    if a.mode == "weekly":
        cfg["min_bars_analysis"] = 80

    btc_bias, _ = S.fetch_for_mode("BTCUSDT", a.mode, not a.no_cache)
    regime = scoring.btc_regime(btc_bias)

    bias, htf = S.fetch_for_mode(sym, a.mode, not a.no_cache)
    if bias is None:
        raise SystemExit(f"Tidak bisa mengambil data {sym}. Cek nama pair-nya.")

    if a.mode == "gem":
        return inspect_gem(sym, bias, regime, cfg, S)
    r = scoring.evaluate(sym, bias, htf, regime, cfg)
    if r is None:
        raise SystemExit(f"{sym}: data historis kurang ({len(bias)} bar).")

    zones = ta.sr_zones(bias, cfg["pivot_order"], cfg["sr_tolerance_pct"])
    fb = scoring.score_fibonacci(bias, zones)
    price = r["price"]
    f = S.fmt_price
    line = "═" * 78

    print("\n" + line)
    print(f"  {sym}  —  mode {a.mode.upper()}  —  harga ${f(price)}")
    print(line)
    print(f"  BTC: {regime['status']} · {regime['message']}")
    print(f"\n  SKOR TOTAL: {r['total']}/100   grade {r['grade']}"
          + ("   ⚠ KENA VETO" if r["vetoed"] else "   ✓ lolos veto"))
    if r["vetoed"]:
        for v in r["veto_reasons"]:
            print(f"     ✗ {v}")

    print(f"\n  ── Rincian skor " + "─" * 55)
    for label, key, mx in (("Volume", "s_volume", 25), ("Stoch RSI", "s_stochrsi", 20),
                           ("Fibonacci", "s_fib", 20), ("Support/Resist", "s_sr", 20),
                           ("Chart Pattern", "s_pattern", 15)):
        pts = r[key]
        bar = "█" * int(pts / mx * 20) + "·" * (20 - int(pts / mx * 20))
        nk = {"s_volume": "volume", "s_stochrsi": "stochrsi", "s_fib": "fibonacci",
              "s_sr": "sr", "s_pattern": "pattern"}[key]
        print(f"  {label:<15} {pts:>2}/{mx:<3} {bar}")
        for n in r["notes"][nk]:
            print(f"                  · {n}")

    print(f"\n  ── Data teknikal " + "─" * 54)
    print(f"  Trend struktur   : {r['trend']}")
    print(f"  Volume vs MA20   : {r['vol_ratio']}x")
    print(f"  Stoch RSI (bias) : {r['stochrsi_k']}   |  HTF: {r['stochrsi_htf']}")
    print(f"  Fib retracement  : {r['fib_retr']}")
    print(f"  Pattern          : {r['pattern'] or '-'}")
    print(f"  Jarak ke resist. : {r['dist_to_res_pct']}%")

    if fb.get("swing"):
        sw, lv = fb["swing"], fb["fib"]
        print(f"\n  ── Fibonacci (swing low {f(sw['low'])} → high {f(sw['high'])}, "
              f"+{sw['move_pct']:.1f}%) " + "─" * 6)
        for k in ("0.382", "0.5", "0.618", "0.786", "ext_1.272", "ext_1.618"):
            mark = "  ← harga di sini" if abs(price - lv[k]) / price < 0.015 else ""
            print(f"     {k:<10} {f(lv[k]):>16}{mark}")

    if zones:
        print(f"\n  ── Zona S/R terdekat " + "─" * 50)
        near = sorted(zones, key=lambda z: abs(z["level"] - price))[:6]
        for z in sorted(near, key=lambda z: -z["level"]):
            tag = "RESIST" if z["level"] > price else "SUPPORT"
            print(f"     {tag:<8} {f(z['low']):>14} – {f(z['high']):<14} "
                  f"{z['touches']}x sentuhan, terakhir {z['last_touch_bars_ago']} bar lalu")

    p = r["plan"]
    print(f"\n  ── Rencana trade " + "─" * 54)
    print(f"     Entry  {f(p['entry']):>16}   {p.get('entry_style', '')}")
    print(f"     Stop   {f(p['sl']):>16}   ({p['sl_pct']}%)")
    print(f"     TP1    {f(p['tp1']):>16}   (+{p['tp1_pct']}%)   R:R 1:{p['rr1']}")
    print(f"     TP2    {f(p['tp2']):>16}   (+{p['tp2_pct']}%)   R:R 1:{p['rr2']}")
    print(f"     Size   ${p['position_size']:>15,.0f}   (risiko ${p['risk_amount']:,.0f})")
    print(f"\n  Chart: https://www.tradingview.com/chart/?symbol=BINANCE:{sym}")
    print(line + "\n")


if __name__ == "__main__":
    main()
