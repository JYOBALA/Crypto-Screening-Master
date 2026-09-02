"""
scoring.py — Implementasi sistem skor 100 poin + veto rules dari SOP.

Bobot: Volume 25 | Stoch RSI 20 | Fibonacci 20 | S/R 20 | Pattern 15
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import indicators as ta


# ─────────────────────────────────────────────────────────────
# 1. VOLUME  (max 25)
# ─────────────────────────────────────────────────────────────

def score_volume(df: pd.DataFrame) -> dict:
    v = df["volume"]
    c = df["close"]
    vma = v.rolling(20).mean()
    last_v, last_vma = float(v.iloc[-1]), float(vma.iloc[-1])
    ratio = last_v / last_vma if last_vma > 0 else 0.0

    o = ta.obv(c, v)
    obv_slope = ta.slope_pct(o, 20)

    prior_high = float(df["high"].iloc[-21:-1].max())
    is_breakout = float(c.iloc[-1]) > prior_high

    recent_high = float(c.iloc[-20:].max())
    in_pullback = float(c.iloc[-1]) < recent_high * 0.97
    dry_up = float(v.iloc[-5:].mean()) < last_vma * 0.8

    # candle reversal: bullish, close di sepertiga atas range, volume ekspansi
    rng_bar = float(df["high"].iloc[-1] - df["low"].iloc[-1])
    close_pos = ((float(c.iloc[-1]) - float(df["low"].iloc[-1])) / rng_bar) if rng_bar > 0 else 0.5
    reversal_bar = (float(c.iloc[-1]) > float(df["open"].iloc[-1])
                    and close_pos >= 0.6 and ratio >= 1.5)

    price_slope = ta.slope_pct(c, 20)
    vol_slope = ta.slope_pct(vma, 20)
    neg_div = price_slope > 0.3 and vol_slope < -1.5 and obv_slope < 0

    notes, veto = [], False
    if neg_div:
        pts, notes = 0, ["divergensi negatif (harga naik, volume & OBV turun)"]
        veto = True
    elif is_breakout and ratio < 1.0:
        pts, notes = 0, [f"breakout tapi volume tipis ({ratio:.2f}x MA20) - risiko fakeout"]
        veto = True
    elif is_breakout and ratio >= 2.0 and obv_slope > 0:
        pts, notes = 25, [f"breakout volume {ratio:.2f}x MA20 + OBV naik"]
    elif is_breakout and ratio >= 1.5:
        pts, notes = 22, [f"breakout volume {ratio:.2f}x MA20"]
    elif reversal_bar and in_pullback:
        pts, notes = 22, [f"candle reversal dengan volume {ratio:.2f}x MA20 setelah pullback"]
    elif ratio >= 1.2 and vol_slope > 0 and obv_slope > 0:
        pts, notes = 20, [f"volume di atas MA20 ({ratio:.2f}x) & OBV menguat"]
    elif in_pullback and dry_up and obv_slope >= -0.5:
        pts, notes = 15, ["volume dry-up saat pullback (koreksi sehat)"]
    elif ratio >= 0.8:
        pts, notes = 8, [f"volume normal ({ratio:.2f}x MA20)"]
    else:
        pts, notes = 5, [f"volume sepi ({ratio:.2f}x MA20)"]

    return {"points": pts, "max": 25, "veto": veto, "notes": notes,
            "vol_ratio": round(ratio, 2), "obv_slope": round(obv_slope, 2),
            "breakout": bool(is_breakout)}


# ─────────────────────────────────────────────────────────────
# 2. STOCH RSI  (max 20)
# ─────────────────────────────────────────────────────────────

def _cross_up(k: pd.Series, d: pd.Series, lookback: int = 3) -> bool:
    for i in range(1, lookback + 1):
        if len(k) <= i:
            break
        if k.iloc[-i] > d.iloc[-i] and k.iloc[-i - 1] <= d.iloc[-i - 1]:
            return True
    return False


def _bullish_divergence(close: pd.Series, k: pd.Series, lb: int = 30) -> bool:
    c, kk = close.tail(lb), k.tail(lb)
    if len(c) < lb:
        return False
    half = lb // 2
    p1, p2 = c.iloc[:half].min(), c.iloc[half:].min()
    k1 = kk.iloc[:half].min()
    k2 = kk.iloc[half:].min()
    return bool(p2 < p1 * 0.995 and k2 > k1 * 1.05)


def score_stochrsi(df_bias: pd.DataFrame, df_htf: pd.DataFrame) -> dict:
    s = ta.stoch_rsi(df_bias["close"])
    k, d = s["k"].dropna(), s["d"].dropna()
    if len(k) < 5:
        return {"points": 0, "max": 20, "veto": True,
                "notes": ["data tidak cukup"], "k": None, "k_htf": None}

    k_now, d_now = float(k.iloc[-1]), float(d.iloc[-1])
    cross = _cross_up(k, d)
    zone_min = float(k.iloc[-4:].min())

    k_htf = None
    htf_turning = False
    if df_htf is not None and len(df_htf) > 25:
        sh = ta.stoch_rsi(df_htf["close"])["k"].dropna()
        if len(sh) >= 3:
            k_htf = float(sh.iloc[-1])
            htf_turning = k_htf > float(sh.iloc[-2]) and k_htf < 40

    div = _bullish_divergence(df_bias["close"], s["k"])

    notes, veto = [], False
    if k_now > 80:
        pts = 0
        notes = [f"Stoch RSI sudah overbought ({k_now:.0f}) - telat"]
        veto = True
    elif cross and zone_min < 20 and htf_turning:
        pts = 20
        notes = [f"cross up dari oversold ({zone_min:.0f}) + HTF berbalik naik ({k_htf:.0f})"]
    elif cross and zone_min < 20:
        pts = 15
        notes = [f"cross up dari zona oversold ({zone_min:.0f})"]
    elif div and k_now < 50:
        pts = 12
        notes = ["bullish divergence terdeteksi"]
    elif cross and zone_min < 45:
        pts = 8
        notes = [f"cross up dari zona tengah ({zone_min:.0f})"]
    elif k_now < 25 and k_now > float(k.iloc[-2]):
        pts = 10
        notes = [f"oversold ({k_now:.0f}) mulai berbalik, belum cross"]
    elif k_now > float(k.iloc[-2]) and k_now < 65 and k_htf is not None and k_htf < 45:
        pts = 7
        notes = [f"K naik dari zona tidak jenuh ({k_now:.0f}), HTF masih rendah ({k_htf:.0f})"]
    elif k_now > float(k.iloc[-2]) and k_now < 65:
        pts = 5
        notes = [f"K berbalik naik ({k_now:.0f}), HTF belum mendukung"]
    else:
        pts = 3
        notes = [f"tidak ada sinyal (K={k_now:.0f}, D={d_now:.0f})"]

    return {"points": pts, "max": 20, "veto": veto, "notes": notes,
            "k": round(k_now, 1), "k_htf": round(k_htf, 1) if k_htf is not None else None}


# ─────────────────────────────────────────────────────────────
# 3. FIBONACCI  (max 20)
# ─────────────────────────────────────────────────────────────

def score_fibonacci(df: pd.DataFrame, zones: list[dict]) -> dict:
    swing = ta.last_impulse_swing(df)
    if not swing:
        return {"points": 0, "max": 20, "veto": True,
                "notes": ["struktur swing tidak jelas - tidak bisa tarik Fibonacci"],
                "fib": None, "retr": None, "swing": None}

    price = float(df["close"].iloc[-1])
    lv = ta.fib_levels(swing["low"], swing["high"])
    retr = ta.retracement_ratio(price, swing["low"], swing["high"])

    e50 = float(ta.ema(df["close"], 50).iloc[-1])
    conf = []
    for z in zones:
        if ta.in_zone(price, z, buffer_pct=1.5) and z["touches"] >= 2:
            conf.append(f"S/R {z['touches']}x sentuhan")
            break
    if abs(price - e50) / price * 100 <= 2.5:
        conf.append("EMA50")

    notes, veto = [], False
    if retr is None or np.isnan(retr):
        pts, veto, notes = 0, True, ["retracement tidak terhitung"]
    elif retr > 0.85:
        pts, veto = 0, True
        notes = [f"harga tembus di bawah Fib 0.786 (retr {retr:.3f}) - struktur batal"]
    elif 0.47 <= retr <= 0.65 and conf:
        pts = 20
        notes = [f"golden zone (retr {retr:.3f}) + confluence: {', '.join(conf)}"]
    elif 0.47 <= retr <= 0.65:
        pts = 15
        notes = [f"golden zone (retr {retr:.3f}), tanpa confluence"]
    elif 0.65 < retr <= 0.85:
        wick = float(df["close"].iloc[-1] - df["low"].iloc[-1])
        body = abs(float(df["close"].iloc[-1] - df["open"].iloc[-1])) + 1e-12
        pts = 12 if wick > body else 9
        notes = [f"deep retracement (retr {retr:.3f}), area 0.786"]
    elif 0.3 <= retr < 0.47:
        pts = 8
        notes = [f"retracement dangkal (retr {retr:.3f}) - trend kuat"]
    elif retr < 0.3:
        pts = 5
        notes = [f"harga dekat swing high (retr {retr:.3f}) - entry telat"]
    else:
        pts = 5
        notes = [f"retr {retr:.3f}"]

    return {"points": pts, "max": 20, "veto": veto, "notes": notes,
            "fib": lv, "retr": round(float(retr), 3) if retr == retr else None,
            "swing": swing}


# ─────────────────────────────────────────────────────────────
# 4. SUPPORT & RESISTANCE  (max 20)
# ─────────────────────────────────────────────────────────────

def score_sr(df: pd.DataFrame, zones: list[dict]) -> dict:
    price = float(df["close"].iloc[-1])
    sup, res = ta.nearest_zones(zones, price)
    struct = ta.market_structure(df)

    # deteksi breakdown: 3 candle terakhir close di bawah zona support yang sebelumnya valid
    breakdown = False
    for z in zones:
        if z["last_touch_bars_ago"] <= 25 and z["touches"] >= 2:
            prev_above = float(df["close"].iloc[-10:-3].max()) > z["high"]
            now_below = price < z["low"] * 0.985
            if prev_above and now_below:
                breakdown = True
                break

    notes, veto = [], False
    if breakdown:
        pts, veto = 0, True
        notes = ["baru saja breakdown zona support"]
    elif sup and ta.in_zone(price, sup, 1.5):
        flip = "H" in sup["types"] and "L" in sup["types"]
        if sup["touches"] >= 3 or flip:
            pts = 20
            notes = [f"di zona support kuat ({sup['touches']}x sentuhan"
                     + (", flip zone)" if flip else ")")]
        else:
            pts = 15
            notes = [f"di zona support ({sup['touches']}x sentuhan)"]
    elif sup and (price - sup["high"]) / price * 100 <= 3.0:
        pts = 12
        notes = [f"dekat support ({(price - sup['high']) / price * 100:.1f}% di atas)"]
    elif sup:
        pts = 6
        notes = ["di tengah range, tidak dekat level"]
    else:
        pts = 4
        notes = ["tidak ada support jelas di bawah - risiko tinggi"]

    if struct["trend"] == "downtrend":
        pts = max(0, pts - 5)
        notes.append("struktur downtrend (LH/LL)")

    dist_res = ((res["level"] - price) / price * 100) if res else None
    return {"points": pts, "max": 20, "veto": veto, "notes": notes,
            "support": sup, "resistance": res, "trend": struct["trend"],
            "last_hl": struct["last_hl"],
            "dist_to_res_pct": round(dist_res, 2) if dist_res is not None else None}


# ─────────────────────────────────────────────────────────────
# 5. CHART PATTERN  (max 15)
# ─────────────────────────────────────────────────────────────

def score_pattern(df: pd.DataFrame, vol_ratio: float) -> dict:
    bull, bear = ta.detect_patterns(df)
    price = float(df["close"].iloc[-1])
    notes, veto = [], False

    if bear and not bull:
        pts, veto = 0, True
        notes = [f"pola bearish terdeteksi: {bear}"]
    elif not bull:
        pts = 0
        notes = ["tidak ada pola jelas"]
    else:
        broke = price > bull["resistance"]
        confirmed = broke and vol_ratio >= 1.5
        if bull["kind"] == "continuation" and confirmed:
            pts = 15
            notes = [f"{bull['name']} - breakout tervalidasi volume"]
        elif bull["kind"] == "continuation" and broke:
            pts = 11
            notes = [f"{bull['name']} - breakout tapi volume belum konfirmasi"]
        elif bull["kind"] == "continuation":
            pts = 12
            notes = [f"{bull['name']} terbentuk, menunggu breakout di {bull['resistance']:.6g}"]
        elif confirmed or broke:
            pts = 10
            notes = [f"{bull['name']} - neckline tertembus"]
        else:
            pts = 7
            notes = [f"{bull['name']} terbentuk, belum konfirmasi neckline"]
        if bear:
            pts = max(0, pts - 4)
            notes.append(f"peringatan: {bear} juga terdeteksi")

    return {"points": pts, "max": 15, "veto": veto, "notes": notes,
            "pattern": bull["name"] if bull else None,
            "pattern_obj": bull, "bearish": bear}


# ─────────────────────────────────────────────────────────────
# Regime BTC
# ─────────────────────────────────────────────────────────────

def btc_regime(df: pd.DataFrame) -> dict:
    c = df["close"]
    e50 = ta.ema(c, 50)
    e200 = ta.ema(c, 200)
    k = ta.stoch_rsi(c)["k"].dropna()
    price = float(c.iloc[-1])
    above50 = price > float(e50.iloc[-1])
    above200 = price > float(e200.iloc[-1]) if len(c) > 200 else above50
    k_now = float(k.iloc[-1]) if len(k) else 50.0
    struct = ta.market_structure(df)

    if above50 and above200 and struct["trend"] != "downtrend" and k_now < 85:
        status, mult = "HIJAU", 1.0
        msg = "BTC di atas EMA50 & EMA200, struktur sehat - cari long"
    elif not above50 and struct["trend"] == "downtrend":
        status, mult = "MERAH", 0.0
        msg = "BTC di bawah EMA50 + struktur downtrend - STOP entry long"
    else:
        status, mult = "KUNING", 0.5
        msg = "BTC sideways/campuran - hanya setup A+ (skor >= 80), size setengah"

    return {"status": status, "size_mult": mult, "message": msg,
            "price": price, "stochrsi_k": round(k_now, 1),
            "above_ema50": above50, "above_ema200": above200,
            "trend": struct["trend"]}


# ─────────────────────────────────────────────────────────────
# Agregasi + rencana trade
# ─────────────────────────────────────────────────────────────

def grade(total: int) -> str:
    if total >= 80:
        return "A+"
    if total >= 70:
        return "A"
    if total >= 60:
        return "B"
    return "C"


def build_trade_plan(df, fib_res, sr_res, pat_res, capital, risk_pct, size_mult):
    """Hitung entry, SL, TP, R:R, dan ukuran posisi."""
    price = float(df["close"].iloc[-1])
    fib = fib_res.get("fib")
    swing = fib_res.get("swing")
    a = float(ta.atr(df).iloc[-1])

    # Entry: golden zone kalau harga masih di atasnya, kalau tidak pakai harga sekarang
    if fib:
        gz_hi, gz_lo = fib["0.5"], fib["0.618"]
        entry = price if price <= gz_hi * 1.01 else (gz_hi + gz_lo) / 2
    else:
        entry = price

    # Stop loss: invalidasi struktur terendah yang masuk akal
    cands = []
    if sr_res.get("last_hl"):
        cands.append(sr_res["last_hl"] * 0.985)
    if sr_res.get("support"):
        cands.append(sr_res["support"]["low"] * 0.985)
    if fib:
        cands.append(fib["0.786"] * 0.99)
    cands = [x for x in cands if x < entry * 0.995]
    sl = max(cands) if cands else entry - 1.5 * a
    if (entry - sl) / entry > 0.20:                      # SL terlalu jauh -> pakai ATR
        sl = entry - 2.0 * a

    # Take profit
    tp1 = None
    if sr_res.get("resistance"):
        tp1 = sr_res["resistance"]["level"]
    if fib and (tp1 is None or tp1 <= entry * 1.02):
        tp1 = fib["ext_1.272"]
    if tp1 is None:
        tp1 = entry + 2 * (entry - sl)
    tp2 = fib["ext_1.618"] if fib else entry + 3 * (entry - sl)
    if pat_res.get("pattern_obj") and pat_res["pattern_obj"].get("target"):
        tp2 = max(tp2, pat_res["pattern_obj"]["target"])

    risk_per_unit = entry - sl
    rr1 = (tp1 - entry) / risk_per_unit if risk_per_unit > 0 else 0
    rr2 = (tp2 - entry) / risk_per_unit if risk_per_unit > 0 else 0

    risk_amount = capital * (risk_pct / 100) * size_mult
    sl_dist = risk_per_unit / entry if entry else 0
    pos_size = risk_amount / sl_dist if sl_dist > 0 else 0

    return {
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "sl_pct": round(-sl_dist * 100, 2),
        "tp1_pct": round((tp1 - entry) / entry * 100, 2),
        "tp2_pct": round((tp2 - entry) / entry * 100, 2),
        "rr1": round(rr1, 2), "rr2": round(rr2, 2),
        "risk_amount": round(risk_amount, 2),
        "position_size": round(pos_size, 2),
    }


def evaluate(symbol, df_bias, df_htf, regime, cfg) -> dict | None:
    """Jalankan seluruh pipeline skoring untuk satu simbol."""
    if len(df_bias) < cfg.get("min_bars_analysis", 120):
        return None

    zones = ta.sr_zones(df_bias, order=cfg["pivot_order"], tol_pct=cfg["sr_tolerance_pct"])

    vol = score_volume(df_bias)
    st = score_stochrsi(df_bias, df_htf)
    fb = score_fibonacci(df_bias, zones)
    sr = score_sr(df_bias, zones)
    pat = score_pattern(df_bias, vol["vol_ratio"])

    total = vol["points"] + st["points"] + fb["points"] + sr["points"] + pat["points"]

    vetoes = []
    if regime["status"] == "MERAH":
        vetoes.append("BTC status MERAH")
    for name, comp in [("Volume", vol), ("StochRSI", st), ("Fibonacci", fb),
                       ("S/R", sr), ("Pattern", pat)]:
        if comp["veto"]:
            vetoes.append(f"{name}: {comp['notes'][0]}")

    plan = build_trade_plan(df_bias, fb, sr, pat, cfg["capital"],
                            cfg["risk_pct"], regime["size_mult"])

    if plan["rr1"] < cfg["min_rr"]:
        vetoes.append(f"R:R ke TP1 hanya 1:{plan['rr1']} (min 1:{cfg['min_rr']})")
    if plan["rr1"] > cfg.get("max_plausible_rr", 15.0):
        vetoes.append(f"R:R 1:{plan['rr1']} tidak masuk akal - level swing/support "
                      "kemungkinan rusak, periksa chart manual")
    if st["k"] is not None and st["k"] > 80:
        pass  # sudah ditangani di score_stochrsi

    return {
        "symbol": symbol,
        "price": float(df_bias["close"].iloc[-1]),
        "total": total,
        "grade": grade(total),
        "vetoed": len(vetoes) > 0,
        "veto_reasons": vetoes,
        "s_volume": vol["points"], "s_stochrsi": st["points"],
        "s_fib": fb["points"], "s_sr": sr["points"], "s_pattern": pat["points"],
        "vol_ratio": vol["vol_ratio"],
        "stochrsi_k": st["k"], "stochrsi_htf": st["k_htf"],
        "fib_retr": fb["retr"],
        "trend": sr["trend"],
        "pattern": pat["pattern"],
        "dist_to_res_pct": sr["dist_to_res_pct"],
        "plan": plan,
        "notes": {"volume": vol["notes"], "stochrsi": st["notes"],
                  "fibonacci": fb["notes"], "sr": sr["notes"], "pattern": pat["notes"]},
    }
