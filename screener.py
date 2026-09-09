#!/usr/bin/env python3
"""
screener.py — Screener swing trade crypto otomatis.

Menerapkan sistem skor 100 poin: Volume 25 | Stoch RSI 20 | Fibonacci 20 | S/R 20 | Pattern 15
Data: Binance public API (tanpa API key, tanpa login).

Contoh:
    python screener.py --mode daily
    python screener.py --mode weekly --min-score 60 --capital 10000
    python screener.py --mode daily --show-all --top 30
    python screener.py --selftest
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as ta          # noqa: E402
import scoring                   # noqa: E402
import accumulation as acc       # noqa: E402


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

# Endpoint utama + cadangan. data-api.binance.vision didahulukan: api.binance.com
# diblokir DNS oleh ISP di Indonesia (AAAA -> ::1, A -> IP server pemblokir lokal,
# bukan Binance -- lihat CLAUDE.md). Override manual masih bisa lewat env:
# export BINANCE_BASE="https://api.binance.com"
BASE_CANDIDATES = [
    os.environ.get("BINANCE_BASE"),
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
]
BASE_CANDIDATES = [b for b in BASE_CANDIDATES if b]
BASE = BASE_CANDIDATES[0]
LAST_HEADERS: dict = {}   # header respons terakhir yang berhasil (mis. X-MBX-USED-WEIGHT-1M)
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

DEFAULT_CFG = {
    # Tahap 0 — filter universe
    "min_volume_usd_daily": 20_000_000,
    "min_volume_usd_weekly": 50_000_000,
    "vol_mcap_min_pct": None,        # butuh data market cap eksternal (opsional)
    "min_history_bars": 200,      # bar minimum pada TF bias
    "min_bars_analysis": 120,     # bar minimum agar analisa dijalankan
    "quote": "USDT",
    # Parameter teknikal
    "pivot_order": 5,
    "sr_tolerance_pct": 1.5,
    # Manajemen risiko
    "capital": 10_000,
    "risk_pct": 1.5,
    "min_rr": 2.0,
    "max_plausible_rr": 15.0,   # di atas ini = geometri swing/support rusak (mis. R:R 1:66). Sanity-check, BUKAN filter kinerja — lihat CLAUDE.md
    "min_score": 55,   # Amandemen 2026-09-09: 70 -> 55, dasar PRAKTIS (laju tinjau chart 2-4/mgg di 15 koin beku), bukan prediktif. Lihat KRITERIA_EVALUASI.md

    "max_open_positions": 5,
    # Mode GEM (deteksi akumulasi)
    "gem_min_bars": 220,             # butuh sejarah panjang untuk lihat basis
    "max_range_width_pct": 45.0,     # lebar maksimum trading range (kripto volatil)
    "max_run_30d_pct": 60.0,         # >60% dalam 30 hari = sudah pump, bukan gem
    "gem_min_rr": 2.0,
    "gem_min_score": 65,
    "gem_respect_btc": True,
    "exclude_top_n_volume": 0,       # lewati N pair tervolume terbesar (cari yang kurang terkenal)
}

EXCLUDE_TOKENS = ("UP", "DOWN", "BULL", "BEAR")
STABLES = {"USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "EUR", "AEUR",
           "USDT", "PAX", "SUSD", "USTC", "USD1"}


# ─────────────────────────────────────────────────────────────
# Pengambilan data
# ─────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None, retries: int = 3):
    """GET dengan fallback endpoint. PENTING: kegagalan koneksi/TLS (endpoint
    diblokir/di-intersepsi, mis. DNS hijack ISP) datang sebagai EXCEPTION
    (SSLError/ConnectionError/Timeout), bukan status HTTP -- harus ditangani
    terpisah dari 403/451 (WAF/geo-block via status code). Kalau exception jenis
    ini ditangkap oleh `except Exception` generik lalu cuma di-retry ke endpoint
    yang SAMA, script menyerah setelah `retries` percobaan padahal endpoint lain
    di BASE_CANDIDATES sehat. Maka: pindah endpoint DULU, baru hitung retry."""
    global BASE
    last_exc: Exception | None = None
    bases_tried = 0
    while bases_tried <= len(BASE_CANDIDATES):
        for i in range(retries):
            try:
                r = requests.get(BASE + path, params=params, timeout=20)
                if r.status_code == 429:
                    time.sleep(5 * (i + 1))
                    continue
                if r.status_code in (403, 451):      # geo-block / WAF -> coba endpoint lain
                    last_exc = requests.exceptions.HTTPError(f"{r.status_code} dari {BASE}")
                    if _switch_base():
                        bases_tried += 1
                        break
                r.raise_for_status()
                LAST_HEADERS.clear()
                LAST_HEADERS.update(r.headers)
                return r.json()
            except (requests.exceptions.SSLError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                last_exc = e
                if _switch_base():                   # endpoint mati -> pindah, JANGAN hitung retry
                    bases_tried += 1
                    break
                if i == retries - 1:
                    raise
                time.sleep(1.5 * (i + 1))
            except Exception as e:
                last_exc = e
                if i == retries - 1:
                    raise
                time.sleep(1.5 * (i + 1))
        else:
            # retry di endpoint ini habis tanpa switch/return -> menyerah total
            raise last_exc if last_exc else RuntimeError(f"gagal tanpa exception tercatat: {path}")
    raise last_exc if last_exc else RuntimeError(f"semua endpoint gagal: {path}")


def _switch_base() -> bool:
    """Pindah ke endpoint cadangan bila endpoint aktif diblokir."""
    global BASE
    try:
        idx = BASE_CANDIDATES.index(BASE)
    except ValueError:
        idx = 0
    if idx + 1 < len(BASE_CANDIDATES):
        BASE = BASE_CANDIDATES[idx + 1]
        print(f"  (endpoint dialihkan ke {BASE})")
        return True
    return False


def fetch_universe(cfg: dict, mode: str) -> list[dict]:
    """Tahap 0: filter universe berdasarkan volume & likuiditas."""
    data = _get("/api/v3/ticker/24hr")
    quote = cfg["quote"]
    min_vol = (cfg["min_volume_usd_weekly"] if mode == "weekly"
               else cfg["min_volume_usd_daily"])

    out = []
    for t in data:
        sym = t["symbol"]
        if not sym.endswith(quote):
            continue
        base = sym[: -len(quote)]
        if base in STABLES or any(base.endswith(x) for x in EXCLUDE_TOKENS):
            continue
        qv = float(t.get("quoteVolume", 0))
        if qv < min_vol:
            continue
        out.append({"symbol": sym, "base": base, "quote_volume": qv,
                    "change_pct": float(t.get("priceChangePercent", 0)),
                    "price": float(t.get("lastPrice", 0))})
    out.sort(key=lambda x: -x["quote_volume"])
    return out


def fetch_klines(symbol: str, interval: str = "1d", limit: int = 1000,
                 use_cache: bool = True) -> pd.DataFrame | None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    cache = os.path.join(CACHE_DIR, f"{symbol}_{interval}_{stamp}.parquet")
    if use_cache and os.path.exists(cache):
        try:
            return pd.read_parquet(cache)
        except Exception:
            pass
    try:
        raw = _get("/api/v3/klines",
                   {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception:
        return None
    if not raw:
        return None

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "qav", "trades", "tbbav", "tbqav", "ignore"])
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
    df = df.iloc[:-1]                      # buang candle berjalan (belum close)
    if use_cache:
        try:
            df.to_parquet(cache)
        except Exception:
            pass
    return df


def cleanup_cache(days: int = 3):
    if not os.path.isdir(CACHE_DIR):
        return
    now = time.time()
    for f in os.listdir(CACHE_DIR):
        p = os.path.join(CACHE_DIR, f)
        if now - os.path.getmtime(p) > days * 86400:
            try:
                os.remove(p)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────

def bias_interval(mode: str) -> str:
    """TF bias: daily swing & gem -> 1d, weekly swing -> 1w."""
    return "1w" if mode == "weekly" else "1d"


def prepare_frames(df_bias: pd.DataFrame, mode: str):
    """Return (df_bias, df_htf). HTF = satu tingkat di atas TF bias."""
    if mode == "weekly":
        return df_bias, ta.resample_ohlcv(df_bias, "MS")
    return df_bias, ta.resample_ohlcv(df_bias, "W-MON")


def fetch_for_mode(symbol: str, mode: str, use_cache: bool = True):
    """Ambil data pada TF bias yang benar, lalu turunkan HTF-nya."""
    df = fetch_klines(symbol, bias_interval(mode), 1000, use_cache)
    if df is None:
        return None, None
    return prepare_frames(df, mode)


def run_screen(cfg: dict, mode: str, workers: int = 8, limit_symbols: int | None = None,
               use_cache: bool = True, verbose: bool = True):
    if verbose:
        print("→ Mengambil universe dari Binance ...", flush=True)
    if mode == "weekly":
        cfg = dict(cfg)
        cfg["min_history_bars"] = 90
        cfg["min_bars_analysis"] = 80
    elif mode == "gem":
        cfg = dict(cfg)
        cfg["min_history_bars"] = cfg["gem_min_bars"]
    universe = fetch_universe(cfg, mode)
    if mode == "gem" and cfg.get("exclude_top_n_volume"):
        universe = universe[cfg["exclude_top_n_volume"]:]
    if limit_symbols:
        universe = universe[:limit_symbols]
    if verbose:
        print(f"  {len(universe)} pair lolos filter volume "
              f"(min ${cfg['min_volume_usd_weekly' if mode == 'weekly' else 'min_volume_usd_daily']:,.0f})")

    if verbose:
        print("→ Cek regime BTC ...", flush=True)
    btc_bias, _ = fetch_for_mode("BTCUSDT", mode, use_cache)
    if btc_bias is None:
        raise SystemExit("Gagal mengambil data BTC. Cek koneksi internet.")
    regime = scoring.btc_regime(btc_bias)
    if verbose:
        print(f"  STATUS PASAR: {regime['status']} — {regime['message']}")

    results, failed = [], []

    def work(item):
        sym = item["symbol"]
        bias, htf = fetch_for_mode(sym, mode, use_cache)
        if bias is None or len(bias) < cfg["min_history_bars"]:
            return None
        try:
            if mode == "gem":
                r = acc.evaluate_gem(sym, bias, btc_bias, regime, cfg)
            else:
                r = scoring.evaluate(sym, bias, htf, regime, cfg)
        except Exception as e:      # noqa: BLE001
            failed.append((sym, str(e)))
            return None
        if r:
            r["quote_volume"] = item["quote_volume"]
            r["change_24h"] = item["change_pct"]
        return r

    if verbose:
        print(f"→ Menganalisa {len(universe)} pair ...", flush=True)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, res in enumerate(ex.map(work, universe), 1):
            if res:
                results.append(res)
            if verbose and i % 25 == 0:
                print(f"  {i}/{len(universe)}", end="\r", flush=True)

    results.sort(key=lambda r: -r["total"])
    return regime, results, failed


# ─────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────

def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0")


def print_report(regime, results, cfg, mode, top=20, show_all=False):
    line = "═" * 108
    print("\n" + line)
    print(f"  HASIL SCREENING SWING CRYPTO — mode: {mode.upper()}"
          f"   |   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(line)
    print(f"  BTC: ${fmt_price(regime['price'])}  |  STATUS: {regime['status']}  |  "
          f"Stoch RSI {regime['stochrsi_k']}  |  Trend: {regime['trend']}")
    print(f"  {regime['message']}")
    print(f"  Modal ${cfg['capital']:,.0f} · risiko {cfg['risk_pct']}%/trade · "
          f"pengali size {regime['size_mult']}x · min R:R 1:{cfg['min_rr']}")
    print(line)

    tradable = [r for r in results if not r["vetoed"] and r["total"] >= cfg["min_score"]]
    watch_floor = cfg["min_score"] - 10
    watch = [r for r in results if not r["vetoed"]
             and watch_floor <= r["total"] < cfg["min_score"]]

    header = (f"{'#':<3} {'TICKER':<13} {'SKOR':>5} {'GR':<3} "
              f"{'VOL':>4} {'SRS':>4} {'FIB':>4} {'S/R':>4} {'PAT':>4}  "
              f"{'HARGA':>13} {'ENTRY':>13} {'STOP':>13} {'TP1':>13} {'R:R':>5}")

    print(f"\n▶ LAYAK EKSEKUSI (skor ≥ {cfg['min_score']}, lolos veto) — {len(tradable)} ticker\n")
    if not tradable:
        print("  Tidak ada setup yang memenuhi syarat hari ini. Tidak entry juga sebuah keputusan.\n")
    else:
        print(header)
        print("─" * 108)
        for i, r in enumerate(tradable[:top], 1):
            p = r["plan"]
            print(f"{i:<3} {r['symbol']:<13} {r['total']:>5} {r['grade']:<3} "
                  f"{r['s_volume']:>4} {r['s_stochrsi']:>4} {r['s_fib']:>4} "
                  f"{r['s_sr']:>4} {r['s_pattern']:>4}  "
                  f"{fmt_price(r['price']):>13} {fmt_price(p['entry']):>13} "
                  f"{fmt_price(p['sl']):>13} {fmt_price(p['tp1']):>13} "
                  f"{p['rr1']:>5.1f}")

    print(f"\n▶ WATCHLIST (skor {watch_floor}–{cfg['min_score'] - 1}, pasang alert) — {len(watch)} ticker")
    if watch:
        print("  " + ", ".join(f"{r['symbol']}({r['total']})" for r in watch[:25]))

    # Detail per kandidat teratas — kalau kosong, tampilkan watchlist teratas
    detail = tradable[:5] if tradable else watch[:3]
    header_txt = ("DETAIL KANDIDAT TERATAS" if tradable
                  else "DETAIL WATCHLIST TERATAS (belum layak entry — untuk dipantau)")
    print("\n" + line)
    print("  " + header_txt)
    print(line)
    if not detail:
        print("\n  Tidak ada kandidat sama sekali. Jalankan --show-all untuk melihat alasan veto,")
        print("  atau longgarkan filter: --min-score 45 --min-volume 10000000\n")
    for r in detail:
        p = r["plan"]
        print(f"\n■ {r['symbol']}  —  {r['total']}/100  ({r['grade']})   "
              f"harga ${fmt_price(r['price'])}   vol24h ${r.get('quote_volume', 0):,.0f}")
        print(f"  Volume    {r['s_volume']:>2}/25 · {r['notes']['volume'][0]}")
        print(f"  StochRSI  {r['s_stochrsi']:>2}/20 · {r['notes']['stochrsi'][0]}")
        print(f"  Fibonacci {r['s_fib']:>2}/20 · {r['notes']['fibonacci'][0]}")
        print(f"  S/R       {r['s_sr']:>2}/20 · {r['notes']['sr'][0]}")
        print(f"  Pattern   {r['s_pattern']:>2}/15 · {r['notes']['pattern'][0]}")
        print(f"  ── Rencana: Entry {fmt_price(p['entry'])} "
              f"[{p.get('entry_style', '-')}] | SL {fmt_price(p['sl'])} "
              f"({p['sl_pct']}%) | TP1 {fmt_price(p['tp1'])} (+{p['tp1_pct']}%) "
              f"| TP2 {fmt_price(p['tp2'])} (+{p['tp2_pct']}%)")
        print(f"     R:R 1:{p['rr1']} / 1:{p['rr2']}  ·  Size ${p['position_size']:,.0f} "
              f"(risiko ${p['risk_amount']:,.0f})")
        print(f"     TradingView: https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}")

    if show_all:
        print("\n" + line)
        print("  DITOLAK VETO (alasan)")
        print(line)
        for r in [x for x in results if x["vetoed"]][:30]:
            print(f"  {r['symbol']:<13} skor {r['total']:>3} — {r['veto_reasons'][0]}")

    print("\n" + line)
    print("  Ingat: skor tinggi bukan jaminan. Cek chart manual sebelum entry, "
          "\n  patuhi stop loss, dan catat setiap trade di jurnal.")
    print(line + "\n")



def print_gem_report(regime, results, cfg, top=20, show_all=False):
    """Laporan khusus mode GEM (deteksi akumulasi)."""
    line = "=" * 112
    print("\n" + line)
    print(f"  HIDDEN GEM SCANNER — DETEKSI FASE AKUMULASI"
          f"   |   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(line)
    print(f"  BTC: ${fmt_price(regime['price'])}  |  STATUS: {regime['status']}  |  "
          f"Trend: {regime['trend']}")
    print(f"  Metode: Wyckoff Accumulation + VCP Minervini + BB Squeeze + "
          f"Weinstein Stage 1 + RS vs BTC")
    print(f"  Modal ${cfg['capital']:,.0f} · risiko {cfg['risk_pct']}%/trade · "
          f"min skor {cfg['gem_min_score']} · min R:R 1:{cfg['gem_min_rr']}")
    print(line)

    ok = [r for r in results if not r["vetoed"] and r["total"] >= cfg["gem_min_score"]]
    ready = [r for r in ok if r["readiness"] == "SIAP"]
    watch = [r for r in ok if r["readiness"] != "SIAP"]
    early = [r for r in results if not r["vetoed"]
             and 50 <= r["total"] < cfg["gem_min_score"]]

    hdr = (f"{'#':<3} {'TICKER':<13} {'SKOR':>4} {'GR':<3} {'FASE':<5} "
           f"{'BAS':>4} {'VCP':>4} {'WYC':>4} {'SM$':>4} {'RS':>4}  "
           f"{'BASIS':>6} {'BBW%':>5} {'RS30':>7} {'HARGA':>12} {'PIVOT':>12} {'R:R':>5}")

    def rows(items, start=1):
        print(hdr)
        print("-" * 112)
        for i, r in enumerate(items[:top], start):
            p = r["plan"] or {}
            bw = "-" if r["bw_pctile"] is None else f"{r['bw_pctile']:.0f}"
            rs30 = "-" if r["vs_btc_30d"] is None else f"{r['vs_btc_30d']:+.1f}%"
            basis = f"{r['base_bars']}d" if r["base_bars"] else "-"
            print(f"{i:<3} {r['symbol']:<13} {r['total']:>4} {r['grade']:<3} "
                  f"{r['phase']:<5} "
                  f"{r['s_base']:>4} {r['s_contraction']:>4} {r['s_wyckoff']:>4} "
                  f"{r['s_smartmoney']:>4} {r['s_rs']:>4}  "
                  f"{basis:>6} {bw:>5} {rs30:>7} "
                  f"{fmt_price(r['price']):>12} {fmt_price(p.get('pivot', 0)):>12} "
                  f"{p.get('rr1', 0):>5.1f}")

    print(f"\n> SIAP DIEKSEKUSI — Phase C/D, akumulasi matang ({len(ready)} ticker)\n")
    if ready:
        rows(ready)
    else:
        print("  Belum ada basis yang matang ke Phase C/D. Ini normal — akumulasi butuh waktu.\n")

    print(f"\n> PANTAU — basis terbentuk, fase belum matang ({len(watch)} ticker)")
    if watch:
        print("  " + ", ".join(f"{r['symbol']}({r['total']}/Ph{r['phase']})" for r in watch[:25]))

    if early:
        print(f"\n> RADAR AWAL — skor 50-{cfg['gem_min_score']-1} ({len(early)} ticker)")
        print("  " + ", ".join(f"{r['symbol']}({r['total']})" for r in early[:25]))

    detail = (ready or watch or early)[:5]
    print("\n" + line)
    print("  DETAIL KANDIDAT TERATAS")
    print(line)
    for r in detail:
        p = r["plan"] or {}
        print(f"\n# {r['symbol']}  —  {r['total']}/100 ({r['grade']})  "
              f"[{r['readiness']}]  harga ${fmt_price(r['price'])}")
        print(f"  {r['phase_label']}")
        print(f"  Event terdeteksi: {', '.join(r['events']) if r['events'] else '-'}")
        print(f"\n  Struktur Basis   {r['s_base']:>2}/20 · " +
              " | ".join(r["notes"]["basis"]))
        print(f"  Kontraksi Vol.   {r['s_contraction']:>2}/20 · " +
              " | ".join(r["notes"]["kontraksi"]))
        print(f"  Event Wyckoff    {r['s_wyckoff']:>2}/25 · " +
              " | ".join(r["notes"]["wyckoff"]))
        print(f"  Smart Money      {r['s_smartmoney']:>2}/20 · " +
              " | ".join(r["notes"]["smartmoney"]))
        print(f"  RS vs BTC        {r['s_rs']:>2}/15 · " +
              " | ".join(r["notes"]["rs"]))
        if r["range_top"]:
            print(f"\n  Range: {fmt_price(r['range_bottom'])} - {fmt_price(r['range_top'])} "
                  f"(lebar {r['range_width_pct']}%, {r['base_bars']} hari, "
                  f"posisi harga {int((r['pos_in_range'] or 0)*100)}%)")
        if r["drawdown_1y"] is not None:
            print(f"  Drawdown 1 tahun: {r['drawdown_1y']}%  ·  gerak 30 hari: {r['run_30d']:+.1f}%")
        if p:
            print(f"\n  -- Rencana: Entry {fmt_price(p['entry'])} ({p['entry_style']})")
            print(f"     SL {fmt_price(p['sl'])} ({p['sl_pct']}%) | "
                  f"TP1 {fmt_price(p['tp1'])} (+{p['tp1_pct']}%) | "
                  f"TP2 {fmt_price(p['tp2'])} (+{p['tp2_pct']}%)")
            print(f"     R:R 1:{p['rr1']} / 1:{p['rr2']}  ·  Size ${p['position_size']:,.0f}")
        print(f"     TradingView: https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}")

    if show_all:
        print("\n" + line)
        print("  DITOLAK VETO")
        print(line)
        for r in [x for x in results if x["vetoed"]][:30]:
            print(f"  {r['symbol']:<13} skor {r['total']:>3} Ph{r['phase']:<2} — {r['veto_reasons'][0]}")

    print("\n" + line)
    print("  Akumulasi butuh KESABARAN. Phase B bisa berlangsung berbulan-bulan.")
    print("  Pasang alert di level pivot, jangan entry hanya karena basisnya terlihat bagus.")
    print(line + "\n")

def export(results, regime, cfg, mode, outdir="."):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    rows = []
    if mode == "gem":
        for r in results:
            p = r["plan"] or {}
            rows.append({
                "symbol": r["symbol"], "total_score": r["total"], "grade": r["grade"],
                "phase": r["phase"], "readiness": r["readiness"],
                "vetoed": r["vetoed"], "veto_reason": "; ".join(r["veto_reasons"]),
                "score_base": r["s_base"], "score_contraction": r["s_contraction"],
                "score_wyckoff": r["s_wyckoff"], "score_smartmoney": r["s_smartmoney"],
                "score_rs": r["s_rs"],
                "price": r["price"], "range_bottom": r["range_bottom"],
                "range_top": r["range_top"], "range_width_pct": r["range_width_pct"],
                "base_bars": r["base_bars"], "pos_in_range": r["pos_in_range"],
                "bbw_percentile": r["bw_pctile"], "atr_percentile": r["atr_pctile"],
                "contractions": str(r["contractions"]),
                "obv_slope": r["obv_slope"], "up_down_vol": r["up_down_vol"],
                "rs_slope": r["rs_slope"], "vs_btc_30d": r["vs_btc_30d"],
                "vs_btc_90d": r["vs_btc_90d"], "run_30d": r["run_30d"],
                "drawdown_1y": r["drawdown_1y"], "events": ",".join(r["events"]),
                "pivot": p.get("pivot"), "entry": p.get("entry"),
                "entry_style": p.get("entry_style"), "stop_loss": p.get("sl"),
                "tp1": p.get("tp1"), "tp2": p.get("tp2"),
                "rr_tp1": p.get("rr1"), "rr_tp2": p.get("rr2"),
                "position_size_usd": p.get("position_size"),
                "vol_24h_usd": r.get("quote_volume"),
                "tradingview": f"https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}",
            })
        df = pd.DataFrame(rows)
        csv_path = os.path.join(outdir, f"screening_gem_{stamp}.csv")
        df.to_csv(csv_path, index=False)
        json_path = os.path.join(outdir, f"screening_gem_{stamp}.json")
        with open(json_path, "w") as f:
            json.dump({"generated_at": stamp, "mode": mode, "regime": regime,
                       "config": cfg, "results": results}, f, indent=2, default=str)
        return csv_path, json_path

    for r in results:
        p = r["plan"]
        rows.append({
            "symbol": r["symbol"], "total_score": r["total"], "grade": r["grade"],
            "vetoed": r["vetoed"], "veto_reason": "; ".join(r["veto_reasons"]),
            "score_volume": r["s_volume"], "score_stochrsi": r["s_stochrsi"],
            "score_fib": r["s_fib"], "score_sr": r["s_sr"], "score_pattern": r["s_pattern"],
            "price": r["price"], "entry": p["entry"],
            "entry_style": p.get("entry_style"), "stop_loss": p["sl"],
            "tp1": p["tp1"], "tp2": p["tp2"], "rr_tp1": p["rr1"], "rr_tp2": p["rr2"],
            "position_size_usd": p["position_size"], "sl_pct": p["sl_pct"],
            "vol_ratio": r["vol_ratio"], "stochrsi_k": r["stochrsi_k"],
            "stochrsi_htf": r["stochrsi_htf"], "fib_retr": r["fib_retr"],
            "trend": r["trend"], "pattern": r["pattern"],
            "dist_to_resistance_pct": r["dist_to_res_pct"],
            "vol_24h_usd": r.get("quote_volume"), "change_24h": r.get("change_24h"),
            "tradingview": f"https://www.tradingview.com/chart/?symbol=BINANCE:{r['symbol']}",
        })
    df = pd.DataFrame(rows)
    csv_path = os.path.join(outdir, f"screening_{mode}_{stamp}.csv")
    df.to_csv(csv_path, index=False)

    json_path = os.path.join(outdir, f"screening_{mode}_{stamp}.json")
    with open(json_path, "w") as f:
        json.dump({"generated_at": stamp, "mode": mode, "regime": regime,
                   "config": cfg, "results": results}, f, indent=2, default=str)
    return csv_path, json_path


# ─────────────────────────────────────────────────────────────
# Selftest (tanpa internet)
# ─────────────────────────────────────────────────────────────

def synth(n=400, seed=1, scenario="bullish_pullback"):
    rng = np.random.default_rng(seed)
    price, rows = 100.0, []
    for i in range(n):
        if scenario == "bullish_pullback":
            drift = 0.004 if i < n - 40 else -0.004
        elif scenario == "downtrend":
            drift = -0.005
        else:
            drift = 0.0
        ret = drift + rng.normal(0, 0.025)
        o = price
        price = max(0.5, price * (1 + ret))
        h = max(o, price) * (1 + abs(rng.normal(0, 0.01)))
        l = min(o, price) * (1 - abs(rng.normal(0, 0.01)))
        vol = abs(rng.normal(1_000_000, 250_000)) * (2.5 if i > n - 5 else 1)
        rows.append((o, h, l, price, vol))
    idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def selftest():
    print("Menjalankan selftest dengan data sintetis (tanpa internet)...\n")
    cfg = dict(DEFAULT_CFG)
    ok = True
    for scen in ("bullish_pullback", "downtrend", "range"):
        for seed in (1, 7, 42):
            d = synth(seed=seed, scenario=scen)
            bias, htf = prepare_frames(d, "daily")
            reg = scoring.btc_regime(bias)
            r = scoring.evaluate(f"TEST-{scen}-{seed}", bias, htf, reg, cfg)
            if r is None:
                print(f"  ✗ {scen}/{seed}: evaluate() mengembalikan None")
                ok = False
                continue
            comp = (r["s_volume"], r["s_stochrsi"], r["s_fib"], r["s_sr"], r["s_pattern"])
            assert sum(comp) == r["total"], "total skor tidak konsisten"
            assert 0 <= r["total"] <= 100, "skor di luar rentang"
            p = r["plan"]
            assert p["sl"] < p["entry"], "SL harus di bawah entry"
            assert p["tp1"] > p["entry"], "TP1 harus di atas entry"
            assert p["tp2"] > p["tp1"], "TP2 harus di atas TP1"
            print(f"  ✓ {scen:<18} seed={seed}  regime={reg['status']:<6} "
                  f"skor={r['total']:>3} {r['grade']:<2} veto={r['vetoed']} "
                  f"RR={p['rr1']:.2f} komponen={comp}")

    # cek mode weekly (data mingguan langsung)
    cfg_w = dict(cfg); cfg_w["min_bars_analysis"] = 80
    d = synth(n=200, seed=3)
    d.index = pd.date_range("2021-01-04", periods=len(d), freq="W-MON", tz="UTC")
    bias, htf = prepare_frames(d, "weekly")
    print(f"\n  ✓ frame weekly: {len(bias)} bar mingguan, {len(htf)} bar bulanan")
    reg = scoring.btc_regime(bias)
    r = scoring.evaluate("TEST-WEEKLY", bias, htf, reg, cfg_w)
    print(f"  ✓ mode weekly skor={r['total']} grade={r['grade']}")

    # ── Mode GEM: skema Wyckoff sintetis
    print("\n  Menguji mesin akumulasi (mode gem)...")
    ok = selftest_gem(cfg) and ok

    print("\nSelftest selesai:", "SEMUA LULUS ✓" if ok else "ADA YANG GAGAL ✗")
    return ok


def _synth_wyckoff(seed=11, truncate=0):
    """Bangun kurva berbentuk skema akumulasi Wyckoff untuk uji deteksi."""
    rng = np.random.default_rng(seed)
    rows, p, V = [], 100.0, 1_000_000

    def bar(o, c, vol, w=0.01):
        return (o, max(o, c) * (1 + abs(rng.normal(0, w))),
                min(o, c) * (1 - abs(rng.normal(0, w))), c, vol)

    for _ in range(120):                                   # markdown
        o = p; p *= (1 - 0.0068 + rng.normal(0, 0.012)); rows.append(bar(o, p, V))
    o = p; p *= 0.80; rows.append((o, o * 1.005, p * 0.97, p, V * 4.2))   # SC
    sc_low = p
    for _ in range(12):                                    # AR
        o = p; p *= 1.022; rows.append(bar(o, p, V * 1.4))
    ar_high = p
    for _ in range(10):                                    # ST
        o = p; p *= 0.972; rows.append(bar(o, p, V * 0.75))
    for _ in range(70):                                    # Phase B
        o = p
        drift = 0.004 if p < (sc_low + ar_high) / 2 else -0.004
        p = min(max(p * (1 + drift + rng.normal(0, 0.014)), sc_low * 1.01), ar_high * 0.99)
        rows.append(bar(o, p, V * (0.95 if p > o else 0.55)))
    o = p; p = sc_low * 1.015                              # SPRING (volume rendah)
    rows.append((o, o * 1.004, sc_low * 0.955, p, V * 0.75))
    for _ in range(6):
        o = p; p *= 1.012; rows.append(bar(o, p, V * 0.6))
    o = p; p = sc_low * 1.02                               # TEST
    rows.append((o, o * 1.003, sc_low * 0.99, p, V * 0.26))
    for _ in range(4):
        o = p; p *= 1.008; rows.append(bar(o, p, V * 0.5))
    o = p; p = (sc_low + ar_high) / 2 * 1.06               # SOS
    rows.append((o, p * 1.008, o * 0.998, p, V * 2.6))
    for _ in range(4):                                     # LPS
        o = p; p *= 0.985; rows.append(bar(o, p, V * 0.45))

    idx = pd.date_range("2023-01-01", periods=len(rows), freq="D", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)
    return df.iloc[:len(df) - truncate] if truncate else df


def selftest_gem(cfg) -> bool:
    btc = synth(n=400, seed=2, scenario="range")
    regime = {"status": "HIJAU", "size_mult": 1.0, "message": "t", "price": 0,
              "stochrsi_k": 50, "above_ema50": True, "above_ema200": True, "trend": "range"}
    ok = True

    phase_d = _synth_wyckoff()
    phase_c = _synth_wyckoff(truncate=10)
    trending = synth(n=340, seed=4, scenario="bullish_pullback")

    for name, d, expect in (("skema Wyckoff (Phase D)", phase_d, "D"),
                            ("skema Wyckoff (Phase C)", phase_c, "C"),
                            ("trending (bukan basis)", trending, None)):
        b = btc.iloc[-len(d):].copy(); b.index = d.index
        r = acc.evaluate_gem(name, d, b, regime, cfg)
        if r is None:
            print(f"    ✗ {name}: evaluate_gem mengembalikan None")
            ok = False
            continue
        comp = (r["s_base"], r["s_contraction"], r["s_wyckoff"],
                r["s_smartmoney"], r["s_rs"])
        assert sum(comp) == r["total"], "total skor tidak konsisten"
        assert 0 <= r["total"] <= 100
        if r["plan"]:
            assert r["plan"]["sl"] < r["plan"]["entry"] < r["plan"]["tp1"], "urutan SL/entry/TP salah"
        if expect and r["phase"] != expect:
            print(f"    ✗ {name}: fase terdeteksi {r['phase']}, harusnya {expect}")
            ok = False
            continue
        print(f"    ✓ {name:<24} skor={r['total']:>3} Phase {r['phase']:<2} "
              f"{r['readiness']:<12} veto={str(r['vetoed']):<5} komponen={comp}")

    # Skema Wyckoff harus jelas menang atas data trending acak
    b = btc.iloc[-len(trending):].copy(); b.index = trending.index
    rt = acc.evaluate_gem("t", trending, b, regime, cfg)
    rc = acc.evaluate_gem("c", phase_c, btc.iloc[-len(phase_c):].set_axis(phase_c.index), regime, cfg)
    if rt and rc and rc["total"] <= rt["total"]:
        print(f"    ✗ diskriminasi gagal: Wyckoff {rc['total']} <= trending {rt['total']}")
        ok = False
    elif rt and rc:
        print(f"    ✓ diskriminasi: Wyckoff {rc['total']} > trending {rt['total']}")
    return ok


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Screener swing trade crypto (Binance)")
    ap.add_argument("--mode", choices=["daily", "weekly", "gem"], default="daily",
                    help="daily = swing harian; weekly = swing mingguan; "
                         "gem = deteksi fase akumulasi (hidden gem)")
    ap.add_argument("--capital", type=float, default=DEFAULT_CFG["capital"])
    ap.add_argument("--risk-pct", type=float, default=DEFAULT_CFG["risk_pct"])
    ap.add_argument("--min-score", type=int, default=DEFAULT_CFG["min_score"])
    ap.add_argument("--min-rr", type=float, default=DEFAULT_CFG["min_rr"])
    ap.add_argument("--min-volume", type=float, default=None,
                    help="Override volume 24h minimum dalam USD")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--limit-symbols", type=int, default=None,
                    help="Batasi jumlah pair (untuk uji cepat)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--show-all", action="store_true", help="Tampilkan yang kena veto")
    ap.add_argument("--csv", action="store_true", help="Ekspor CSV + JSON")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--exclude-top", type=int, default=0,
                    help="[gem] lewati N pair tervolume terbesar agar dapat nama kurang terkenal")
    ap.add_argument("--max-run-30d", type=float, default=None,
                    help="[gem] batas kenaikan 30 hari (%%); di atas ini dianggap sudah pump")
    ap.add_argument("--phase", default=None,
                    help="[gem] filter fase Wyckoff, mis. C atau CD")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    cfg = dict(DEFAULT_CFG)
    cfg.update({"capital": args.capital, "risk_pct": args.risk_pct,
                "min_score": args.min_score, "min_rr": args.min_rr})
    if args.min_volume:
        cfg["min_volume_usd_daily"] = args.min_volume
        cfg["min_volume_usd_weekly"] = args.min_volume
    if args.mode == "gem":
        cfg["exclude_top_n_volume"] = args.exclude_top
        if args.max_run_30d is not None:
            cfg["max_run_30d_pct"] = args.max_run_30d
        if args.min_score != DEFAULT_CFG["min_score"]:
            cfg["gem_min_score"] = args.min_score
        if args.min_rr != DEFAULT_CFG["min_rr"]:
            cfg["gem_min_rr"] = args.min_rr

    cleanup_cache()
    t0 = time.time()
    regime, results, failed = run_screen(
        cfg, args.mode, workers=args.workers,
        limit_symbols=args.limit_symbols, use_cache=not args.no_cache)
    print(f"\n  Selesai dalam {time.time() - t0:.1f} detik. "
          f"{len(results)} pair dianalisa, {len(failed)} error.")

    if args.mode == "gem":
        if args.phase:
            want = set(args.phase.upper())
            results = [r for r in results if r["phase"] in want]
        print_gem_report(regime, results, cfg, top=args.top, show_all=args.show_all)
    else:
        print_report(regime, results, cfg, args.mode, top=args.top, show_all=args.show_all)

    if args.csv:
        c, j = export(results, regime, cfg, args.mode, args.outdir)
        print(f"  Diekspor: {c}\n            {j}\n")


if __name__ == "__main__":
    main()
