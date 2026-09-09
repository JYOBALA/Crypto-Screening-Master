"""
short_scan.py — Kandidat SHORT: cermin sistem skor long (scoring.py), bobot
SAMA (Volume 25 | Stoch RSI 20 | Fibonacci 20 | S/R 20 | Pattern 15).

PERINGATAN WAJIB (baca sebelum pakai output modul ini):
Sisi LONG sudah diuji 5x (backtest komposit, faktor tunggal, mekanik
entry-acak, detektor regime, aliran order) dan semuanya null -- lihat
RINGKASAN_AKHIR.md. Sisi SHORT di modul ini BELUM PERNAH DIUJI SAMA SEKALI.
Ini murni pembangkit ide untuk dicek manual lewat cara yang sama seperti
long (cermin logika, bukan logika baru yang diarang), TAPI bukti dasarnya
JAUH lebih lemah daripada long -- long minimal sudah diukur dan gagal;
short belum diukur sama sekali. JANGAN PERNAH menampilkan hasil modul ini
seolah tervalidasi. Setiap pemanggil WAJIB menyertakan disclaimer ini di
output (ide_trade.xlsx/Telegram) -- lihat konstanta VALIDASI_SHORT di bawah.

Dua hal yang BUKAN bagian dari long dan wajib dicek sebelum menghasilkan
kandidat short:
  1. Ketersediaan pasar perpetual futures (check_perp_availability) --
     tidak semua koin di universe_frozen.json bisa di-short.
  2. Estimasi biaya funding (get_last_funding_rate) -- INFORMASI saja,
     TIDAK masuk skor (funding bisa berubah tiap 8 jam, skor dihitung
     sekali per hari).
"""
from __future__ import annotations

import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as ta      # noqa: E402
import accumulation as acc   # noqa: E402  (detect_trading_range/wyckoff_events -- UTAD, sudah ada)
import scoring                # noqa: E402  (grade() -- bucket skor 0-100, sama utk long/short)
import screener as scr       # noqa: E402  (_get -- dipakai jg utk /fapi/*, base URL beda lihat di bawah)

VALIDASI_LONG = "long: 5 uji null (RINGKASAN_AKHIR.md)"
VALIDASI_SHORT = "short: belum pernah diuji"

# /fapi/* ada di host BERBEDA dari spot (BASE_CANDIDATES di screener.py).
# Tidak dipakai untuk data harga (itu tetap dari spot data-api.binance.vision
# via screener.fetch_for_mode) -- HANYA utk 2 hal yang memang cuma ada di
# futures API: daftar simbol yang punya perp, dan funding rate.
#
# CATATAN: fapi.binance.com TERBLOKIR di jaringan dev (sama seperti
# api.binance.com sebelum diperbaiki utk spot -- lihat CLAUDE.md), dan TIDAK
# ada padanan data-api.binance.vision utk futures yg diketahui. Modul ini
# gagal aman (semua simbol dianggap TIDAK punya perp) kalau host ini tidak
# terjangkau -- fungsinya TIDAK bisa diverifikasi penuh dari mesin dev ini,
# WAJIB dicek ulang setelah deploy ke VPS (lihat DEPLOY.md).
FUTURES_BASE = "https://fapi.binance.com"


def _futures_get(path: str, params: dict | None = None):
    import requests
    r = requests.get(FUTURES_BASE + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────────────────
# a. Ketersediaan perpetual futures
# ─────────────────────────────────────────────────────────────

def check_perp_availability(symbols: list[str]) -> dict[str, bool]:
    """{simbol: True/False punya perp USDT-M aktif}. Gagal jaringan -> semua
    False (aman: TIDAK mengizinkan short kalau tidak yakin ada perp-nya)."""
    try:
        info = _futures_get("/fapi/v1/exchangeInfo")
        perp_syms = {s["symbol"] for s in info.get("symbols", [])
                     if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
                     and s.get("status") == "TRADING"}
    except Exception:
        return {s: False for s in symbols}
    return {s: (s in perp_syms) for s in symbols}


def get_last_funding_rate(symbol: str) -> float | None:
    """Funding rate 8-jam TERAKHIR (persen), INFORMASI SAJA -- lihat docstring modul."""
    try:
        rows = _futures_get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
        if not rows:
            return None
        return round(float(rows[-1]["fundingRate"]) * 100, 4)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# 1. VOLUME short (max 25) -- cermin score_volume: breakDOWN, bukan breakout
# ─────────────────────────────────────────────────────────────

def score_volume_short(df: pd.DataFrame) -> dict:
    v, c = df["volume"], df["close"]
    vma = v.rolling(20).mean()
    last_v, last_vma = float(v.iloc[-1]), float(vma.iloc[-1])
    ratio = last_v / last_vma if last_vma > 0 else 0.0

    o = ta.obv(c, v)
    obv_slope = ta.slope_pct(o, 20)

    prior_low = float(df["low"].iloc[-21:-1].min())
    is_breakdown = float(c.iloc[-1]) < prior_low

    recent_low = float(c.iloc[-20:].min())
    in_bounce = float(c.iloc[-1]) > recent_low * 1.03
    dry_up = float(v.iloc[-5:].mean()) < last_vma * 0.8

    rng_bar = float(df["high"].iloc[-1] - df["low"].iloc[-1])
    close_pos = ((float(c.iloc[-1]) - float(df["low"].iloc[-1])) / rng_bar) if rng_bar > 0 else 0.5
    reversal_bar = (float(c.iloc[-1]) < float(df["open"].iloc[-1])
                    and close_pos <= 0.4 and ratio >= 1.5)

    price_slope = ta.slope_pct(c, 20)
    vol_slope = ta.slope_pct(vma, 20)
    # cermin: harga TURUN tapi volume+OBV justru menguat -> distribusi tersembunyi, jangan chase
    pos_div = price_slope < -0.3 and vol_slope < -1.5 and obv_slope > 0

    notes, veto = [], False
    if pos_div:
        pts, notes = 0, ["divergensi (harga turun, volume & OBV naik) - kemungkinan akumulasi tersembunyi"]
        veto = True
    elif is_breakdown and ratio < 1.0:
        pts, notes = 0, [f"breakdown tapi volume tipis ({ratio:.2f}x MA20) - risiko fake breakdown"]
        veto = True
    elif is_breakdown and ratio >= 2.0 and obv_slope < 0:
        pts, notes = 25, [f"breakdown volume {ratio:.2f}x MA20 + OBV turun"]
    elif is_breakdown and ratio >= 1.5:
        pts, notes = 22, [f"breakdown volume {ratio:.2f}x MA20"]
    elif reversal_bar and in_bounce:
        pts, notes = 22, [f"candle reversal bearish dengan volume {ratio:.2f}x MA20 setelah bounce"]
    elif ratio >= 1.2 and vol_slope > 0 and obv_slope < 0:
        pts, notes = 20, [f"volume di atas MA20 ({ratio:.2f}x) & OBV melemah"]
    elif in_bounce and dry_up and obv_slope <= 0.5:
        pts, notes = 15, ["volume dry-up saat bounce (pelemahan sehat utk short)"]
    elif ratio >= 0.8:
        pts, notes = 8, [f"volume normal ({ratio:.2f}x MA20)"]
    else:
        pts, notes = 5, [f"volume sepi ({ratio:.2f}x MA20)"]

    return {"points": pts, "max": 25, "veto": veto, "notes": notes,
            "vol_ratio": round(ratio, 2), "obv_slope": round(obv_slope, 2),
            "breakdown": bool(is_breakdown)}


# ─────────────────────────────────────────────────────────────
# 2. STOCH RSI short (max 20) -- cermin: cross DOWN dari zona >80
# ─────────────────────────────────────────────────────────────

def _cross_down(k: pd.Series, d: pd.Series, lookback: int = 3) -> bool:
    for i in range(1, lookback + 1):
        if len(k) <= i:
            break
        if k.iloc[-i] < d.iloc[-i] and k.iloc[-i - 1] >= d.iloc[-i - 1]:
            return True
    return False


def _bearish_divergence_sr(close: pd.Series, k: pd.Series, lb: int = 30) -> bool:
    c, kk = close.tail(lb), k.tail(lb)
    if len(c) < lb:
        return False
    half = lb // 2
    p1, p2 = c.iloc[:half].max(), c.iloc[half:].max()
    k1, k2 = kk.iloc[:half].max(), kk.iloc[half:].max()
    return bool(p2 > p1 * 1.005 and k2 < k1 * 0.95)


def score_stochrsi_short(df_bias: pd.DataFrame, df_htf: pd.DataFrame) -> dict:
    s = ta.stoch_rsi(df_bias["close"])
    k, d = s["k"].dropna(), s["d"].dropna()
    if len(k) < 5:
        return {"points": 0, "max": 20, "veto": True,
                "notes": ["data tidak cukup"], "k": None, "k_htf": None}

    k_now, d_now = float(k.iloc[-1]), float(d.iloc[-1])
    cross = _cross_down(k, d)
    zone_max = float(k.iloc[-4:].max())

    k_htf, htf_turning = None, False
    if df_htf is not None and len(df_htf) > 25:
        sh = ta.stoch_rsi(df_htf["close"])["k"].dropna()
        if len(sh) >= 3:
            k_htf = float(sh.iloc[-1])
            htf_turning = k_htf < float(sh.iloc[-2]) and k_htf > 60

    div = _bearish_divergence_sr(df_bias["close"], s["k"])

    notes, veto = [], False
    if k_now < 20:
        pts, notes, veto = 0, [f"Stoch RSI sudah oversold ({k_now:.0f}) - telat utk short"], True
    elif cross and zone_max > 80 and htf_turning:
        pts = 20
        notes = [f"cross down dari overbought ({zone_max:.0f}) + HTF berbalik turun ({k_htf:.0f})"]
    elif cross and zone_max > 80:
        pts = 15
        notes = [f"cross down dari zona overbought ({zone_max:.0f})"]
    elif div and k_now > 50:
        pts = 12
        notes = ["bearish divergence terdeteksi"]
    elif cross and zone_max > 55:
        pts = 8
        notes = [f"cross down dari zona tengah ({zone_max:.0f})"]
    elif k_now > 75 and k_now < float(k.iloc[-2]):
        pts = 10
        notes = [f"overbought ({k_now:.0f}) mulai berbalik turun, belum cross"]
    elif k_now < float(k.iloc[-2]) and k_now > 35 and k_htf is not None and k_htf > 55:
        pts = 7
        notes = [f"K turun dari zona tidak jenuh ({k_now:.0f}), HTF masih tinggi ({k_htf:.0f})"]
    elif k_now < float(k.iloc[-2]) and k_now > 35:
        pts = 5
        notes = [f"K berbalik turun ({k_now:.0f}), HTF belum mendukung"]
    else:
        pts = 3
        notes = [f"tidak ada sinyal (K={k_now:.0f}, D={d_now:.0f})"]

    return {"points": pts, "max": 20, "veto": veto, "notes": notes,
            "k": round(k_now, 1), "k_htf": round(k_htf, 1) if k_htf is not None else None}


# ─────────────────────────────────────────────────────────────
# 3. FIBONACCI short (max 20) -- cermin: retracement NAIK ke 0.5-0.618
#    dari swing TURUN terakhir (last_impulse_swing_down)
# ─────────────────────────────────────────────────────────────

def score_fibonacci_short(df: pd.DataFrame, zones: list[dict]) -> dict:
    swing = ta.last_impulse_swing_down(df)
    if not swing:
        return {"points": 0, "max": 20, "veto": True,
                "notes": ["struktur swing turun tidak jelas - tidak bisa tarik Fibonacci"],
                "fib": None, "retr": None, "swing": None}

    price = float(df["close"].iloc[-1])
    lv = ta.fib_levels(swing["low"], swing["high"])
    rng = swing["high"] - swing["low"]
    retr_up = (price - swing["low"]) / rng if rng > 0 else np.nan   # 0=di low, 1=kembali ke high

    e50 = float(ta.ema(df["close"], 50).iloc[-1])
    conf = []
    for z in zones:
        if ta.in_zone(price, z, buffer_pct=1.5) and z["touches"] >= 2:
            conf.append(f"S/R {z['touches']}x sentuhan")
            break
    if abs(price - e50) / price * 100 <= 2.5:
        conf.append("EMA50")

    notes, veto = [], False
    if retr_up is None or np.isnan(retr_up):
        pts, veto, notes = 0, True, ["retracement tidak terhitung"]
    elif retr_up > 0.85:
        pts, veto = 0, True
        notes = [f"harga sudah kembali ke atas swing high (retr {retr_up:.3f}) - struktur turun batal"]
    elif 0.47 <= retr_up <= 0.65 and conf:
        pts = 20
        notes = [f"golden zone bounce (retr {retr_up:.3f}) + confluence: {', '.join(conf)}"]
    elif 0.47 <= retr_up <= 0.65:
        pts = 15
        notes = [f"golden zone bounce (retr {retr_up:.3f}), tanpa confluence"]
    elif 0.65 < retr_up <= 0.85:
        wick = float(df["high"].iloc[-1] - df["close"].iloc[-1])
        body = abs(float(df["close"].iloc[-1] - df["open"].iloc[-1])) + 1e-12
        pts = 12 if wick > body else 9
        notes = [f"deep bounce (retr {retr_up:.3f}), area 0.786"]
    elif 0.3 <= retr_up < 0.47:
        pts = 8
        notes = [f"bounce dangkal (retr {retr_up:.3f}) - downtrend kuat"]
    elif retr_up < 0.3:
        pts = 5
        notes = [f"harga dekat swing low (retr {retr_up:.3f}) - entry telat"]
    else:
        pts = 5
        notes = [f"retr {retr_up:.3f}"]

    return {"points": pts, "max": 20, "veto": veto, "notes": notes,
            "fib": lv, "retr": round(float(retr_up), 3) if retr_up == retr_up else None,
            "swing": swing}


# ─────────────────────────────────────────────────────────────
# 4. SUPPORT & RESISTANCE short (max 20) -- cermin: harga di resistance,
#    atau baru gagal menembusnya
# ─────────────────────────────────────────────────────────────

def score_sr_short(df: pd.DataFrame, zones: list[dict]) -> dict:
    price = float(df["close"].iloc[-1])
    sup, res = ta.nearest_zones(zones, price)
    struct = ta.market_structure(df)

    # cermin breakdown: 3 candle terakhir close di ATAS zona resistance yang sebelumnya valid
    breakout_up = False
    for z in zones:
        if z["last_touch_bars_ago"] <= 25 and z["touches"] >= 2:
            prev_below = float(df["close"].iloc[-10:-3].min()) < z["low"]
            now_above = price > z["high"] * 1.015
            if prev_below and now_above:
                breakout_up = True
                break

    notes, veto = [], False
    if breakout_up:
        pts, veto = 0, True
        notes = ["baru saja breakout ke atas zona resistance - tesis short batal"]
    elif res and ta.in_zone(price, res, 1.5):
        flip = "H" in res["types"] and "L" in res["types"]
        if res["touches"] >= 3 or flip:
            pts = 20
            notes = [f"di zona resistance kuat ({res['touches']}x sentuhan"
                     + (", flip zone)" if flip else ")")]
        else:
            pts = 15
            notes = [f"di zona resistance ({res['touches']}x sentuhan)"]
    elif res and (price - res["low"]) / price * 100 >= -3.0 and price < res["low"]:
        pts = 12
        notes = [f"dekat resistance ({(res['low'] - price) / price * 100:.1f}% di bawah)"]
    elif res:
        pts = 6
        notes = ["di tengah range, tidak dekat level"]
    else:
        pts = 4
        notes = ["tidak ada resistance jelas di atas - risiko tinggi"]

    if struct["trend"] == "uptrend":
        pts = max(0, pts - 5)
        notes.append("struktur uptrend (HH/HL)")

    dist_sup = ((price - sup["level"]) / price * 100) if sup else None
    return {"points": pts, "max": 20, "veto": veto, "notes": notes,
            "support": sup, "resistance": res, "trend": struct["trend"],
            "last_swing_high": struct["last_swing_high"],
            "dist_to_sup_pct": round(dist_sup, 2) if dist_sup is not None else None}


# ─────────────────────────────────────────────────────────────
# 5. CHART PATTERN short (max 15) -- UTAD (accumulation.py, sudah ada) +
#    detect_bearish_pattern() (indicators.py, sudah ada: Rising Wedge/Double Top)
# ─────────────────────────────────────────────────────────────

def score_pattern_short(df: pd.DataFrame) -> dict:
    utad = False
    rng = acc.detect_trading_range(df)
    if rng:
        ev = acc.wyckoff_events(df, rng)
        utad = "UTAD" in ev
    bear_name = ta.detect_bearish_pattern(df)

    notes = []
    if utad and bear_name:
        pts = 15
        notes = [f"UTAD (distribusi) terdeteksi + {bear_name}"]
    elif utad:
        pts = 14
        notes = ["UTAD (distribusi) terdeteksi"]
    elif bear_name == "Double Top":
        pts = 11
        notes = ["Double Top terdeteksi"]
    elif bear_name == "Rising Wedge":
        pts = 9
        notes = ["Rising Wedge terdeteksi"]
    else:
        pts = 0
        notes = ["tidak ada pola bearish jelas"]

    return {"points": pts, "max": 15, "veto": False, "notes": notes,
            "pattern": (f"UTAD+{bear_name}" if (utad and bear_name)
                        else ("UTAD" if utad else bear_name)),
            "utad": utad, "bearish_pattern": bear_name}


# ─────────────────────────────────────────────────────────────
# Rencana trade short: entry, SL DI ATAS struktur, TP DI BAWAH
# ─────────────────────────────────────────────────────────────

def build_short_trade_plan(df, fib_res, sr_res, capital, risk_pct, size_mult):
    price = float(df["close"].iloc[-1])
    fib = fib_res.get("fib")
    a = float(ta.atr(df).iloc[-1])

    if fib:
        gz_hi, gz_lo = fib["0.5"], fib["0.618"]
        if price >= gz_lo * 0.99:
            entry = price
            entry_style = "harga pasar -- sudah di/di atas golden zone, tak perlu tunggu bounce"
        else:
            entry = (gz_hi + gz_lo) / 2
            prem = (entry - price) / price * 100
            entry_style = (f"limit -- tunggu bounce ~{prem:.0f}% ke golden zone "
                           f"{gz_lo:.6g}-{gz_hi:.6g}")
    else:
        entry = price
        entry_style = "harga pasar -- tak ada swing Fibonacci acuan, pakai harga terakhir"

    cands = []
    if sr_res.get("last_swing_high"):
        cands.append(sr_res["last_swing_high"] * 1.015)
    if sr_res.get("resistance"):
        cands.append(sr_res["resistance"]["high"] * 1.015)
    if fib:
        cands.append(fib["0.786"] * 1.01)
    cands = [x for x in cands if x > entry * 1.005]
    sl = min(cands) if cands else entry + 1.5 * a
    if (sl - entry) / entry > 0.20:
        sl = entry + 2.0 * a

    sup_level = sr_res["support"]["level"] if sr_res.get("support") else None
    ext_1272 = ext_1618 = None
    if fib_res.get("swing"):
        sw = fib_res["swing"]
        rng = sw["high"] - sw["low"]
        ext_1272 = sw["low"] - 0.272 * rng
        ext_1618 = sw["low"] - 0.618 * rng

    down_levels = sorted((x for x in (sup_level, ext_1272, ext_1618)
                          if x is not None and x < entry * 0.98), reverse=True)
    if down_levels:
        tp1 = down_levels[0]
        beyond = [x for x in down_levels if x < tp1 * 0.99]
        tp2 = beyond[0] if beyond else tp1 - (entry - tp1)
    else:
        tp1 = entry - 2 * (sl - entry)
        tp2 = entry - 3.5 * (sl - entry)
    tp2 = min(tp2, tp1 - 0.5 * (entry - tp1))

    risk_per_unit = sl - entry
    rr1 = (entry - tp1) / risk_per_unit if risk_per_unit > 0 else 0
    rr2 = (entry - tp2) / risk_per_unit if risk_per_unit > 0 else 0

    risk_amount = capital * (risk_pct / 100) * size_mult
    sl_dist = risk_per_unit / entry if entry else 0
    pos_size = risk_amount / sl_dist if sl_dist > 0 else 0

    return {
        "entry": entry, "entry_style": entry_style, "sl": sl, "tp1": tp1, "tp2": tp2,
        "sl_pct": round(sl_dist * 100, 2),
        "tp1_pct": round(-(entry - tp1) / entry * 100, 2),
        "tp2_pct": round(-(entry - tp2) / entry * 100, 2),
        "rr1": round(rr1, 2), "rr2": round(rr2, 2),
        "risk_amount": round(risk_amount, 2),
        "position_size": round(pos_size, 2),
    }


# ─────────────────────────────────────────────────────────────
# Orkestrasi -- bentuk return SEMIRIP mungkin dgn scoring.evaluate()
# ─────────────────────────────────────────────────────────────

def evaluate_short(symbol: str, df_bias: pd.DataFrame, df_htf: pd.DataFrame,
                    regime: dict, cfg: dict) -> dict | None:
    """Cermin scoring.evaluate(). Veto tambahan yg TIDAK ada di long: regime
    BTC HIJAU (mirror dari MERAH mem-veto long -- jangan short saat tren naik
    kuat)."""
    if len(df_bias) < cfg.get("min_bars_analysis", 120):
        return None

    zones = ta.sr_zones(df_bias, order=cfg["pivot_order"], tol_pct=cfg["sr_tolerance_pct"])

    vol = score_volume_short(df_bias)
    st = score_stochrsi_short(df_bias, df_htf)
    fb = score_fibonacci_short(df_bias, zones)
    sr = score_sr_short(df_bias, zones)
    pat = score_pattern_short(df_bias)

    total = vol["points"] + st["points"] + fb["points"] + sr["points"] + pat["points"]

    vetoes = []
    if regime["status"] == "HIJAU":
        vetoes.append("BTC status HIJAU (cermin veto MERAH di long) - jangan short saat tren naik kuat")
    for name, comp in [("Volume", vol), ("StochRSI", st), ("Fibonacci", fb), ("S/R", sr)]:
        if comp["veto"]:
            vetoes.append(f"{name}: {comp['notes'][0]}")

    size_mult = regime["size_mult"]
    plan = build_short_trade_plan(df_bias, fb, sr, cfg["capital"], cfg["risk_pct"], size_mult)

    if plan["rr1"] < cfg["min_rr"]:
        vetoes.append(f"R:R ke TP1 hanya 1:{plan['rr1']} (min 1:{cfg['min_rr']})")
    if plan["rr1"] > cfg.get("max_plausible_rr", 15.0):
        vetoes.append(f"R:R 1:{plan['rr1']} tidak masuk akal - level swing/resistance "
                       "kemungkinan rusak, periksa chart manual")

    return {
        "symbol": symbol, "side": "SHORT",
        "price": float(df_bias["close"].iloc[-1]),
        "total": total, "grade": scoring.grade(total),   # bucket 0-100 sama, TAPI belum divalidasi utk short
        "vetoed": len(vetoes) > 0, "veto_reasons": vetoes,
        "s_volume": vol["points"], "s_stochrsi": st["points"],
        "s_fib": fb["points"], "s_sr": sr["points"], "s_pattern": pat["points"],
        "vol_ratio": vol["vol_ratio"],
        "stochrsi_k": st["k"], "stochrsi_htf": st["k_htf"],
        "fib_retr": fb["retr"],
        "trend": sr["trend"],
        "pattern": pat["pattern"],
        "dist_to_sup_pct": sr["dist_to_sup_pct"],
        "plan": plan,
        "funding_rate_last_8h_pct": get_last_funding_rate(symbol),
        "validasi": VALIDASI_SHORT,
        "notes": {"volume": vol["notes"], "stochrsi": st["notes"],
                  "fibonacci": fb["notes"], "sr": sr["notes"], "pattern": pat["notes"]},
    }
