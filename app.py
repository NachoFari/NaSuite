# -*- coding: utf-8 -*-
"""
NaSuite Launcher — Hub de Aplicaciones del Ecosistema NaSuite.
Autor: NachoFari (https://github.com/NachoFari)
"""

from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Optional

if getattr(sys, "frozen", False):
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = RESOURCE_DIR

if RESOURCE_DIR not in sys.path:
    sys.path.insert(0, RESOURCE_DIR)

from backend import APP_NAME, LAUNCHER_VERSION
from backend import autostart
from backend.api_router import ApiRouter
from backend.tray import SystemTray


def find_available_port(start_port: int = 5100, max_attempts: int = 50) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port


class NaSuiteHandler(http.server.BaseHTTPRequestHandler):
    router: ApiRouter
    resource_dir: str

    def log_message(self, format: str, *args) -> None:
        return

    def _send_bytes(
        self,
        status: int,
        content_type: str,
        data: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, val in extra_headers.items():
                self.send_header(key, val)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path
        query = {
            k: v[0]
            for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()
        }

        if req_path.startswith("/api/"):
            status, ctype, data = self.router.handle_get(req_path, query)
            self._send_bytes(status, ctype, data)
            return

        if req_path in ("/", "/index.html"):
            index_file = os.path.join(self.resource_dir, "templates", "index.html")
            if os.path.isfile(index_file):
                with open(index_file, "rb") as fh:
                    self._send_bytes(200, "text/html; charset=utf-8", fh.read())
                return

        if req_path == "/favicon.ico":
            ico_file = os.path.join(self.resource_dir, "nasuite.ico")
            if os.path.isfile(ico_file):
                with open(ico_file, "rb") as fh:
                    self._send_bytes(200, "image/x-icon", fh.read())
                return

        if req_path.startswith("/static/"):
            rel = urllib.parse.unquote(req_path[len("/static/"):]).lstrip("/\\")
            static_root = os.path.abspath(os.path.join(self.resource_dir, "static"))
            target = os.path.abspath(os.path.join(static_root, rel))
            try:
                in_static = os.path.commonpath([static_root, target]) == static_root
            except ValueError:
                in_static = False
            if in_static and os.path.isfile(target):
                ctype, _ = mimetypes.guess_type(target)
                with open(target, "rb") as fh:
                    self._send_bytes(200, ctype or "application/octet-stream", fh.read())
                return

        self._send_bytes(404, "text/plain; charset=utf-8", b"Not Found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""

        if req_path.startswith("/api/"):
            status, ctype, data = self.router.handle_post(req_path, body)
            self._send_bytes(status, ctype, data)
            return

        self._send_bytes(404, "application/json; charset=utf-8", b'{"ok":false,"error":"Not Found"}')


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{LAUNCHER_VERSION}")
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Ejecuta unicamente el servidor HTTP local sin abrir ventana nativa.",
    )
    parser.add_argument(
        "--minimized",
        "--tray",
        dest="minimized",
        action="store_true",
        help="Inicia NaSuite Launcher oculto en la bandeja del sistema.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Puerto TCP deseado (si se omite, busca un puerto libre desde 5100).",
    )
    return parser.parse_args(argv)


_SINGLE_INSTANCE_MUTEX: Any = None


def _get_instance_lock_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    lock_dir = os.path.join(base, "NaSuite")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, "launcher_instance.json")


def _write_instance_lock(port: int) -> None:
    try:
        with open(_get_instance_lock_path(), "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "port": port}, fh)
    except Exception:
        pass


def _wake_existing_instance(show_window: bool = True) -> bool:
    """Ask an already-running NaSuite Launcher instance to restore its window."""
    candidate_ports: list[int] = []
    lock_path = _get_instance_lock_path()
    if os.path.isfile(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            p = int(data.get("port") or 0)
            if p > 0:
                candidate_ports.append(p)
        except Exception:
            pass
    for p in range(5100, 5110):
        if p not in candidate_ports:
            candidate_ports.append(p)

    woke = False
    for port in candidate_ports:
        try:
            endpoint = "/api/window/show" if show_window else "/api/health"
            method = "POST" if show_window else "GET"
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{endpoint}",
                data=b"{}" if method == "POST" else None,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200 and "NaSuite" in raw:
                    woke = True
                    break
        except Exception:
            continue

    if show_window and sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "NaSuite — Hub de Aplicaciones")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                woke = True
        except Exception:
            pass
    return woke


def _acquire_single_instance_mutex() -> bool:
    """Return True if this process is the primary single instance, False if another instance holds the mutex."""
    global _SINGLE_INSTANCE_MUTEX
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "Local\\NachoFari_NaSuite_Launcher_SingleInstance")
        err = kernel32.GetLastError()
        _SINGLE_INSTANCE_MUTEX = mutex
        if err == 183:  # ERROR_ALREADY_EXISTS
            return False
        return True
    except Exception:
        return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Garantizar instancia unica: si ya hay un NaSuite activo, despertarlo y salir.
    if not _acquire_single_instance_mutex() or _wake_existing_instance(show_window=not args.minimized):
        _wake_existing_instance(show_window=not args.minimized)
        print(f"[{APP_NAME}] Ya existe una instancia en ejecucion. Ventana activada.")
        return 0

    port = args.port if args.port > 0 else find_available_port(5100, max_attempts=50)
    _write_instance_lock(port)

    router = ApiRouter(APP_DIR, RESOURCE_DIR)
    NaSuiteHandler.router = router
    NaSuiteHandler.resource_dir = RESOURCE_DIR

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), NaSuiteHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}"
    print("==================================================")
    print(f"   [{APP_NAME} v{LAUNCHER_VERSION}] Modo Nativo")
    print("==================================================")
    print(f"Servidor local activo en {url}")

    if args.server_only:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            router.shutdown()
            httpd.shutdown()
        return 0

    # Sync Windows autostart registry entry with saved configuration & set explicit AppUserModelID for taskbar icon
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NachoFari.NaSuite.Launcher.1")
        except Exception:
            pass
        cfg_boot = router.config_mgr.get()
        want_autostart = bool(cfg_boot.get("start_with_windows", True))
        ok_as, msg_as = autostart.set_autostart(want_autostart)
        router.logs.add("info" if ok_as else "warn", "Sistema", msg_as)

    state: dict[str, Any] = {"window": None, "force_quit": False}
    tray: Optional[SystemTray] = None

    def hide_window() -> None:
        win = state.get("window")
        if win is not None:
            try:
                win.hide()
            except Exception:
                pass

    def restore_window() -> None:
        win = state.get("window")
        if win is not None:
            try:
                win.show()
                win.restore()
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    hwnd = user32.FindWindowW(None, "NaSuite — Hub de Aplicaciones")
                    if hwnd:
                        user32.ShowWindow(hwnd, 9)
                        user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
        else:
            webbrowser.open(url)

    def tray_sync() -> None:
        threading.Thread(target=router.trigger_sync, daemon=True).start()

    def tray_quit() -> None:
        state["force_quit"] = True
        if tray is not None:
            tray.stop()
        win = state.get("window")
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        router.shutdown()
        try:
            httpd.shutdown()
        except Exception:
            pass
        os._exit(0)

    ico_path = os.path.join(RESOURCE_DIR, "nasuite.ico")
    tray = SystemTray(
        icon_path=ico_path,
        tooltip=f"{APP_NAME} v{LAUNCHER_VERSION}",
        on_restore=restore_window,
        on_sync=tray_sync,
        on_quit=tray_quit,
    )
    tray.start()
    router.set_window_callbacks(on_hide=hide_window, on_show=restore_window, on_balloon=tray.show_balloon)

    try:
        import webview

        window = webview.create_window(
            title="NaSuite — Hub de Aplicaciones",
            url=url,
            width=1320,
            height=840,
            min_size=(980, 640),
            background_color="#0E1015",
            hidden=bool(args.minimized),
        )
        state["window"] = window

        def on_closing() -> bool:
            if state["force_quit"]:
                return True
            cfg = router.config_mgr.get()
            if cfg.get("minimize_to_tray_on_close", True) and tray is not None and tray.is_active:
                try:
                    window.hide()
                except Exception:
                    pass
                return False
            return True

        window.events.closing += on_closing

        def apply_win32_window_icon() -> None:
            if sys.platform != "win32" or not os.path.isfile(ico_path):
                return
            try:
                import ctypes
                user32 = ctypes.windll.user32
                IMAGE_ICON = 1
                LR_LOADFROMFILE = 0x0010
                WM_SETICON = 0x0080
                ICON_SMALL = 0
                ICON_BIG = 1
                GCLP_HICON = -14
                GCLP_HICONSM = -34

                hicon_big = user32.LoadImageW(None, ico_path, IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
                hicon_sm = user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                for _ in range(25):
                    hwnd = user32.FindWindowW(None, "NaSuite — Hub de Aplicaciones")
                    if hwnd:
                        if hicon_big:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                            try:
                                user32.SetClassLongPtrW(hwnd, GCLP_HICON, hicon_big)
                            except Exception:
                                pass
                        if hicon_sm:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_sm)
                            try:
                                user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, hicon_sm)
                            except Exception:
                                pass
                        break
                    time.sleep(0.15)
            except Exception:
                pass

        def on_shown() -> None:
            threading.Thread(target=apply_win32_window_icon, daemon=True).start()

        window.events.shown += on_shown

        gui_backend = "edgechromium" if sys.platform == "win32" else None
        try:
            webview.start(gui=gui_backend, debug=False, icon=ico_path if os.path.isfile(ico_path) else None)
        except TypeError:
            webview.start(gui=gui_backend, debug=False)
    except Exception as exc:
        print(f"[NaSuite] Ventana nativa no disponible ({exc}), abriendo navegador...")
        if not args.minimized:
            webbrowser.open(url)
        try:
            while not state["force_quit"]:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        if tray is not None:
            tray.stop()
        router.shutdown()
        try:
            httpd.shutdown()
        except Exception:
            pass
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
