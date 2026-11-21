# -*- coding: utf-8 -*-
"""
Script oficial de compilacion de NaSuite Launcher.
Genera:
  - dist/NaSuite/NaSuite.exe (Ejecutable portable onedir)
  - dist_installer/NaSuite-Setup-v<ver>.exe (Instalador Inno Setup 6)
  - dist_installer/NaSuite-v<ver>-Windows-Portable.zip (Paquete portable)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend import LAUNCHER_VERSION


def find_pyinstaller() -> str:
    candidates = [
        os.path.join(ROOT, ".venv", "Scripts", "pyinstaller.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python312", "Scripts", "pyinstaller.exe"),
        shutil.which("pyinstaller") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return "pyinstaller"


def find_iscc() -> str:
    candidates = [
        shutil.which("ISCC.exe") or "",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def main() -> int:
    os.chdir(ROOT)
    pyinstaller = find_pyinstaller()

    print(f"=== 1/3 Compilando NaSuite Launcher v{LAUNCHER_VERSION} con PyInstaller ===")
    spec_path = os.path.join(ROOT, "NaSuite.spec")
    cmd = [pyinstaller, "--noconfirm", spec_path]

    if subprocess.call(cmd) != 0:
        print("[ERROR] Fallo PyInstaller.")
        return 1

    dist_dir = os.path.join(ROOT, "dist", "NaSuite")
    if not os.path.isdir(dist_dir):
        print(f"[ERROR] No se genero el directorio esperado: {dist_dir}")
        return 1

    # Also copy NaSuite.exe note/shortcut convenience
    os.makedirs(os.path.join(ROOT, "dist_installer"), exist_ok=True)

    iscc = find_iscc()
    iss_path = os.path.join(ROOT, "installer.iss")
    if iscc and os.path.isfile(iss_path):
        print(f"=== 2/3 Generando instalador Windows con Inno Setup 6 ({iscc}) ===")
        if subprocess.call([iscc, iss_path]) != 0:
            print("[WARN] Inno Setup devolvio un codigo distinto de cero.")
    else:
        print("[WARN] ISCC.exe (Inno Setup 6) no encontrado; omitiendo instalador .exe.")

    zip_path = os.path.join(
        ROOT,
        "dist_installer",
        f"NaSuite-v{LAUNCHER_VERSION}-Windows-Portable.zip",
    )
    print(f"=== 3/3 Empaquetando {os.path.basename(zip_path)} ===")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, _, files in os.walk(dist_dir):
            for fn in files:
                full = os.path.join(folder, fn)
                rel = os.path.relpath(full, dist_dir)
                zf.write(full, rel)

    print("[OK] Build completado en dist/NaSuite y dist_installer/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
