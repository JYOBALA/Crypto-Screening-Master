"""
indicators.py — Perhitungan indikator, struktur pasar, Fibonacci, S/R, dan chart pattern.
Murni pandas/numpy. Tidak butuh TA-Lib.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────
# Indikator dasar
# ─────────────────────────────────────────────────────────────

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def stoch_rsi(close: pd.Series, rsi_len: int = 14, stoch_len: int = 14,
              k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stoch RSI standar TradingView: K=3, D=3, RSI=14, Stoch=14 (skala 0-100)."""
    r = rsi(close, rsi_len)
    lo = r.rolling(stoch_len).min()
    hi = r.rolling(stoch_len).max()
    raw = (r - lo) / (hi - lo).replace(0, np.nan) * 100
    k_line = raw.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return pd.DataFrame({"k": k_line, "d": d_line})


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def slope_pct(series: pd.Series, lookback: int = 20) -> float:
    """Kemiringan regresi linear dinormalisasi terhadap nilai rata-rata (%/bar)."""
    s = series.dropna().tail(lookback)
    if len(s) < max(5, lookback // 2):
        return 0.0
    x = np.arange(len(s))
    coef = np.polyfit(x, s.values, 1)[0]
    denom = np.abs(s.values).mean()
    return float(coef / denom * 100) if denom else 0.0


# ─────────────────────────────────────────────────────────────
# Resample timeframe
# ─────────────────────────────────────────────────────────────

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    out = df.resample(rule, label="left", closed="left").agg(agg).dropna()
    return out


# ─────────────────────────────────────────────────────────────
# Swing point / struktur pasar
# ─────────────────────────────────────────────────────────────

def find_pivots(df: pd.DataFrame, order: int = 5):
    """Fractal pivot: high tertinggi (low terendah) di antara `order` bar kiri-kanan.
    Return list of (index_position, price)."""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    n = len(df)
    for i in range(order, n - order):
        win_h = h[i - order:i + order + 1]
        win_l = l[i - order:i + order + 1]
        if h[i] == win_h.max() and (win_h.argmax() == order):
            highs.append((i, float(h[i])))
        if l[i] == win_l.min() and (win_l.argmin() == order):
            lows.append((i, float(l[i])))
    return highs, lows


def market_structure(df: pd.DataFrame, order: int = 5) -> dict:
    """Deteksi HH/HL (uptrend) atau LH/LL (downtrend) + Higher Low terakhir."""
    highs, lows = find_pivots(df, order)
    res = {"trend": "undefined", "last_hl": None, "last_hl_idx": None,
           "last_swing_high": None, "last_swing_low": None}
    if len(highs) >= 2:
        res["last_swing_high"] = highs[-1][1]
    if len(lows) >= 2:
        res["last_swing_low"] = lows[-1][1]
        res["last_hl"] = lows[-1][1]
        res["last_hl_idx"] = lows[-1][0]
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]
        hl = lows[-1][1] > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]
        ll = lows[-1][1] < lows[-2][1]
        if hh and hl:
            res["trend"] = "uptrend"
        elif lh and ll:
            res["trend"] = "downtrend"
        else:
            res["trend"] = "range"
    return res


# ─────────────────────────────────────────────────────────────
# Fibonacci
# ─────────────────────────────────────────────────────────────

def last_impulse_swing(df: pd.DataFrame, order: int = 5, min_move_pct: float = 12.0):
    """Cari gelombang impuls naik terakhir: swing low -> swing high sesudahnya.
    Mengembalikan dict atau None kalau strukturnya tidak jelas."""
    highs, lows = find_pivots(df, order)
    if not highs or not lows:
        return None

    # Ambil swing high terakhir, lalu swing low terendah sebelum swing high itu
    for hi_idx, hi_price in reversed(highs):
        prior_lows = [(i, p) for i, p in lows if i < hi_idx]
        if not prior_lows:
            continue
        # low terendah dalam 90 bar sebelum swing high (batasi supaya relevan)
        window = [(i, p) for i, p in prior_lows if hi_idx - i <= 90]
        if not window:
            continue
        lo_idx, lo_price = min(window, key=lambda x: x[1])
        move = (hi_price - lo_price) / lo_price * 100
        bars = hi_idx - lo_idx
        if move >= min_move_pct and bars >= 3:
            # Perbarui swing high bila harga sudah melewatinya sejak pivot terbentuk
            tail = df["high"].iloc[hi_idx:]
            run_high = float(tail.max())
            if run_high > hi_price:
                hi_price = run_high
                hi_idx = int(tail.idxmax() == tail.index[0]) and hi_idx or \
                    hi_idx + int(np.argmax(tail.values))
                move = (hi_price - lo_price) / lo_price * 100
                bars = hi_idx - lo_idx
            return {"low_idx": lo_idx, "low": lo_price,
                    "high_idx": hi_idx, "high": hi_price,
                    "move_pct": move, "bars": bars}
    return None


def last_impulse_swing_down(df: pd.DataFrame, order: int = 5, min_move_pct: float = 12.0):
    """Cermin last_impulse_swing(): gelombang impuls TURUN terakhir (swing high ->
    swing low sesudahnya). Dipakai modul kandidat SHORT (belum pernah diuji --
    lihat KRITERIA_EVALUASI.md/RINGKASAN_AKHIR.md, sisi long saja yang sudah diuji)."""
    highs, lows = find_pivots(df, order)
    if not highs or not lows:
        return None

    for lo_idx, lo_price in reversed(lows):
        prior_highs = [(i, p) for i, p in highs if i < lo_idx]
        if not prior_highs:
            continue
        window = [(i, p) for i, p in prior_highs if lo_idx - i <= 90]
        if not window:
            continue
        hi_idx, hi_price = max(window, key=lambda x: x[1])
        move = (hi_price - lo_price) / hi_price * 100
        bars = lo_idx - hi_idx
        if move >= min_move_pct and bars >= 3:
            # Perbarui swing low bila harga sudah melewatinya sejak pivot terbentuk
            tail = df["low"].iloc[lo_idx:]
            run_low = float(tail.min())
            if run_low < lo_price:
                lo_price = run_low
                lo_idx = lo_idx + int(np.argmin(tail.values))
                move = (hi_price - lo_price) / hi_price * 100
                bars = lo_idx - hi_idx
            return {"high_idx": hi_idx, "high": hi_price,
                    "low_idx": lo_idx, "low": lo_price,
                    "move_pct": move, "bars": bars}
    return None


def fib_levels(low: float, high: float) -> dict:
    rng = high - low
    return {
        "0.0": high,
        "0.236": high - 0.236 * rng,
        "0.382": high - 0.382 * rng,
        "0.5": high - 0.5 * rng,
        "0.618": high - 0.618 * rng,
        "0.786": high - 0.786 * rng,
        "1.0": low,
        "ext_1.272": high + 0.272 * rng,
        "ext_1.618": high + 0.618 * rng,
        "ext_2.618": high + 1.618 * rng,
    }


def retracement_ratio(price: float, low: float, high: float) -> float:
    rng = high - low
    if rng <= 0:
        return np.nan
    return (high - price) / rng


# ─────────────────────────────────────────────────────────────
# Support & Resistance
# ─────────────────────────────────────────────────────────────

def sr_zones(df: pd.DataFrame, order: int = 5, tol_pct: float = 1.5) -> list[dict]:
    """Kelompokkan pivot menjadi zona S/R. Zona valid = minimal 2 sentuhan."""
    highs, lows = find_pivots(df, order)
    pts = [(i, p, "H") for i, p in highs] + [(i, p, "L") for i, p in lows]
    if not pts:
        return []
    pts.sort(key=lambda x: x[1])

    clusters, cur = [], [pts[0]]
    for pt in pts[1:]:
        ref = np.mean([c[1] for c in cur])
        if abs(pt[1] - ref) / ref * 100 <= tol_pct:
            cur.append(pt)
        else:
            clusters.append(cur)
            cur = [pt]
    clusters.append(cur)

    n = len(df)
    zones = []
    for cl in clusters:
        prices = [c[1] for c in cl]
        idxs = [c[0] for c in cl]
        zones.append({
            "level": float(np.mean(prices)),
            "low": float(min(prices)),
            "high": float(max(prices)),
            "touches": len(cl),
            "last_touch_bars_ago": int(n - 1 - max(idxs)),
            "types": [c[2] for c in cl],
        })
    return [z for z in zones if z["touches"] >= 2]


def nearest_zones(zones: list[dict], price: float):
    below = [z for z in zones if z["high"] < price * 1.005]
    above = [z for z in zones if z["low"] > price * 0.995]
    sup = max(below, key=lambda z: z["level"]) if below else None
    res = min(above, key=lambda z: z["level"]) if above else None
    return sup, res


def in_zone(price: float, zone: dict, buffer_pct: float = 1.0) -> bool:
    lo = zone["low"] * (1 - buffer_pct / 100)
    hi = zone["high"] * (1 + buffer_pct / 100)
    return lo <= price <= hi


# ─────────────────────────────────────────────────────────────
# Chart pattern
# ─────────────────────────────────────────────────────────────

def detect_bull_flag(df: pd.DataFrame) -> dict | None:
    """Pole naik tajam lalu konsolidasi turun/datar dengan volume mengecil."""
    c, v = df["close"], df["volume"]
    if len(df) < 40:
        return None
    for cons in range(3, 16):                       # panjang flag
        for pole in range(4, 13):                   # panjang pole
            end = len(df) - cons
            start = end - pole
            if start < 5:
                continue
            pole_low = df["low"].iloc[start:end].min()
            pole_high = df["high"].iloc[start:end].max()
            if pole_low <= 0:
                continue
            pole_gain = (pole_high - pole_low) / pole_low * 100
            if pole_gain < 15:
                continue
            flag = df.iloc[end:]
            flag_high = flag["high"].max()
            flag_low = flag["low"].min()
            depth = (pole_high - flag_low) / (pole_high - pole_low)
            if depth > 0.62:                        # koreksi terlalu dalam
                continue
            vol_pole = v.iloc[start:end].mean()
            vol_flag = flag["volume"].mean()
            if vol_flag > vol_pole * 0.85:          # volume harus mengering
                continue
            if flag["high"].iloc[-1] > flag_high * 1.0001:
                pass
            return {"name": "Bull Flag", "kind": "continuation",
                    "resistance": float(flag_high), "support": float(flag_low),
                    "target": float(flag_high + (pole_high - pole_low)),
                    "quality": min(1.0, pole_gain / 35)}
    return None


def detect_ascending_triangle(df: pd.DataFrame, order: int = 4) -> dict | None:
    highs, lows = find_pivots(df, order)
    if len(highs) < 2 or len(lows) < 2:
        return None
    rh = [h for h in highs if len(df) - 1 - h[0] <= 70][-3:]
    rl = [l for l in lows if len(df) - 1 - l[0] <= 70][-3:]
    if len(rh) < 2 or len(rl) < 2:
        return None
    lvl = np.mean([h[1] for h in rh])
    flat = all(abs(h[1] - lvl) / lvl * 100 <= 2.0 for h in rh)
    rising = all(rl[i][1] > rl[i - 1][1] for i in range(1, len(rl)))
    if flat and rising:
        height = lvl - rl[0][1]
        return {"name": "Ascending Triangle", "kind": "continuation",
                "resistance": float(lvl), "support": float(rl[-1][1]),
                "target": float(lvl + height), "quality": 0.9}
    return None


def detect_falling_wedge(df: pd.DataFrame, order: int = 4) -> dict | None:
    highs, lows = find_pivots(df, order)
    rh = [h for h in highs if len(df) - 1 - h[0] <= 60][-3:]
    rl = [l for l in lows if len(df) - 1 - l[0] <= 60][-3:]
    if len(rh) < 2 or len(rl) < 2:
        return None
    lower_highs = all(rh[i][1] < rh[i - 1][1] for i in range(1, len(rh)))
    lower_lows = all(rl[i][1] < rl[i - 1][1] for i in range(1, len(rl)))
    if not (lower_highs and lower_lows):
        return None
    w_start = abs(rh[0][1] - rl[0][1]) / rh[0][1]
    w_end = abs(rh[-1][1] - rl[-1][1]) / rh[-1][1]
    if w_end >= w_start * 0.85:                     # harus menyempit
        return None
    vol_slope = slope_pct(df["volume"], 20)
    if vol_slope > 0.5:
        return None
    return {"name": "Falling Wedge", "kind": "reversal",
            "resistance": float(rh[-1][1]), "support": float(rl[-1][1]),
            "target": float(rh[0][1]), "quality": 0.8}


def detect_double_bottom(df: pd.DataFrame, order: int = 5) -> dict | None:
    _, lows = find_pivots(df, order)
    rl = [l for l in lows if len(df) - 1 - l[0] <= 90]
    if len(rl) < 2:
        return None
    b1, b2 = rl[-2], rl[-1]
    gap = b2[0] - b1[0]
    if not (5 <= gap <= 60):
        return None
    if abs(b2[1] - b1[1]) / b1[1] * 100 > 4.0:
        return None
    neck = df["high"].iloc[b1[0]:b2[0] + 1].max()
    if neck <= max(b1[1], b2[1]):
        return None
    v1 = df["volume"].iloc[max(0, b1[0] - 2):b1[0] + 3].mean()
    v2 = df["volume"].iloc[max(0, b2[0] - 2):b2[0] + 3].mean()
    quality = 0.9 if v2 < v1 else 0.6
    return {"name": "Double Bottom", "kind": "reversal",
            "resistance": float(neck), "support": float(min(b1[1], b2[1])),
            "target": float(neck + (neck - min(b1[1], b2[1]))), "quality": quality}


def detect_bearish_pattern(df: pd.DataFrame, order: int = 4) -> str | None:
    """Rising wedge / double top sederhana — dipakai sebagai penalti."""
    highs, lows = find_pivots(df, order)
    rh = [h for h in highs if len(df) - 1 - h[0] <= 60][-3:]
    rl = [l for l in lows if len(df) - 1 - l[0] <= 60][-3:]
    if len(rh) >= 2 and len(rl) >= 2:
        hh = all(rh[i][1] > rh[i - 1][1] for i in range(1, len(rh)))
        hl = all(rl[i][1] > rl[i - 1][1] for i in range(1, len(rl)))
        if hh and hl:
            w_start = abs(rh[0][1] - rl[0][1]) / rh[0][1]
            w_end = abs(rh[-1][1] - rl[-1][1]) / rh[-1][1]
            if w_end < w_start * 0.7 and slope_pct(df["volume"], 20) < 0:
                return "Rising Wedge"
    if len(rh) >= 2:
        t1, t2 = rh[-2], rh[-1]
        if abs(t2[1] - t1[1]) / t1[1] * 100 < 2.5 and 5 <= t2[0] - t1[0] <= 60:
            if df["close"].iloc[-1] < t2[1] * 0.97:
                return "Double Top"
    return None


def detect_patterns(df: pd.DataFrame) -> tuple[dict | None, str | None]:
    """Return (pola bullish terbaik, nama pola bearish jika ada)."""
    bearish = detect_bearish_pattern(df)
    candidates = [
        detect_bull_flag(df),
        detect_ascending_triangle(df),
        detect_double_bottom(df),
        detect_falling_wedge(df),
    ]
    candidates = [c for c in candidates if c]
    best = max(candidates, key=lambda c: c["quality"]) if candidates else None
    return best, bearish
