# -*- coding: utf-8 -*-
"""Windows startup (autostart) registry manager for NaSuite Launcher."""

from __future__ import annotations

import os
import sys
from typing import Tuple

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "NaSuiteLauncher"


def get_autostart_command() -> str:
    """Build the command line registered in HKCU Run to start NaSuite silently in tray."""
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return f'"{exe_path}" --minimized'

    py_dir = os.path.dirname(os.path.abspath(sys.executable))
    pythonw = os.path.join(py_dir, "pythonw.exe")
    py_exe = pythonw if os.path.isfile(pythonw) else os.path.abspath(sys.executable)
    app_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app.py",
    )
    return f'"{py_exe}" "{app_py}" --minimized'


def is_autostart_enabled() -> bool:
    """Return True if NaSuiteLauncher is registered in HKCU Run."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(str(value or "").strip())
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled: bool) -> Tuple[bool, str]:
    """Enable or disable NaSuite Launcher autostart for the current Windows user."""
    if sys.platform != "win32":
        return False, "Inicio automatico disponible solo en Windows."

    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                cmd = get_autostart_command()
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
                return True, "Inicio automatico con Windows activado."
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
            return True, "Inicio automatico con Windows desactivado."
    except OSError as exc:
        return False, f"No se pudo actualizar el registro de Windows: {exc}"
