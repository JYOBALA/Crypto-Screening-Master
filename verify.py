#!/usr/bin/env python3
"""
verify.py — Gerbang mutu. Jalankan setelah SETIAP perubahan kode.
Claude Code wajib menjalankan ini sebelum menyatakan sebuah perubahan selesai.

    python verify.py
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):          # konsisten UTF-8 juga di Windows
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

MODULES = ["indicators.py", "scoring.py", "accumulation.py",
           "screener.py", "inspect_symbol.py", "diagnose.py", "backtest.py"]

def main() -> int:
    root = Path(__file__).parent
    fail = []

    print("1/4  Cek sintaks ...")
    for m in MODULES:
        p = root / m
        if not p.exists():
            fail.append(f"{m} HILANG")
            print(f"     x {m} tidak ditemukan")
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            print(f"     v {m}")
        except SyntaxError as e:
            fail.append(f"{m}: {e}")
            print(f"     x {m} baris {e.lineno}: {e.msg}")

    print("2/4  Cek impor ...")
    r = subprocess.run([sys.executable, "-c",
                        "import screener, scoring, indicators, accumulation"],
                       cwd=root, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    if r.returncode:
        fail.append("impor gagal")
        print("     x", r.stderr.strip().splitlines()[-1] if r.stderr else "gagal")
    else:
        print("     v semua modul termuat")

    print("3/4  Cek ketahanan output di console Windows non-UTF8 ...")
    env_cp = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    rc = subprocess.run([sys.executable, "screener.py", "--selftest"],
                        cwd=root, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env_cp)
    if "UnicodeEncodeError" in (rc.stderr or "") + (rc.stdout or ""):
        fail.append("output rusak di console cp1252 - _force_utf8() hilang?")
        print("     x UnicodeEncodeError - periksa _force_utf8() di entry script")
    else:
        print("     v aman di console cp1252")

    print("4/4  Selftest ...")
    r = subprocess.run([sys.executable, "screener.py", "--selftest"],
                       cwd=root, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    print("\n".join("     " + l for l in r.stdout.strip().splitlines()[-8:]))
    if r.returncode or "SEMUA LULUS" not in r.stdout:
        fail.append("selftest gagal")

    print()
    if fail:
        print("GAGAL:")
        for f in fail:
            print("  -", f)
        print("\nJANGAN commit. Perbaiki dulu, lalu jalankan verify.py lagi.")
        return 1
    print("SEMUA VERIFIKASI LULUS - aman untuk commit.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
