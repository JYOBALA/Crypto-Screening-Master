"""
accumulation.py — Mesin deteksi fase akumulasi ("hidden gem") untuk altcoin yang sedang sideways.

Landasan metodologi:
  1. Wyckoff Accumulation Schematic — Selling Climax → AR → ST → Spring → Test → SOS → LPS.
     Spring bervolume rendah yang diikuti SOS bervolume tinggi = sinyal konviksi tertinggi.
  2. VCP (Mark Minervini) — 2–5 kontraksi berjenjang yang makin dangkal (mis. 20% → 10% → 5%)
     disertai volume mengering; pivot = high kontraksi terakhir.
  3. Bollinger BandWidth Squeeze (John Bollinger) — BandWidth = (upper-lower)/middle.
     Dinilai sebagai persentil terhadap sejarahnya sendiri, bukan angka absolut.
  4. Stage Analysis (Stan Weinstein) — Stage 1 = basis sideways dengan MA 30-minggu (150 hari)
     mendatar; Stage 2 = breakout di atas basis + MA berbalik naik + volume ekspansi.
  5. Jejak volume smart money — OBV/ADL naik saat harga datar = akumulasi diam-diam;
     volume lebih besar di bounce ketimbang di penurunan (di dalam range).
  6. Relative Strength vs BTC — koin yang menahan diri lebih baik dari BTC saat sideways
     cenderung memimpin saat rotasi terjadi.

Skor total 100:
  Struktur Basis 20 | Kontraksi Volatilitas 20 | Event Wyckoff 25 | Smart Money 20 | RS vs BTC 15
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import indicators as ta


# ─────────────────────────────────────────────────────────────
# Utilitas
# ─────────────────────────────────────────────────────────────

def bollinger_bandwidth(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.Series:
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std(ddof=0)
    return ((mid + mult * sd) - (mid - mult * sd)) / mid.replace(0, np.nan) * 100


def percentile_rank(series: pd.Series, window: int = 180) -> float:
    """Persentil nilai terakhir terhadap `window` bar terakhir (0 = paling rendah)."""
    s = series.dropna().tail(window)
    if len(s) < 20:
        return np.nan
    return float((s < s.iloc[-1]).sum() / len(s) * 100)


def atr_pct(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return ta.atr(df, length) / df["close"] * 100


def adl(df: pd.DataFrame) -> pd.Series:
    """Accumulation/Distribution Line — volume dibobot posisi close dalam range bar."""
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm.fillna(0) * df["volume"]).cumsum()


# ─────────────────────────────────────────────────────────────
# 1. Deteksi Trading Range (basis sideways)
# ─────────────────────────────────────────────────────────────

def detect_trading_range(df: pd.DataFrame, max_width_pct: float = 45.0,
                         min_bars: int = 25) -> dict | None:
    """Cari basis sideways terpanjang yang masih memenuhi batas lebar range.

    Kripto jauh lebih volatil dari saham, jadi ambang lebar default 45% (bukan 15-20%).
    """
    n = len(df)
    best = None
    for L in (150, 120, 100, 80, 65, 50, 40, 30, min_bars):
        if L > n - 60 or L < min_bars:
            continue
        seg = df.iloc[-L:]
        hi = float(seg["high"].max())
        lo = float(seg["low"].min())
        if lo <= 0:
            continue
        width = (hi - lo) / lo * 100
        if width > max_width_pct:
            continue
        price = float(df["close"].iloc[-1])
        if not (lo * 0.98 <= price <= hi * 1.02):
            continue
        # Basis harus terbentuk SETELAH penurunan (ciri Stage 1, bukan Stage 3)
        prior = df.iloc[max(0, n - L - 90): n - L]
        came_from_above = (len(prior) > 20 and float(prior["high"].max()) > hi * 1.15)
        best = {"bars": L, "top": hi, "bottom": lo, "width_pct": width,
                "mid": (hi + lo) / 2, "start_idx": n - L,
                "after_decline": bool(came_from_above)}
        break
    return best


def contraction_legs(df: pd.DataFrame, start_idx: int, order: int = 4) -> list[float]:
    """Kedalaman tiap pullback di dalam basis (%), urut dari lama ke terbaru — inti VCP."""
    seg = df.iloc[start_idx:].reset_index(drop=True)
    if len(seg) < 15:
        return []
    highs, lows = ta.find_pivots(seg, order)
    pts = sorted([(i, p, "H") for i, p in highs] + [(i, p, "L") for i, p in lows])
    depths = []
    for a, b in zip(pts, pts[1:]):
        if a[2] == "H" and b[2] == "L" and a[1] > 0:
            d = (a[1] - b[1]) / a[1] * 100
            if d >= 2.0:                 # abaikan noise
                depths.append(d)
    return depths


# ─────────────────────────────────────────────────────────────
# 2. Event Wyckoff
# ─────────────────────────────────────────────────────────────

def wyckoff_events(df: pd.DataFrame, rng: dict) -> dict:
    """Deteksi event kunci skema akumulasi Wyckoff di dalam trading range."""
    n = len(df)
    s = rng["start_idx"]
    seg = df.iloc[s:]
    if len(seg) < 20:
        return {"phase": "?", "events": {}}

    vma = df["volume"].rolling(20).mean()
    spread = (df["high"] - df["low"])
    spread_ma = spread.rolling(20).mean()
    top, bot, mid = rng["top"], rng["bottom"], rng["mid"]
    ev: dict = {}

    # Support REFERENSI = dasar dari 60% awal basis. Spring harus menembus level ini,
    # bukan dasar range yang sudah termasuk spring-nya sendiri.
    ref_end = s + max(10, int(len(seg) * 0.6))
    ref_support = float(df["low"].iloc[s:ref_end].min())

    # ── Selling Climax: bar turun lebar, volume ekstrem, dekat dasar range.
    #    Jendela diperluas ke belakang — SC sering terjadi tepat SEBELUM range terbentuk.
    sc_start = max(20, s - 40)
    third = s + max(3, len(seg) // 3)
    sc_i = None
    for i in range(sc_start, min(third, n)):
        if pd.isna(vma.iloc[i]) or vma.iloc[i] <= 0:
            continue
        vr = df["volume"].iloc[i] / vma.iloc[i]
        down = df["close"].iloc[i] < df["open"].iloc[i]
        wide = spread.iloc[i] > spread_ma.iloc[i] * 1.4
        near_low = df["low"].iloc[i] <= bot * 1.10
        if vr >= 2.0 and down and wide and near_low:
            if sc_i is None or df["volume"].iloc[i] > df["volume"].iloc[sc_i]:
                sc_i = i
    if sc_i is not None:
        ev["SC"] = {"idx": sc_i, "low": float(df["low"].iloc[sc_i]),
                    "vol_ratio": round(float(df["volume"].iloc[sc_i] / vma.iloc[sc_i]), 2)}

        # ── Automatic Rally: high tertinggi dalam 25 bar setelah SC
        w = df.iloc[sc_i + 1: min(sc_i + 26, n)]
        if len(w) > 3:
            ev["AR"] = {"idx": int(w["high"].idxmax() == w.index[0]) or 0,
                        "high": float(w["high"].max())}

        # ── Secondary Test: kembali ke area SC dengan volume lebih kecil
        for i in range(sc_i + 5, n):
            if df["low"].iloc[i] <= ev["SC"]["low"] * 1.05 and not pd.isna(vma.iloc[i]):
                if df["volume"].iloc[i] < df["volume"].iloc[sc_i] * 0.7:
                    ev["ST"] = {"idx": i, "low": float(df["low"].iloc[i]),
                                "vol_ratio": round(float(df["volume"].iloc[i] / vma.iloc[i]), 2)}
                    break

    # ── Spring / Shakeout: tembus di bawah support range lalu close kembali di dalam.
    #    Dicari di 45% terakhir basis (Phase C).
    spring_ref = min(ref_support, ev["ST"]["low"]) if "ST" in ev else ref_support
    phase_c_start = s + int(len(seg) * 0.45)
    for i in range(max(phase_c_start, s + 5), n):
        pen = (spring_ref - df["low"].iloc[i]) / spring_ref * 100
        if pen > 0.4 and df["close"].iloc[i] > spring_ref * 0.998:
            vr = float(df["volume"].iloc[i] / vma.iloc[i]) if vma.iloc[i] > 0 else 1
            # setelah spring harga harus kembali bertahan di dalam range
            after = df.iloc[i + 1: min(i + 8, n)]
            recovered = bool(len(after) and float(after["close"].max()) > bot * 1.01)
            if recovered or i >= n - 3:
                ev["SPRING"] = {"idx": i, "low": float(df["low"].iloc[i]),
                                "ref_support": round(spring_ref, 10),
                                "penetration_pct": round(pen, 2), "vol_ratio": round(vr, 2),
                                "bars_ago": n - 1 - i,
                                "type": "terminal_shakeout" if pen > 3 and vr > 2 else "spring"}
                break

    # ── Test of Spring: retest low spring dengan volume jauh lebih kecil (<50%)
    if "SPRING" in ev:
        si = ev["SPRING"]["idx"]
        for i in range(si + 2, n):
            if df["low"].iloc[i] <= ev["SPRING"]["low"] * 1.04:
                if df["volume"].iloc[i] < df["volume"].iloc[si] * 0.5:
                    ev["TEST"] = {"idx": i, "bars_ago": n - 1 - i,
                                  "vol_pct_of_spring": round(
                                      float(df["volume"].iloc[i] / df["volume"].iloc[si] * 100), 1)}
                    break

    # ── Sign of Strength: bar naik lebar, close dekat high, volume di atas rata-rata,
    #    menembus di atas titik tengah range
    start_sos = ev["SPRING"]["idx"] + 1 if "SPRING" in ev else s + len(seg) // 2
    for i in range(max(start_sos, s), n):
        if pd.isna(vma.iloc[i]) or vma.iloc[i] <= 0 or spread.iloc[i] <= 0:
            continue
        up = df["close"].iloc[i] > df["open"].iloc[i]
        wide = spread.iloc[i] > spread_ma.iloc[i] * 1.3
        close_high = (df["close"].iloc[i] - df["low"].iloc[i]) / spread.iloc[i] >= 0.65
        volup = df["volume"].iloc[i] >= vma.iloc[i] * 1.5
        above_mid = df["close"].iloc[i] > mid
        if up and wide and close_high and volup and above_mid:
            ev["SOS"] = {"idx": i, "bars_ago": n - 1 - i,
                         "vol_ratio": round(float(df["volume"].iloc[i] / vma.iloc[i]), 2),
                         "close": float(df["close"].iloc[i])}
            break

    # ── Last Point of Support: pullback volume tipis yang bertahan di atas mid-range
    if "SOS" in ev:
        oi = ev["SOS"]["idx"]
        for i in range(oi + 1, n):
            quiet = df["volume"].iloc[i] < vma.iloc[i] * 0.85
            holds = df["low"].iloc[i] > mid
            if quiet and holds and df["close"].iloc[i] < df["close"].iloc[oi]:
                ev["LPS"] = {"idx": i, "bars_ago": n - 1 - i}
                break

    # ── UTAD (tanda distribusi, bukan akumulasi) — penalti berat
    for i in range(s + len(seg) // 2, n):
        if df["high"].iloc[i] > top * 1.01 and df["close"].iloc[i] < top * 0.99:
            fwd = df.iloc[i + 1: min(i + 6, n)]
            if len(fwd) and float(fwd["close"].min()) < mid:
                ev["UTAD"] = {"idx": i, "bars_ago": n - 1 - i}
                break

    # ── Klasifikasi fase
    if "SOS" in ev and ("SPRING" in ev or "ST" in ev or "LPS" in ev):
        phase = "D"          # kekuatan muncul, markup mendekat
    elif "SPRING" in ev:
        phase = "C"          # uji pasokan terakhir — entry konviksi tertinggi
    elif "SC" in ev and "ST" in ev:
        phase = "B"          # membangun sebab, masih lama
    elif "SC" in ev:
        phase = "A"          # penurunan baru berhenti
    else:
        phase = "?"
    return {"phase": phase, "events": ev}


# ─────────────────────────────────────────────────────────────
# Komponen skor
# ─────────────────────────────────────────────────────────────

def score_base(df: pd.DataFrame, rng: dict | None) -> dict:
    """Struktur basis / Stage 1 Weinstein — max 20."""
    if not rng:
        return {"points": 0, "max": 20, "veto": True,
                "notes": ["tidak ada basis sideways (harga masih trending)"], "rng": None}

    c = df["close"]
    ma150 = c.rolling(150).mean() if len(c) >= 150 else c.rolling(max(20, len(c) // 3)).mean()
    slope150 = ta.slope_pct(ma150, 30)
    price = float(c.iloc[-1])
    pos = (price - rng["bottom"]) / (rng["top"] - rng["bottom"]) if rng["top"] > rng["bottom"] else 0.5

    pts, notes = 0, []
    # Durasi basis — makin lama makin besar "sebab" yang dibangun (hukum cause & effect)
    b = rng["bars"]
    pts += 8 if b >= 90 else 6 if b >= 60 else 4 if b >= 40 else 2
    notes.append(f"basis {b} hari, lebar {rng['width_pct']:.1f}%")

    # MA 150 mendatar = ciri Stage 1
    if abs(slope150) < 0.08:
        pts += 6
        notes.append("MA150 mendatar (ciri Stage 1)")
    elif slope150 > 0:
        pts += 4
        notes.append("MA150 mulai naik (transisi Stage 2)")
    else:
        pts += 1
        notes.append(f"MA150 masih turun ({slope150:.2f}%/bar)")

    # Posisi harga dalam range — paruh bawah = risiko lebih kecil
    if pos <= 0.45:
        pts += 4
        notes.append(f"harga di paruh bawah range ({pos * 100:.0f}%)")
    elif pos <= 0.7:
        pts += 3
        notes.append(f"harga di tengah range ({pos * 100:.0f}%)")
    else:
        pts += 1
        notes.append(f"harga sudah dekat atap range ({pos * 100:.0f}%)")

    if rng["after_decline"]:
        pts += 2
        notes.append("basis terbentuk setelah penurunan (bukan distribusi puncak)")

    return {"points": min(pts, 20), "max": 20, "veto": False, "notes": notes,
            "rng": rng, "ma150_slope": round(slope150, 3), "pos_in_range": round(pos, 2)}


def score_contraction(df: pd.DataFrame, rng: dict | None) -> dict:
    """VCP + Bollinger squeeze — max 20."""
    bw = bollinger_bandwidth(df["close"])
    bw_now = percentile_rank(bw, 180)
    # squeeze bisa baru saja mulai melepas; ambil kompresi terketat 10 bar terakhir
    bw_recent = percentile_rank(bw.iloc[:-9] if len(bw) > 30 else bw, 180)
    tail = bw.dropna().tail(10)
    hist = bw.dropna().tail(180)
    bw_min = (float((hist < tail.min()).sum() / len(hist) * 100)
              if len(hist) >= 20 and len(tail) else np.nan)
    bw_pct = np.nanmin([bw_now, bw_min]) if not (np.isnan(bw_now) and np.isnan(bw_min)) else np.nan
    ap = atr_pct(df)
    atr_pctile = percentile_rank(ap, 180)

    pts, notes = 0, []
    if not np.isnan(bw_pct):
        if bw_pct <= 10:
            pts += 9
            notes.append(f"BandWidth di persentil {bw_pct:.0f} — squeeze ekstrem")
        elif bw_pct <= 25:
            pts += 7
            notes.append(f"BandWidth di persentil {bw_pct:.0f} — volatilitas terkompresi")
        elif bw_pct <= 45:
            pts += 4
            notes.append(f"BandWidth di persentil {bw_pct:.0f}")
        else:
            notes.append(f"BandWidth di persentil {bw_pct:.0f} — belum terkompresi")
    _ = bw_recent

    if not np.isnan(atr_pctile) and atr_pctile <= 25:
        pts += 3
        notes.append(f"ATR di persentil {atr_pctile:.0f} — rentang harian mengecil")

    legs = contraction_legs(df, rng["start_idx"]) if rng else []
    if len(legs) >= 2:
        tightening = sum(1 for a, b in zip(legs, legs[1:]) if b < a * 0.85)
        depth_str = " → ".join(f"{d:.0f}%" for d in legs[-4:])
        if tightening >= 2 and legs[-1] < legs[0] * 0.5:
            pts += 8
            notes.append(f"VCP {len(legs)} kontraksi menyempit: {depth_str}")
        elif tightening >= 1:
            pts += 5
            notes.append(f"kontraksi menyempit: {depth_str}")
        else:
            pts += 1
            notes.append(f"kontraksi tidak menyempit: {depth_str}")
    else:
        notes.append("kontraksi belum terbentuk")

    return {"points": min(pts, 20), "max": 20, "veto": False, "notes": notes,
            "bw_pctile": None if np.isnan(bw_pct) else round(bw_pct, 1),
            "atr_pctile": None if np.isnan(atr_pctile) else round(atr_pctile, 1),
            "legs": [round(x, 1) for x in legs[-4:]]}


def score_wyckoff(df: pd.DataFrame, rng: dict | None) -> dict:
    """Event Wyckoff — max 25. Bobot terbesar: ini inti deteksi akumulasi."""
    if not rng:
        return {"points": 0, "max": 25, "veto": True,
                "notes": ["tidak ada trading range untuk dianalisa"], "phase": "?", "events": {}}

    w = wyckoff_events(df, rng)
    ev, phase = w["events"], w["phase"]
    pts, notes, veto = 0, [], False

    if "UTAD" in ev:
        return {"points": 0, "max": 25, "veto": True,
                "notes": [f"UTAD terdeteksi {ev['UTAD']['bars_ago']} bar lalu — "
                          "ini pola DISTRIBUSI, bukan akumulasi"],
                "phase": "distribusi", "events": ev}

    if "SC" in ev:
        pts += 3
        notes.append(f"Selling Climax (volume {ev['SC']['vol_ratio']}x)")
    if "ST" in ev:
        pts += 3
        notes.append(f"Secondary Test volume mengecil ({ev['ST']['vol_ratio']}x)")

    if "SPRING" in ev:
        sp = ev["SPRING"]
        if sp["vol_ratio"] < 1.2:
            pts += 9
            notes.append(f"Spring volume rendah ({sp['vol_ratio']}x, tembus "
                         f"{sp['penetration_pct']}%) {sp['bars_ago']} bar lalu — "
                         "sinyal Wyckoff terkuat")
        else:
            pts += 6
            notes.append(f"{sp['type'].replace('_', ' ').title()} "
                         f"(volume {sp['vol_ratio']}x, tembus {sp['penetration_pct']}%) "
                         f"{sp['bars_ago']} bar lalu")
    if "TEST" in ev:
        pts += 5
        notes.append(f"Test spring volume hanya {ev['TEST']['vol_pct_of_spring']}% "
                     "dari spring — pasokan habis")
    if "SOS" in ev:
        pts += 5
        notes.append(f"Sign of Strength {ev['SOS']['bars_ago']} bar lalu "
                     f"(volume {ev['SOS']['vol_ratio']}x)")
    if "LPS" in ev:
        pts += 3
        notes.append(f"Last Point of Support {ev['LPS']['bars_ago']} bar lalu")

    if not notes:
        notes.append("belum ada event Wyckoff yang jelas")

    return {"points": min(pts, 25), "max": 25, "veto": veto, "notes": notes,
            "phase": phase, "events": ev}


def score_smart_money(df: pd.DataFrame, rng: dict | None) -> dict:
    """Jejak volume institusional di dalam range — max 20."""
    c, v = df["close"], df["volume"]
    o = ta.obv(c, v)
    a = adl(df)
    lb = min(rng["bars"], 120) if rng else 60

    obv_s = ta.slope_pct(o, lb)
    adl_s = ta.slope_pct(a, lb)
    price_s = ta.slope_pct(c, lb)

    seg = df.iloc[-lb:]
    up_v = float(seg.loc[seg["close"] > seg["open"], "volume"].sum())
    dn_v = float(seg.loc[seg["close"] <= seg["open"], "volume"].sum())
    ud = up_v / dn_v if dn_v > 0 else 2.0

    pts, notes = 0, []
    # Inti: harga datar tapi OBV naik = akumulasi diam-diam
    flat = abs(price_s) < 0.15
    if flat and obv_s > 0.5:
        pts += 10
        notes.append(f"harga datar tapi OBV naik ({obv_s:+.2f}) — akumulasi diam-diam")
    elif obv_s > 0.5:
        pts += 7
        notes.append(f"OBV menguat ({obv_s:+.2f})")
    elif obv_s > 0:
        pts += 4
        notes.append(f"OBV sedikit naik ({obv_s:+.2f})")
    else:
        notes.append(f"OBV menurun ({obv_s:+.2f}) — indikasi distribusi diam-diam")

    if adl_s > 0:
        pts += 4
        notes.append(f"A/D Line naik ({adl_s:+.2f})")

    # Wyckoff: di akumulasi, volume lebih besar saat naik daripada saat turun
    if ud >= 1.25:
        pts += 6
        notes.append(f"volume beli {ud:.2f}x volume jual di dalam range")
    elif ud >= 1.05:
        pts += 4
        notes.append(f"volume beli sedikit unggul ({ud:.2f}x)")
    elif ud >= 0.9:
        pts += 2
        notes.append(f"volume beli-jual seimbang ({ud:.2f}x)")
    else:
        notes.append(f"volume jual dominan ({ud:.2f}x)")

    return {"points": min(pts, 20), "max": 20, "veto": False, "notes": notes,
            "obv_slope": round(obv_s, 2), "adl_slope": round(adl_s, 2),
            "up_down_vol": round(ud, 2)}


def score_relative_strength(df: pd.DataFrame, btc: pd.DataFrame, rng: dict | None) -> dict:
    """Relative strength vs BTC — max 15."""
    j = df[["close"]].join(btc[["close"]], how="inner", rsuffix="_btc").dropna()
    if len(j) < 60:
        return {"points": 0, "max": 15, "veto": False,
                "notes": ["data tidak cukup untuk hitung RS"], "rs_slope": None}

    rs = j["close"] / j["close_btc"]
    lb = min(rng["bars"], 120) if rng else 60
    rs_s = ta.slope_pct(rs, lb)

    half = min(lb, len(rs)) // 2
    rs_hl = bool(len(rs) >= lb and rs.tail(lb).iloc[half:].min() > rs.tail(lb).iloc[:half].min())

    def perf(series, n):
        if len(series) <= n:
            return 0.0
        return float((series.iloc[-1] / series.iloc[-n - 1] - 1) * 100)

    p30 = perf(j["close"], 30) - perf(j["close_btc"], 30)
    p90 = perf(j["close"], 90) - perf(j["close_btc"], 90)

    pts, notes = 0, []
    if rs_s > 0.15:
        pts += 7
        notes.append(f"RS vs BTC menguat ({rs_s:+.2f}/bar)")
    elif rs_s > -0.05:
        pts += 5
        notes.append(f"RS vs BTC stabil ({rs_s:+.2f}/bar) — tidak tertinggal")
    else:
        pts += 1
        notes.append(f"RS vs BTC melemah ({rs_s:+.2f}/bar)")

    if rs_hl:
        pts += 4
        notes.append("RS membentuk higher low — mulai memimpin")
    if p30 > 0:
        pts += 2
        notes.append(f"unggul {p30:+.1f}% vs BTC (30 hari)")
    if p90 > 0:
        pts += 2
        notes.append(f"unggul {p90:+.1f}% vs BTC (90 hari)")

    return {"points": min(pts, 15), "max": 15, "veto": False, "notes": notes,
            "rs_slope": round(rs_s, 3), "rs_higher_low": rs_hl,
            "vs_btc_30d": round(p30, 1), "vs_btc_90d": round(p90, 1)}


# ─────────────────────────────────────────────────────────────
# Rencana trade untuk setup akumulasi
# ─────────────────────────────────────────────────────────────

def gem_trade_plan(df, rng, wy, cfg, size_mult=1.0):
    """Entry/SL/TP untuk setup basis. Trigger = pivot breakout, bukan harga sekarang."""
    price = float(df["close"].iloc[-1])
    a = float(ta.atr(df).iloc[-1])
    ev = wy["events"]
    top, bot, mid = rng["top"], rng["bottom"], rng["mid"]

    # Pivot: high kontraksi terakhir (VCP) atau atap range
    recent_high = float(df["high"].iloc[-20:].max())
    pivot = min(top, recent_high) if recent_high > mid else top

    phase = wy["phase"]
    lps_low = float(df["low"].iloc[ev["LPS"]["idx"]]) if "LPS" in ev else None
    low10 = float(df["low"].iloc[-10:].min())

    if phase == "D" and price > mid:
        # Wyckoff: setelah SOS, entry ideal BUKAN market chase, melainkan back-up / LPS.
        backup = (lps_low * 1.02) if lps_low else max(mid, low10 * 1.03)
        if backup >= price * 0.995 or price <= low10 * 1.06:
            entry, style = price, "sekarang (Phase D, harga belum melar dari support)"
        else:
            entry, style = backup, f"limit di zona back-up/LPS (jangan kejar bar SOS)"
    elif "SPRING" in ev and ev["SPRING"]["bars_ago"] <= 10:
        entry, style = price, "sekarang (spring baru terjadi, entry parsial)"
    else:
        entry, style = pivot * 1.005, f"breakout di atas {pivot:.6g} + konfirmasi volume"

    # Stop mengikuti GAYA ENTRY:
    #   entry setelah spring  -> di bawah low spring (invalidasi Wyckoff)
    #   entry di breakout     -> di bawah low kontraksi terakhir (aturan VCP),
    #                            supaya risiko tetap kecil relatif terhadap target
    recent_low20 = float(df["low"].iloc[-20:].min())
    recent_low10 = float(df["low"].iloc[-10:].min())

    if phase == "D" and price > mid:
        # Setelah SOS, invalidasi adalah LPS/support terakhir — bukan low spring.
        # Menaruh stop sejauh spring membuat risiko besar tanpa alasan.
        cands = [x for x in (lps_low, recent_low10) if x and x < entry * 0.99]
        sl = (max(cands) if cands else recent_low20) * 0.97
    elif "SPRING" in ev and ev["SPRING"]["bars_ago"] <= 10:
        sl = ev["SPRING"]["low"] * 0.97          # invalidasi Wyckoff klasik
    else:
        sl = max(recent_low20 * 0.97, mid * 0.90)
        if sl >= entry * 0.995:
            sl = min(recent_low20 * 0.97, entry * 0.93)
    if sl <= 0 or sl >= entry * 0.995 or (entry - sl) / entry > 0.22:
        sl = entry - 2.5 * a

    height = top - bot
    tp1 = top + height * 0.5          # proyeksi tinggi range
    tp2 = top + height * 1.0
    if tp1 <= entry * 1.03:
        tp1 = entry + 2.5 * (entry - sl)
        tp2 = entry + 5.0 * (entry - sl)

    risk_unit = entry - sl
    rr1 = (tp1 - entry) / risk_unit if risk_unit > 0 else 0
    rr2 = (tp2 - entry) / risk_unit if risk_unit > 0 else 0
    risk_amount = cfg["capital"] * (cfg["risk_pct"] / 100) * size_mult
    sl_dist = risk_unit / entry if entry else 0
    size = risk_amount / sl_dist if sl_dist > 0 else 0

    return {"entry": entry, "entry_style": style, "sl": sl, "tp1": tp1, "tp2": tp2,
            "pivot": pivot,
            "sl_pct": round(-sl_dist * 100, 2),
            "tp1_pct": round((tp1 - entry) / entry * 100, 2),
            "tp2_pct": round((tp2 - entry) / entry * 100, 2),
            "rr1": round(rr1, 2), "rr2": round(rr2, 2),
            "risk_amount": round(risk_amount, 2), "position_size": round(size, 2)}


# ─────────────────────────────────────────────────────────────
# Evaluasi utama
# ─────────────────────────────────────────────────────────────

PHASE_LABEL = {
    "A": "Phase A — penurunan baru berhenti, terlalu dini",
    "B": "Phase B — membangun sebab, masih butuh waktu",
    "C": "Phase C — uji pasokan terakhir (spring), zona entry konviksi tertinggi",
    "D": "Phase D — kekuatan muncul, markup mendekat",
    "?": "belum terklasifikasi",
    "distribusi": "DISTRIBUSI — hindari",
}

PHASE_READY = {"C": "SIAP", "D": "SIAP", "B": "PANTAU", "A": "TERLALU DINI", "?": "PANTAU"}


def evaluate_gem(symbol: str, df: pd.DataFrame, btc: pd.DataFrame,
                 regime: dict, cfg: dict) -> dict | None:
    if len(df) < cfg.get("gem_min_bars", 220):
        return None

    rng = detect_trading_range(df, cfg.get("max_range_width_pct", 45.0))
    base = score_base(df, rng)
    contr = score_contraction(df, rng)
    wy = score_wyckoff(df, rng)
    sm = score_smart_money(df, rng)
    rs = score_relative_strength(df, btc, rng)

    total = base["points"] + contr["points"] + wy["points"] + sm["points"] + rs["points"]

    vetoes = []
    for name, comp in [("Basis", base), ("Wyckoff", wy)]:
        if comp["veto"]:
            vetoes.append(f"{name}: {comp['notes'][0]}")

    price = float(df["close"].iloc[-1])
    # Sudah terlanjur pump = bukan lagi hidden gem
    if len(df) > 30:
        run30 = (price / float(df["close"].iloc[-31]) - 1) * 100
        if run30 > cfg.get("max_run_30d_pct", 60):
            vetoes.append(f"sudah naik {run30:.0f}% dalam 30 hari — bukan lagi fase akumulasi")
    else:
        run30 = 0.0

    if regime["status"] == "MERAH" and cfg.get("gem_respect_btc", True):
        vetoes.append("BTC status MERAH — tunda entry, tetap pantau basisnya")

    plan = gem_trade_plan(df, rng, wy, cfg, regime["size_mult"] or 0.5) if rng else None
    if plan and plan["rr1"] < cfg.get("gem_min_rr", 2.5):
        vetoes.append(f"R:R ke TP1 hanya 1:{plan['rr1']}")

    dd = None
    if len(df) >= 365:
        ath = float(df["high"].iloc[-365:].max())
        dd = round((price / ath - 1) * 100, 1)

    return {
        "symbol": symbol, "price": price, "total": total,
        "grade": ("A+" if total >= 80 else "A" if total >= 70 else
                  "B" if total >= 60 else "C"),
        "phase": wy["phase"], "phase_label": PHASE_LABEL.get(wy["phase"], "?"),
        "readiness": PHASE_READY.get(wy["phase"], "PANTAU"),
        "vetoed": len(vetoes) > 0, "veto_reasons": vetoes,
        "s_base": base["points"], "s_contraction": contr["points"],
        "s_wyckoff": wy["points"], "s_smartmoney": sm["points"], "s_rs": rs["points"],
        "base_bars": rng["bars"] if rng else None,
        "range_top": rng["top"] if rng else None,
        "range_bottom": rng["bottom"] if rng else None,
        "range_width_pct": round(rng["width_pct"], 1) if rng else None,
        "pos_in_range": base.get("pos_in_range"),
        "bw_pctile": contr["bw_pctile"], "atr_pctile": contr["atr_pctile"],
        "contractions": contr["legs"],
        "obv_slope": sm["obv_slope"], "up_down_vol": sm["up_down_vol"],
        "rs_slope": rs["rs_slope"], "vs_btc_30d": rs.get("vs_btc_30d"),
        "vs_btc_90d": rs.get("vs_btc_90d"),
        "run_30d": round(run30, 1), "drawdown_1y": dd,
        "events": list(wy["events"].keys()),
        "plan": plan,
        "notes": {"basis": base["notes"], "kontraksi": contr["notes"],
                  "wyckoff": wy["notes"], "smartmoney": sm["notes"], "rs": rs["notes"]},
    }
