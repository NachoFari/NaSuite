# -*- coding: utf-8 -*-
"""Pure Win32 ctypes System Tray icon with native popup menu and balloon notifications."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes
from typing import Callable, Optional

# Win32 constants
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800

TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

WS_OVERLAPPED = 0x00000000

ID_MENU_OPEN = 1001
ID_MENU_SYNC = 1002
ID_MENU_QUIT = 1003

# 64-bit safe Win32 scalar/pointer types
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
HWND = ctypes.c_void_p
HICON = ctypes.c_void_p
HCURSOR = ctypes.c_void_p
HBRUSH = ctypes.c_void_p
HMENU = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p

if sys.platform == "win32":
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, wintypes.UINT, WPARAM, LPARAM)

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", HINSTANCE),
            ("hIcon", HICON),
            ("hCursor", HCURSOR),
            ("hbrBackground", HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", HICON),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", HICON),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [
            ("x", wintypes.LONG),
            ("y", wintypes.LONG),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", HWND),
            ("message", wintypes.UINT),
            ("wParam", WPARAM),
            ("lParam", LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = HINSTANCE

    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = wintypes.ATOM

    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL

    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT

    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        HWND,
        HMENU,
        HINSTANCE,
        ctypes.c_void_p,
    ]
    _user32.CreateWindowExW.restype = HWND

    _user32.DestroyWindow.argtypes = [HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL

    _user32.DefWindowProcW.argtypes = [HWND, wintypes.UINT, WPARAM, LPARAM]
    _user32.DefWindowProcW.restype = LRESULT

    _user32.PostMessageW.argtypes = [HWND, wintypes.UINT, WPARAM, LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL

    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None

    _user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = wintypes.BOOL

    _user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL

    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    _user32.DispatchMessageW.restype = LRESULT

    _user32.LoadImageW.argtypes = [
        HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.LoadImageW.restype = HICON

    _user32.LoadIconW.argtypes = [HINSTANCE, ctypes.c_void_p]
    _user32.LoadIconW.restype = HICON

    _user32.DestroyIcon.argtypes = [HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL

    _user32.CreatePopupMenu.argtypes = []
    _user32.CreatePopupMenu.restype = HMENU

    _user32.AppendMenuW.argtypes = [HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    _user32.AppendMenuW.restype = wintypes.BOOL

    _user32.TrackPopupMenu.argtypes = [
        HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        HWND,
        ctypes.c_void_p,
    ]
    _user32.TrackPopupMenu.restype = wintypes.BOOL

    _user32.DestroyMenu.argtypes = [HMENU]
    _user32.DestroyMenu.restype = wintypes.BOOL

    _user32.SetForegroundWindow.argtypes = [HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL

    _user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL

    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class SystemTray:
    """Native Windows System Tray icon running a hidden Win32 message loop in a daemon thread."""

    def __init__(
        self,
        icon_path: str = "",
        tooltip: str = "NaSuite Launcher",
        on_restore: Optional[Callable[[], None]] = None,
        on_sync: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        self.icon_path = icon_path
        self.tooltip = (tooltip or "NaSuite Launcher")[:127]
        self._on_restore = on_restore
        self._on_sync = on_sync
        self._on_quit = on_quit

        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._hicon: Optional[int] = None
        self._owns_icon: bool = False
        self._hinstance: Optional[int] = None
        self._class_name: str = f"NaSuiteTrayWindow_{id(self)}"
        self._wndproc_ref = None
        self._taskbar_created_msg: int = 0
        self._active: bool = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._active
            self._ready_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="NaSuiteTray", daemon=True)
            self._thread.start()
        self._ready_event.wait(timeout=2.0)
        return self.is_active

    def stop(self) -> None:
        if sys.platform != "win32":
            return
        with self._lock:
            hwnd = self._hwnd
            self._active = False
        if hwnd:
            try:
                self._remove_icon(hwnd)
            except Exception:
                pass
            try:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def show_balloon(self, title: str, message: str) -> bool:
        """Display a native Windows tray balloon notification."""
        if sys.platform != "win32":
            return False
        with self._lock:
            hwnd = self._hwnd
            hicon = self._hicon
            active = self._active
        if not active or not hwnd:
            return False

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_INFO | NIF_TIP
        nid.hIcon = hicon
        nid.szTip = self.tooltip
        nid.szInfoTitle = str(title or "NaSuite")[:63]
        nid.szInfo = str(message or "")[:255]
        nid.dwInfoFlags = NIIF_INFO
        try:
            return bool(_shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        except Exception:
            return False

    def _invoke_async(self, callback: Optional[Callable[[], None]]) -> None:
        if not callback:
            return

        def runner() -> None:
            try:
                callback()
            except Exception:
                pass

        threading.Thread(target=runner, daemon=True).start()

    def _load_icon(self) -> tuple[Optional[int], bool]:
        if self.icon_path and os.path.isfile(self.icon_path):
            hicon = _user32.LoadImageW(
                None,
                self.icon_path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if hicon:
                return hicon, True
        fallback = _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        return fallback, False

    def _add_icon(self, hwnd: int, hicon: Optional[int]) -> bool:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = self.tooltip
        return bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove_icon(self, hwnd: int) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _show_context_menu(self, hwnd: int) -> None:
        hmenu = _user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            _user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_OPEN, "Abrir NaSuite")
            _user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_SYNC, "Sincronizar repos ahora")
            _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_QUIT, "Salir completamente")

            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            _user32.SetForegroundWindow(hwnd)

            cmd = _user32.TrackPopupMenu(
                hmenu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x,
                pt.y,
                0,
                hwnd,
                None,
            )
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)

            if cmd == ID_MENU_OPEN:
                self._invoke_async(self._on_restore)
            elif cmd == ID_MENU_SYNC:
                self._invoke_async(self._on_sync)
            elif cmd == ID_MENU_QUIT:
                self._invoke_async(self._on_quit)
        finally:
            _user32.DestroyMenu(hmenu)

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_TRAYICON:
            event_code = lparam & 0xFFFF
            if event_code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._invoke_async(self._on_restore)
            elif event_code == WM_RBUTTONUP:
                self._show_context_menu(hwnd)
            return 0

        if self._taskbar_created_msg and msg == self._taskbar_created_msg:
            if self._hicon:
                self._add_icon(hwnd, self._hicon)
            return 0

        if msg == WM_CLOSE:
            self._remove_icon(hwnd)
            _user32.DestroyWindow(hwnd)
            return 0

        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0

        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run_loop(self) -> None:
        hinstance = _kernel32.GetModuleHandleW(None)
        self._hinstance = hinstance
        self._taskbar_created_msg = _user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc_ref = WNDPROC(self._wnd_proc)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name

        atom = _user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            self._ready_event.set()
            return

        hwnd = _user32.CreateWindowExW(
            0,
            self._class_name,
            "NaSuite Tray",
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            _user32.UnregisterClassW(self._class_name, hinstance)
            self._ready_event.set()
            return

        hicon, owns_icon = self._load_icon()
        added = self._add_icon(hwnd, hicon)

        with self._lock:
            self._hwnd = hwnd
            self._hicon = hicon
            self._owns_icon = owns_icon
            self._active = added
        self._ready_event.set()

        if not added:
            _user32.DestroyWindow(hwnd)
            if owns_icon and hicon:
                _user32.DestroyIcon(hicon)
            _user32.UnregisterClassW(self._class_name, hinstance)
            return

        msg = MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        with self._lock:
            self._active = False
            self._hwnd = None

        if owns_icon and hicon:
            _user32.DestroyIcon(hicon)
        _user32.UnregisterClassW(self._class_name, hinstance)
