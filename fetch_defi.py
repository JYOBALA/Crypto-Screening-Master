#!/usr/bin/env python3
"""
fetch_defi.py — Unduh data fundamental protokol dari DefiLlama untuk uji nilai
prediktif cross-sectional (defi_test.py). Lihat HIPOTESIS_DEFI.md — pra-registrasi
di-commit SEBELUM script ini dibuat/dijalankan.

Kategori data KEENAM yang diuji di proyek ini (5 sebelumnya turunan harga/volume;
lihat RINGKASAN_AKHIR.md). Data fundamental = pemakaian protokol sebenarnya:
TVL, fee, revenue, volume DEX, pasokan stablecoin per chain.

Sumber:
  api.llama.fi            -> /protocols, /protocol/{slug}, /summary/fees, /summary/dexs,
                             /v2/historicalChainTvl/{chain}
  stablecoins.llama.fi    -> /stablecoincharts/{chain}
Semua GRATIS tanpa API key. Host ini TIDAK diblokir ISP (beda dari api.binance.com)
sehingga dipakai langsung; hanya panggilan Binance (/api/v3/exchangeInfo untuk
pemetaan simbol) yang lewat screener._get (BASE_CANDIDATES + _switch_base, sesuai
CLAUDE.md).

Harga token untuk return dipakai dari .cache_history/ (Binance daily, sama seperti
backtest.py). Simbol yang dipetakan tapi tidak ada di .cache_history/ diunduh ke
.cache_defi/_prices/ lewat screener._get("/api/v3/klines").

Output -> .cache_defi/  (terpisah dari semua cache lain):
  universe_defi.json        daftar protokol + tabel pemetaan (berhasil/gagal + alasan)
  raw/{SYMBOL}.json         deret mentah per protokol (tvl, tokens, tokensInUsd, fees, revenue, dexvol)
  _chains/{chain}.json      TVL chain + pasokan stablecoin chain
  _prices/{SYMBOL}_1d.parquet   harga Binance untuk simbol yang tak ada di .cache_history/

    python fetch_defi.py                 # bangun universe + unduh semua
    python fetch_defi.py --refresh       # unduh ulang walau file sudah ada
    python fetch_defi.py --universe-only # cuma bangun/laporkan pemetaan, tanpa unduh deret

Read-only terhadap akun: tidak ada API key, tidak ada order. Hanya GET publik.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener as scr  # noqa: E402  (_force_utf8 via import + Binance _get untuk exchangeInfo)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFI_DIR = os.path.join(HERE, ".cache_defi")
RAW_DIR = os.path.join(DEFI_DIR, "raw")
CHAIN_DIR = os.path.join(DEFI_DIR, "_chains")
PRICE_DIR = os.path.join(DEFI_DIR, "_prices")
HIST_DIR = os.path.join(HERE, ".cache_history")

LLAMA = "https://api.llama.fi"
STABLES_API = "https://stablecoins.llama.fi"
MS_DAY = 86_400_000
START_MS = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

# HIPOTESIS_DEFI.md, bagian Universe: kategori yang dikecualikan.
EXCLUDE_CATEGORIES = {
    "Chain",             # L1/L2 itu sendiri, bukan protokol; D4 (share of chain) tak bermakna
    "CEX",               # "TVL" = cadangan bursa, bukan pemakaian protokol on-chain
    "Stablecoin",        # dinamika harga token issuer != kripto volatil
    "Basis Trading",     # sebagian besar produk tertutup, TVL != harga token
    "Memes",             # tidak punya data fundamental (jebakan #4) -- jarang lolos filter TVL toh
}

# Ditulis SEBELUM run. Tabrakan ticker DefiLlama<->Binance yang default "max current
# TVL" salah pilih, atau ticker yang menabrak koin non-DeFi. Kosong di awal; diisi
# kalau tabel pemetaan (dilaporkan) menunjukkan kesalahan yang jelas. Perubahan di
# sini = perubahan universe -> harus dilakukan sebelum defi_test.py dijalankan.
SYMBOL_OVERRIDES: dict[str, str] = {
    # "SYMBOL": "defillama-slug",
}
SYMBOL_BLOCKLIST: dict[str, str] = {
    # "SYMBOL": "alasan",
}

MIN_HISTORY_DAYS = 180   # HIPOTESIS_DEFI.md: butuh >=180 hari TVL ATAU fee


# ─────────────────────────────────────────────────────────────
# GET DefiLlama (host tidak diblokir; retry sederhana untuk hiccup jaringan)
# ─────────────────────────────────────────────────────────────

def _llama_get(url: str, retries: int = 4, timeout: int = 60):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research; read-only)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return None                     # protokol tak punya dimensi ini (mis. bukan DEX)
            time.sleep(2.0 * (i + 1))
        except Exception as e:                   # noqa: BLE001
            last = e
            time.sleep(2.0 * (i + 1))
    print(f"   ! gagal {url}  ({last})", flush=True)
    return None


# ─────────────────────────────────────────────────────────────
# Universe: petakan protokol DefiLlama -> simbol Binance USDT spot
# ─────────────────────────────────────────────────────────────

def _binance_usdt_bases() -> set[str]:
    info = scr._get("/api/v3/exchangeInfo")
    out = set()
    for s in info.get("symbols", []):
        if s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
            continue
        if not s.get("isSpotTradingAllowed", False):
            continue
        b = s.get("baseAsset", "")
        if b in scr.STABLES or any(b.endswith(x) for x in scr.EXCLUDE_TOKENS):
            continue
        out.add(b)
    return out


def build_universe(refresh: bool) -> dict:
    os.makedirs(DEFI_DIR, exist_ok=True)
    path = os.path.join(DEFI_DIR, "universe_defi.json")
    if os.path.exists(path) and not refresh:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    bases = _binance_usdt_bases()
    print(f"-> {len(bases)} base asset USDT spot Binance", flush=True)
    prot = _llama_get(f"{LLAMA}/protocols")
    if not prot:
        raise SystemExit("gagal menarik /protocols")
    print(f"-> {len(prot)} protokol DefiLlama", flush=True)

    # kelompokkan protokol per ticker
    by_sym: dict[str, list[dict]] = {}
    for p in prot:
        sym = (p.get("symbol") or "").upper().lstrip("$")
        if not sym or sym in ("-", "NONE"):
            continue
        by_sym.setdefault(sym, []).append(p)

    mapped, failed = [], []
    for sym in sorted(bases):
        if sym in SYMBOL_BLOCKLIST:
            failed.append({"symbol": sym, "reason": f"blocklist: {SYMBOL_BLOCKLIST[sym]}"})
            continue
        cands = by_sym.get(sym, [])
        if not cands:
            failed.append({"symbol": sym, "reason": "tidak ada protokol DefiLlama dengan ticker ini"})
            continue

        if sym in SYMBOL_OVERRIDES:
            slug = SYMBOL_OVERRIDES[sym]
            pick = next((c for c in cands if c.get("slug") == slug), None)
            if pick is None:
                failed.append({"symbol": sym, "reason": f"override slug '{slug}' tak ditemukan"})
                continue
        else:
            # kanonik = TVL sekarang terbesar (tie-break: nama terpendek)
            pick = max(cands, key=lambda c: (c.get("tvl") or 0.0, -len(c.get("name", ""))))

        cat = pick.get("category") or ""
        if pick.get("isParentProtocol"):
            failed.append({"symbol": sym, "reason": f"protokol induk agregat ({pick.get('name')})"})
            continue
        if cat in EXCLUDE_CATEGORIES:
            failed.append({"symbol": sym, "reason": f"kategori dikecualikan: {cat} ({pick.get('name')})"})
            continue

        mapped.append({
            "symbol": sym,
            "binance_pair": f"{sym}USDT",
            "slug": pick.get("slug"),
            "name": pick.get("name"),
            "category": cat,
            "chains": pick.get("chains") or ([pick["chain"]] if pick.get("chain") else []),
            "gecko_id": pick.get("gecko_id"),
            "tvl_now": pick.get("tvl"),
            "n_ticker_collisions": len(cands),
            "collision_names": [c.get("name") for c in cands] if len(cands) > 1 else [],
        })

    meta = {
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_binance_usdt_bases": len(bases),
        "n_protocols_defillama": len(prot),
        "n_mapped": len(mapped),
        "n_failed": len(failed),
        "note": ("Universe dibekukan. HISTORY >=180 hari & unduh sukses divalidasi "
                 "di fetch (lihat data_ok per protokol setelah unduh). Kategori "
                 f"dikecualikan: {sorted(EXCLUDE_CATEGORIES)}."),
        "mapped": mapped,
        "failed": failed,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


# ─────────────────────────────────────────────────────────────
# Unduh deret per protokol
# ─────────────────────────────────────────────────────────────

def _chart_to_pairs(chart) -> list[list]:
    """Normalkan [[ts,val],...] atau [{date,..},...] -> [[ts_int, float]]."""
    out = []
    if not chart:
        return out
    for row in chart:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            ts, v = row[0], row[1]
        elif isinstance(row, dict):
            ts = row.get("date")
            v = row.get("totalLiquidityUSD", row.get("tvl", row.get("value")))
        else:
            continue
        try:
            out.append([int(float(ts)), float(v)])
        except (TypeError, ValueError):
            continue
    return out


def fetch_protocol(rec: dict, refresh: bool) -> dict:
    sym = rec["symbol"]
    slug = rec["slug"]
    out_path = os.path.join(RAW_DIR, f"{sym}.json")
    if os.path.exists(out_path) and not refresh:
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"symbol": sym, "status": "cache", "tvl_days": d.get("tvl_days", 0),
                    "fee_days": d.get("fee_days", 0)}
        except Exception:
            pass

    pd_ = _llama_get(f"{LLAMA}/protocol/{slug}")
    if not pd_:
        return {"symbol": sym, "status": "gagal /protocol", "tvl_days": 0, "fee_days": 0}

    tvl = _chart_to_pairs(pd_.get("tvl"))
    # tokens[] / tokensInUsd[] : [{date, tokens:{SYM:amt}}] -> {ts: {SYM:amt}}
    def _tokmap(key):
        m = {}
        for row in pd_.get(key) or []:
            try:
                m[int(float(row["date"]))] = {k: float(v) for k, v in (row.get("tokens") or {}).items()}
            except (TypeError, ValueError, KeyError):
                continue
        return m

    fees = _llama_get(f"{LLAMA}/summary/fees/{slug}?dataType=dailyFees")
    rev = _llama_get(f"{LLAMA}/summary/fees/{slug}?dataType=dailyRevenue")
    dex = _llama_get(f"{LLAMA}/summary/dexs/{slug}")

    rec_out = {
        "symbol": sym, "slug": slug, "name": rec["name"], "category": rec["category"],
        "chains": rec["chains"], "gecko_id": rec.get("gecko_id"),
        "mcap_now": pd_.get("mcap"),
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tvl_usd": tvl,
        "tokens_native": {str(k): v for k, v in _tokmap("tokens").items()},
        "tokens_usd": {str(k): v for k, v in _tokmap("tokensInUsd").items()},
        "daily_fees": _chart_to_pairs((fees or {}).get("totalDataChart")),
        "daily_revenue": _chart_to_pairs((rev or {}).get("totalDataChart")),
        "daily_dex_volume": _chart_to_pairs((dex or {}).get("totalDataChart")),
    }
    rec_out["tvl_days"] = len(tvl)
    rec_out["fee_days"] = len(rec_out["daily_fees"])
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec_out, f)
    return {"symbol": sym, "status": "unduh", "tvl_days": rec_out["tvl_days"],
            "fee_days": rec_out["fee_days"]}


# ─────────────────────────────────────────────────────────────
# Data level-chain (TVL chain + stablecoin), dedup lintas protokol
# ─────────────────────────────────────────────────────────────

def fetch_chain(chain: str, refresh: bool) -> str:
    os.makedirs(CHAIN_DIR, exist_ok=True)
    safe = chain.replace("/", "_")
    path = os.path.join(CHAIN_DIR, f"{safe}.json")
    if os.path.exists(path) and not refresh:
        return "cache"
    ctvl = _llama_get(f"{LLAMA}/v2/historicalChainTvl/{chain}")
    scharts = _llama_get(f"{STABLES_API}/stablecoincharts/{chain}")
    stbl = []
    for row in scharts or []:
        try:
            ts = int(float(row["date"]))
            tot = row.get("totalCirculatingUSD") or {}
            stbl.append([ts, float(sum(tot.values()))])
        except (TypeError, ValueError, KeyError):
            continue
    if not ctvl and not stbl:
        return "kosong"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"chain": chain, "chain_tvl_usd": _chart_to_pairs(ctvl),
                   "stablecoin_usd": stbl}, f)
    return "unduh"


# ─────────────────────────────────────────────────────────────
# Harga Binance untuk simbol yang tak ada di .cache_history/
# ─────────────────────────────────────────────────────────────

def fetch_price_if_missing(sym: str, refresh: bool) -> str:
    pair = f"{sym}USDT"
    if os.path.exists(os.path.join(HIST_DIR, f"{pair}_1d.parquet")):
        return "cache_history"
    os.makedirs(PRICE_DIR, exist_ok=True)
    path = os.path.join(PRICE_DIR, f"{pair}_1d.parquet")
    if os.path.exists(path) and not refresh:
        return "cache_defi"
    rows, cur, guard = [], START_MS, 0
    now_ms = int(time.time() * 1000)
    while cur < now_ms and guard < 200:
        guard += 1
        try:
            raw = scr._get("/api/v3/klines", {"symbol": pair, "interval": "1d",
                                              "startTime": cur, "limit": 1000})
        except Exception:
            return "gagal"
        if not raw:
            break
        rows.extend(raw)
        if len(raw) < 1000:
            break
        nxt = raw[-1][0] + MS_DAY
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.2)
    if not rows:
        return "kosong"
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "ct", "qav", "trades", "tbb", "tbq", "ig"])
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df = (df.set_index("time")[["open", "high", "low", "close", "volume"]]
            .loc[~df.set_index("time").index.duplicated(keep="first")].sort_index().iloc[:-1])
    if len(df) < 60:
        return "pendek"
    df.to_parquet(path)
    return f"unduh ({len(df)})"


# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Unduh data fundamental DefiLlama")
    ap.add_argument("--refresh", action="store_true", help="Unduh ulang walau file sudah ada")
    ap.add_argument("--universe-only", action="store_true", help="Cuma bangun & laporkan pemetaan")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    for d in (DEFI_DIR, RAW_DIR, CHAIN_DIR, PRICE_DIR):
        os.makedirs(d, exist_ok=True)

    print("=" * 74)
    print("FETCH DEFI — data fundamental protokol (HIPOTESIS_DEFI.md, kategori data ke-6)")
    print("=" * 74)

    meta = build_universe(args.refresh)
    print(f"\nPEMETAAN protokol -> Binance USDT spot:")
    print(f"  base asset Binance   : {meta['n_binance_usdt_bases']}")
    print(f"  protokol DefiLlama   : {meta['n_protocols_defillama']}")
    print(f"  BERHASIL dipetakan   : {meta['n_mapped']}")
    print(f"  GAGAL dipetakan      : {meta['n_failed']}")
    # rincian alasan gagal
    from collections import Counter
    rc = Counter()
    for f in meta["failed"]:
        key = f["reason"].split(":")[0].split("(")[0].strip()
        rc[key] += 1
    for k, n in rc.most_common():
        print(f"     - {k}: {n}")
    n_collide = sum(1 for m in meta["mapped"] if m["n_ticker_collisions"] > 1)
    print(f"  dipetakan via tabrakan ticker (dipilih TVL terbesar): {n_collide}")
    print(f"  -> {os.path.join(DEFI_DIR, 'universe_defi.json')}")

    if args.universe_only:
        print("\n--universe-only: berhenti sebelum unduh deret.")
        return

    mapped = meta["mapped"]
    chains = sorted({c for m in mapped for c in m["chains"]})
    print(f"\n-> Unduh {len(mapped)} protokol + {len(chains)} chain + harga yang hilang ...", flush=True)

    t0 = time.time()

    # 1) chain data
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_chain, c, args.refresh): c for c in chains}
        for fut in cf.as_completed(futs):
            done += 1
            c = futs[fut]
            print(f"   chain [{done}/{len(chains)}] {c:<22} {fut.result()}", flush=True)

    # 2) protokol
    done, ok_hist = 0, 0
    N = len(mapped)
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_protocol, m, args.refresh): m for m in mapped}
        for fut in cf.as_completed(futs):
            r = fut.result()
            done += 1
            enough = (r["tvl_days"] >= MIN_HISTORY_DAYS) or (r["fee_days"] >= MIN_HISTORY_DAYS)
            ok_hist += enough
            eta = ""
            if done >= 8:
                rem = (time.time() - t0) / done * (N - done)
                mm, ss = divmod(int(rem), 60)
                eta = f"  | ETA {mm:02d}:{ss:02d}"
            flag = "" if enough else "  <180h!"
            print(f"   prot [{done}/{N}] {r['symbol']:<12} {r['status']:<14} "
                  f"tvl={r['tvl_days']:>4}d fee={r['fee_days']:>4}d{flag}{eta}", flush=True)

    # 3) harga yang hilang
    done, n_missing = 0, 0
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_price_if_missing, m["symbol"], args.refresh): m for m in mapped}
        for fut in cf.as_completed(futs):
            done += 1
            res = fut.result()
            if res not in ("cache_history", "cache_defi"):
                n_missing += 1
                print(f"   harga [{done}/{N}] {futs[fut]['symbol']:<12} {res}", flush=True)

    dt = time.time() - t0
    print("\n" + "=" * 74)
    print(f"  SELESAI dalam {dt/60:.1f} menit")
    print(f"  Protokol dipetakan        : {N}")
    print(f"  Protokol >=180 hari data   : {ok_hist}  (kandidat efektif universe)")
    print(f"  Chain diunduh             : {len(chains)}")
    print(f"  Harga di-fetch tambahan   : {n_missing}")
    print(f"  Lokasi                    : {DEFI_DIR}")
    print("=" * 74)
    print("  Lanjut: python defi_test.py")


if __name__ == "__main__":
    main()
